import asyncio
from datetime import datetime, timezone
import hashlib

import pytest

from src.curator.matcher import CuratedTopic
from src.editorial.models import SourceRecord
from src.writer.deep_researcher import DeepResearcher


def record(url, text, links=(), kind='html'):
    return SourceRecord(url=url, title='Research paper', fetched_at=datetime.now(timezone.utc),
        sha256=hashlib.sha256(text.encode()).hexdigest(), text=text, locations=('page:1',),
        kind=kind, role='primary', links=links)


def topic(url):
    return CuratedTopic(rank=1, title='paper', url=url, source='HF', one_line_summary='',
                        relevance_reason='', suggested_angle='How does the method work?')


def test_follows_only_inspected_primary_links_and_bounds_cycles(tmp_path):
    hf='https://huggingface.co/papers/2610.10164'
    pdf='https://arxiv.org/pdf/2610.10164v1'
    repo='https://github.com/LimOkii/UniSKill'
    sources={hf:record(hf,'Landing', (pdf,)), pdf:record(pdf,'Method evidence', (hf,repo), 'pdf'),
             repo:record(repo,'Code will be released.')}
    calls=[]
    def fetch(url, **kwargs):
        calls.append(url)
        return sources[url]
    result=asyncio.run(DeepResearcher(tmp_path, fetcher=fetch, max_sources=3).research(topic(hf)))
    assert calls == [hf,pdf,repo]
    assert len(result['packet'].sources)==3
    assert result['status']=='RESEARCH_READY'


def test_budget_reports_unfollowed_links(tmp_path):
    url='https://huggingface.co/papers/2610.10164'
    source=record(url,'Landing', ('https://arxiv.org/pdf/2610.10164v1',))
    result=asyncio.run(DeepResearcher(tmp_path, fetcher=lambda *a,**k:source, max_sources=1).research(topic(url)))
    assert result['status']=='NEEDS_RESEARCH'
    assert 'source_follow_budget_exhausted' in result['reasons']


def test_located_verification_rejects_invented_text_and_cross_study():
    from src.research.extract import verify_located_excerpt
    source=record('https://arxiv.org/pdf/1234.56789',
        '4.1 STUDY A\nConditions: CPU A\nSuccess rate 98.4%.\n4.2 STUDY B\nConditions: GPU B\nSuccess rate 84.7%.', kind='pdf')
    assert verify_located_excerpt(source,'page:1','Success rate 98.4%.')
    assert not verify_located_excerpt(source,'page:1','Success rate 99.4%.')
    from src.research.extract import verify_metric_context
    assert verify_metric_context(source,'page:1','Success rate 98.4%.',
        baseline='CPU A', conditions='CPU A', target='Success rate', value=98.4, unit='%')
    assert not verify_metric_context(source,'page:1','Success rate 98.4%.',
        baseline='GPU B', conditions='GPU B', target='Success rate', value=98.4, unit='%')


def test_pdf_sections_keep_experiment_and_appendix_separate():
    from src.research.extract import extract_sections, metric_scope
    source=record('https://arxiv.org/pdf/1234.56789',
        '4 EXPERIMENTS\n4.1 EXPERIMENTALSETUP\nCPU conditions.\n4.2 RESULTS\nSuccess rate 98.4%.\nA APPENDIX\nPrompt: imagined GPU.',kind='pdf')
    sections=extract_sections(source)
    assert any('EXPERIMENTALSETUP' in s.title for s in sections)
    setup=next(s for s in sections if 'CPU conditions' in s.text)
    result=next(s for s in sections if '98.4%' in s.text)
    appendix=next(s for s in sections if 'imagined GPU' in s.text)
    assert metric_scope(setup)==metric_scope(result)
    assert metric_scope(appendix)!=metric_scope(result)


def test_model_selection_splits_results_and_preserves_full_snapshot(tmp_path):
    import json
    source=record('https://arxiv.org/pdf/1234.56789',
        '4 EXPERIMENTS\n4.1 EXPERIMENTALSETUP\nBaseline: GRPO. Conditions: ALFWorld CPU.\n'
        '4.2 RESULTS\nSuccess rate 98.4% and success rate 84.7%.\nA APPENDIX\nUnrelated prompt.',kind='pdf')
    proposals=[{'url':source.url,'location':'page:1','text':text,'metric_context':
        {'value':value,'unit':'%','target':target,'baseline':'GRPO','conditions':'ALFWorld CPU'}}
        for text,value,target in [('Success rate 98.4%',98.4,'Success rate'),('success rate 84.7%',84.7,'success rate')]]
    class Model:
        def generate(self, stage, prompt, **kwargs):
            assert stage=='research'
            return json.dumps({'claims':proposals,'gaps':[]})
    result=asyncio.run(DeepResearcher(tmp_path,fetcher=lambda *a,**k:source,model=Model()).research(topic(source.url)))
    assert result['status']=='RESEARCH_READY'
    assert len(result['packet'].claims)==2
    assert [c.metric_context.value for c in result['packet'].claims]==[98.4,84.7]
    assert result['packet'].sources[0].text==source.text
    assert 'evidence_selection_omits_unselected_spans' in result['packet'].gaps


@pytest.mark.parametrize('change', ['text','conditions'])
def test_invalid_model_proposal_blocks(tmp_path,change):
    import json
    source=record('https://arxiv.org/pdf/1234.56789','Success rate 98.4%. Baseline GRPO. Conditions CPU.',kind='pdf')
    proposal={'url':source.url,'location':'page:1','text':'Success rate 98.4%',
        'metric_context':{'value':98.4,'unit':'%','target':'Success rate','baseline':'GRPO','conditions':'CPU'}}
    if change=='text': proposal['text']='Success rate 99.4%'
    else: proposal['metric_context']['conditions']='invented GPU'
    class Model:
        def generate(self,*a,**k): return json.dumps({'claims':[proposal],'gaps':[]})
    result=asyncio.run(DeepResearcher(tmp_path,fetcher=lambda *a,**k:source,model=Model()).research(topic(source.url)))
    assert result['status']=='NEEDS_RESEARCH'
    assert 'invalid_extraction_proposal' in result['reasons']


def uniskill_source():
    import json
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures/uniskill-located.json').read_text(encoding='utf-8'))
    excerpts=fixture['excerpts']
    pages={}
    for e in excerpts: pages[e['location']]=pages.get(e['location'],'')+'\n'+e['text']
    text='\f'.join(pages.values())
    return SourceRecord(url=fixture['provenance']['paper'], title='UniSkill research paper',kind='pdf',
        text=text,locations=tuple(pages),sha256=hashlib.sha256(text.encode()).hexdigest(),
        fetched_at=datetime.now(timezone.utc),role='primary')


def test_uniskill_swapped_benchmark_context_is_rejected():
    from src.research.extract import verify_metric_context
    source=uniskill_source()
    assert not verify_metric_context(source,'page:7','84.7% success on WebShop', value=84.7,unit='%',
        target='success on WebShop',baseline='training-free, RL-only, and\nmemory- or skill-augmented RL baselines',conditions='ALFWorld’s officialvalid seen split')


@pytest.mark.parametrize('capture', [False, True])
def test_real_first_trial_excerpt_builds_useful_brief(tmp_path,capture):
    import json
    from src.editorial.brief import build_brief
    from src.editorial.models import EditorialBrief, UserContext
    source=uniskill_source()
    if capture:
        from pathlib import Path
        path=Path('temp/first-trial-captures/paper-pdf.json')
        if not path.exists(): pytest.skip('Optional complete local first-trial capture is unavailable')
        source=SourceRecord.model_validate_json(path.read_text(encoding='utf-8'))
    selected=[
        ('page:4','Accordingly, we retain only trajectories for which both reference sets remain nonempty after excluding\nthe proposer inputs, with all trajectories sharing the same task, retrieved skills, and current actor.'),
        ('page:6','At each training step, actor rollouts and skill proposals are generated from the same pre-update policy\nand skillbank snapshot.'),
        ('page:6','We compare UniSkill with training-free, RL-only, and\nmemory- or skill-augmented RL baselines.'),
        ('page:6','We evaluate UniSkill on ALFWorld (Shridhar et al., 2021) and WebShop (Yao et al.,\n2022), two multi-turn interactive benchmarks.'),
        ('page:6','All compared methods use Qwen2.5-7B-Instruct (Yang\net al., 2024) as the base model.'),
        ('page:6','Unless otherwise specified, the baseline results in Table 1 are\ndrawn from prior work (Feng et al., 2025; Shi et al., 2026), where they were obtained using the\nreleasedVERL-AGENTimplementations (Feng et al., 2025) under a common evaluation protocol.'),
        ('page:8','UniSkill reports mean ± std over three independent training runs.'),
        ('page:8','assess the contribution of joint learning, we compare UniSkill with three ablation variants in Table 2.'),
        ('page:8','All\nvariants start from an empty skillbank and retain skill retrieval during training, with identical critic\nchecks and skillbank update rules.'),
        ('page:9','Thus,Ralign is informative in aggregate among the sampled proposals\nbut does not guarantee improvement for every proposal.')]
    proposals=[{'url':source.url,'location':loc,'text':text} for loc,text in selected]
    for text,value,target,conditions in [
        ('98.4%\nsuccess on ALFWorld',98.4,'success on ALFWorld','ALFWorld’s officialvalid seen split'),
        ('84.7% success on WebShop',84.7,'success on WebShop','All compared methods use Qwen2.5-7B-Instruct (Yang\net al., 2024) as the base model.')]:
        proposals.append({'url':source.url,'location':'page:7','text':text,'metric_context':
            {'value':value,'unit':'%','target':target,'baseline':'training-free, RL-only, and\nmemory- or skill-augmented RL baselines','conditions':conditions}})
    class Model:
        def generate(self,*a,**k):return json.dumps({'claims':proposals,'gaps':[]})
    result=asyncio.run(DeepResearcher(tmp_path,fetcher=lambda *a,**k:source,model=Model()).research(topic(source.url)))
    assert result['status']=='RESEARCH_READY'
    brief=build_brief(result['packet'],UserContext())
    assert isinstance(brief,EditorialBrief), getattr(brief,'reasons',None)
    assert brief.study.dataset and brief.study.metrics and brief.study.ablation
    assert 'dataset_not_reported' not in brief.warnings
    assert 'metrics_not_reported' not in brief.warnings


def test_html_links_are_inspected_and_stay_on_selected_paper():
    from src.research.fetch import inspected_links
    links=inspected_links('<a href="https://arxiv.org/pdf/2610.10164v1">PDF</a>'
        '<a href="https://arxiv.org/abs/9999.12345">recommended unrelated paper</a>'
        '<p>Someone might invent https://github.com/imagined/repository here.</p>',
        'https://huggingface.co/papers/2610.10164','html')
    assert links==('https://arxiv.org/pdf/2610.10164v1',)


def test_model_missing_facts_stay_needs_research(tmp_path):
    import json
    source=record('https://arxiv.org/pdf/1234.56789','Literal method.',kind='pdf')
    class Model:
        def generate(self,*a,**k):return json.dumps({'claims':[{'url':source.url,
            'location':'page:1','text':'Literal method.'}], 'gaps':['evaluation split missing']})
    result=asyncio.run(DeepResearcher(tmp_path,fetcher=lambda *a,**k:source,model=Model()).research(topic(source.url)))
    assert result['status']=='NEEDS_RESEARCH'
    assert 'evaluation split missing' in result['reasons']


@pytest.mark.parametrize('heading_a,heading_b', [('ALFWORLD','WEBSHOP'),('MODEL 3B','MODEL 7B'),
    ('PERFORMANCE ON ALFWORLD','PERFORMANCE ON WEBSHOP')])
def test_numbered_siblings_cannot_borrow_generic_context(tmp_path,heading_a,heading_b):
    from src.research.extract import verify_metric_context, extract_sections
    from src.research.packet import build_packet, select_proposed_evidence
    from src.editorial.brief import _metric_reasons
    from src.editorial.models import EvidenceClaim, MetricContext, SourceRef
    source=record('https://arxiv.org/pdf/1234.56789',
        f'4 EXPERIMENTS\n4.1 {heading_a}\nBaseline: GRPO. Conditions: CPU A. Success rate 98.4%.\n'
        f'4.2 {heading_b}\nBaseline: PPO. Conditions: GPU B. Success rate 84.7%.',kind='pdf')
    context=dict(value=84.7,unit='%',target='Success rate',baseline='GRPO',conditions='CPU A')
    assert not verify_metric_context(source,'page:1','Success rate 84.7%',**context)
    packet=build_packet(topic(source.url),[source],artifact_dir=tmp_path)
    with pytest.raises(ValueError,match='unverified metric context'):
        select_proposed_evidence(packet,{'claims':[dict(url=source.url,location='page:1',
            text='Success rate 84.7%',metric_context=context)],'gaps':[]})
    sections=extract_sections(source)
    owner=next(s for s in sections if '84.7%' in s.text)
    claim=EvidenceClaim(text='Success rate 84.7%',kind='source_claim',metric_context=MetricContext(**context),
        source_refs=(SourceRef(url=source.url,sha256=source.sha256,location=owner.location),))
    reasons=_metric_reasons(claim,{(source.url,s.location):s for s in sections},{source.url:source.role})
    assert any('unsupported_metric_conditions' in reason for reason in reasons)


def test_numbered_siblings_can_use_explicit_shared_parent_setup():
    from src.research.extract import verify_metric_context
    source=record('https://arxiv.org/pdf/1234.56789',
        '4 EXPERIMENTS\n4.1 EXPERIMENTALSETUP\nBaseline: GRPO. Conditions: shared CPU.\n'
        '4.2 ALFWORLD\nSuccess rate 98.4%.\n4.3 WEBSHOP\nSuccess rate 84.7%.',kind='pdf')
    assert verify_metric_context(source,'page:1','Success rate 84.7%',value=84.7,unit='%',
        target='Success rate',baseline='GRPO',conditions='shared CPU')

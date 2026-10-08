import json
import pytest
from drafting_fixtures import drafting_input, response
from src.editorial.models import DraftText
from src.editorial.validate import validate_draft
from test_media import catalog


def korean(packet, brief):
    payload = response()
    sentence = f'문서는 요청을 먼저 저장한 뒤 응답하도록 설명한다. [E1]({packet.sources[0].url})'
    payload['sections'][0]['text'] = sentence
    payload['sections'][0]['claims'][0]['sentence'] = sentence
    return DraftText(**payload, packet=packet, policy_version=brief.policy_version)


def review(draft, packet, verdict='supported'):
    from src.editorial.grounding import review_draft
    class Reviewer:
        def generate(self, prompt):
            data = json.loads(prompt.split('\nREVIEW_INPUT\n')[1])
            return json.dumps({'judgments': [{'id': c['id'], 'verdict': verdict} for c in data['claims']]})
    return review_draft(draft, packet, Reviewer())


def test_korean_requires_independent_review(drafting_input):
    packet, brief = drafting_input
    draft = korean(packet, brief)
    assert validate_draft(draft, packet, brief).status == 'NEEDS_REVISION'
    assert validate_draft(draft, packet, brief, grounding=review(draft, packet)).status == 'REVIEW_READY'


@pytest.mark.parametrize('verdict', ['contradicted', 'unknown'])
def test_negative_review_blocks(drafting_input, verdict):
    packet, brief = drafting_input
    draft = korean(packet, brief)
    assert validate_draft(draft, packet, brief, grounding=review(draft, packet, verdict)).status == 'NEEDS_REVISION'


def test_changed_sentence_invalidates_review(drafting_input):
    packet, brief = drafting_input
    draft = korean(packet, brief)
    bound = review(draft, packet)
    changed = draft.model_copy(update={'frontmatter': draft.frontmatter.replace('PageIndex', 'Queue')})
    assert 'stale_grounding_review' in {i.code for i in validate_draft(changed, packet, brief, grounding=bound).issues}


@pytest.mark.parametrize('sentence,code', [('문서는 성공률이 99%라고 보고한다.', 'unsupported_metric'), ('직접 써보니 요청을 저장했다.', 'invented_experience')])
def test_review_cannot_override_guards(drafting_input, sentence, code):
    packet, brief = drafting_input
    draft = korean(packet, brief)
    section = draft.sections[0]
    sentence += f' [E1]({packet.sources[0].url})'
    section = section.model_copy(update={'text': sentence, 'claims': (section.claims[0].model_copy(update={'sentence': sentence}),)})
    draft = draft.model_copy(update={'sections': (section, *draft.sections[1:])})
    assert code in {i.code for i in validate_draft(draft, packet, brief, grounding=review(draft, packet)).issues}


def test_writer_attestation_rejected():
    from src.editorial.models import DraftPayload
    payload = response()
    payload['grounding'] = {'verdict': 'supported'}
    with pytest.raises(ValueError):
        DraftPayload.model_validate(payload)


def test_repair_excludes_unrelated_large_section(drafting_input):
    from src.editorial.draft import write_draft
    from test_drafting import FakeLLM
    packet, brief = drafting_input
    payload = response()
    original = ('어떤 조건에서 선택할까?\n' * 1000) + payload['sections'][1]['text']
    payload['sections'][1]['text'] = original
    payload['sections'][0]['text'] = '직접 써보니 빨랐다.'
    payload['sections'][0]['claims'] = []
    llm = FakeLLM(payload, {'sections': [response()['sections'][0]]})
    draft = write_draft(brief, packet, llm)
    assert draft.report.status == 'REVIEW_READY'
    assert draft.sections[1].text == original
    assert original not in llm.prompts[1]


def test_separate_reviewer_is_retained(drafting_input):
    from src.editorial.draft import write_draft
    from test_drafting import FakeLLM
    packet, brief = drafting_input
    draft = korean(packet, brief)
    class Reviewer:
        def generate(self, prompt):
            data = json.loads(prompt.split('\nREVIEW_INPUT\n')[1])
            return json.dumps({'judgments': [{'id': c['id'], 'verdict': 'supported'} for c in data['claims']]})
    result = write_draft(brief, packet, FakeLLM({'frontmatter': draft.frontmatter, 'sections': [s.model_dump() for s in draft.sections]}), reviewer=Reviewer())
    assert result.report.status == 'REVIEW_READY'
    assert result.grounding_review['content_sha256']


def test_snapshot_relocation_keeps_review_but_changed_evidence_blocks(drafting_input, tmp_path):
    from src.editorial.grounding import bound_verdicts
    packet, brief = drafting_input
    draft = korean(packet, brief)
    bound = review(draft, packet)
    relocated = packet.model_copy(update={'sources': (packet.sources[0].model_copy(update={'snapshot_path': tmp_path / 'copied.txt'}),)})
    assert bound_verdicts(draft, relocated, bound)
    changed = relocated.model_copy(update={'claims': (packet.claims[0].model_copy(update={'text': 'Changed meaning.'}), *packet.claims[1:])})
    with pytest.raises(ValueError, match='stale_grounding_review'):
        bound_verdicts(draft, changed, bound)


def metric_packet():
    import hashlib
    from src.editorial.models import EvidenceClaim, MetricContext, ResearchPacket, SourceRecord, SourceRef
    text = '# Study A\nBaseline queue; same workload.\naccuracy is 90%.\naccuracy is 80%.'
    source = SourceRecord(url='https://example.invalid/metric', title='Study', kind='markdown', text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(), locations=('section:0',), fetched_at='2026-10-07T00:00:00Z')
    claims = tuple(EvidenceClaim(text=f'accuracy is {v}%.', kind='source_claim', source_refs=(SourceRef(url=source.url, sha256=source.sha256, location='section:0'),),
        metric_context=MetricContext(value=v, unit='%', target='accuracy', baseline='Baseline queue', conditions='same workload')) for v in (90, 80))
    return ResearchPacket(topic_id='metric', question='Compare?', sources=(source,), claims=claims)


def test_safe_derived_difference_and_cross_study_rejection():
    from src.editorial.grounding import derived_difference
    packet = metric_packet()
    assert derived_difference(packet, ('E1', 'E2')) == (10, 'percentage_points')
    bad = packet.claims[1].model_copy(update={'metric_context': packet.claims[1].metric_context.model_copy(update={'conditions': 'different workload'})})
    with pytest.raises(ValueError):
        derived_difference(packet.model_copy(update={'claims': (packet.claims[0], bad)}), ('E1', 'E2'))


def test_numeric_paraphrase_needs_exact_verified_value():
    from src.editorial.grounding import reported_number_supported
    packet = metric_packet()
    assert reported_number_supported('저자는 accuracy가 90%라고 보고한다.', (packet.claims[0],), packet)
    assert not reported_number_supported('저자는 accuracy가 99%라고 보고한다.', (packet.claims[0],), packet)


def test_natural_nonassertive_heading(drafting_input):
    packet, brief = drafting_input
    draft = korean(packet, brief)
    section = draft.sections[0].model_copy(update={'text': '## 언제 채택을 고려할까?\n\n' + draft.sections[0].text})
    draft = draft.model_copy(update={'sections': (section, *draft.sections[1:])})
    assert validate_draft(draft, packet, brief, grounding=review(draft, packet)).status == 'REVIEW_READY'


def test_final_numeric_korean_and_fabrication_block(drafting_input):
    packet, brief = drafting_input
    metrics = metric_packet()
    packet = packet.model_copy(update={'sources': (*packet.sources, *metrics.sources), 'claims': (*packet.claims, *metrics.claims)})
    draft = korean(packet, brief)
    from src.editorial.models import DraftSection
    sentence = f'저자는 accuracy가 90%라고 보고한다. [E4]({metrics.sources[0].url})'
    context = '저자는 Baseline queue; same workload 조건을 보고한다.'
    section = DraftSection(id='result', text=sentence + '\n' + context, claims=(
        {'sentence': sentence, 'kind': 'source_claim', 'evidence_ids': ['E4']},
        {'sentence': context, 'kind': 'source_claim', 'evidence_ids': ['E4']},))
    # Context needs reader citation too, while the Korean number remains free of English quotes.
    context += f' [E4]({metrics.sources[0].url})'
    section = section.model_copy(update={'text': sentence + '\n' + context, 'claims': (section.claims[0], section.claims[1].model_copy(update={'sentence': context}))})
    draft = draft.model_copy(update={'sections': (*draft.sections, section)})
    report = validate_draft(draft, packet, brief, grounding=review(draft, packet))
    assert report.status == 'REVIEW_READY', report.issues
    sentence = sentence.replace('90%', '99%')
    section = section.model_copy(update={'text': sentence + '\n' + context, 'claims': (section.claims[0].model_copy(update={'sentence': sentence}), section.claims[1])})
    draft = draft.model_copy(update={'sections': (*draft.sections[:-1], section)})
    assert 'unsupported_metric' in {i.code for i in validate_draft(draft, packet, brief, grounding=review(draft, packet)).issues}


def test_persisted_review_and_capture_manifest(drafting_input, tmp_path):
    from src.editorial.store import DraftStore
    from src.editorial.grounding import bound_verdicts
    from src.editorial.models import ResearchPacket
    packet, brief = drafting_input
    draft = korean(packet, brief)
    bound = review(draft, packet)
    report = validate_draft(draft, packet, brief, grounding=bound)
    draft = draft.model_copy(update={'grounding_review': bound.model_dump(mode='json'), 'report': report})
    store = DraftStore(tmp_path / 'store')
    identity = store.begin(packet.topic_id)
    captures = store.directory(identity) / 'working' / 'model-calls'
    captures.mkdir(parents=True)
    (captures / 'model-call-fixture.json').write_text('{"stage": "grounding"}', encoding='utf-8')
    artifact = store.persist(identity, packet.topic_id, {'packet': packet, 'brief': brief, 'draft': draft,
        'validation': report, 'content': draft.content, 'status': report.status})
    directory = store.directory(identity)
    stored = ResearchPacket.model_validate_json((directory / 'packet.json').read_text(encoding='utf-8'))
    assert bound_verdicts(draft, stored, json.loads((directory / 'grounding.json').read_text(encoding='utf-8')))
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    assert 'grounding.json' in manifest and 'model-calls/model-call-fixture.json' in manifest
    store.verify(artifact)
    (directory / 'grounding.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='review file changed'):
        store.verify(artifact)


@pytest.mark.parametrize('with_media', [False, True])
def test_pipeline_uses_actual_adapter_captures_in_draft_bundle(drafting_input, tmp_path, monkeypatch, catalog, with_media):
    import asyncio
    from unittest.mock import AsyncMock
    from src.llm.client import ModelClient
    from test_llm_client import config
    from src.writer.blog_writer import BlogWriter
    from src.editorial.pipeline import EditorialPipeline
    from src.curator.matcher import CuratedTopic
    packet, brief = drafting_input
    draft = korean(packet, brief)
    def transport(**request):
        prompt = request['prompt']
        if '\nREVIEW_INPUT\n' in prompt:
            data = json.loads(prompt.split('\nREVIEW_INPUT\n')[1])
            return json.dumps({'judgments': [{'id': c['id'], 'verdict': 'supported'} for c in data['claims']]})
        return json.dumps({'frontmatter': draft.frontmatter, 'sections': [s.model_dump(mode='json') for s in draft.sections]})
    client = ModelClient(transport=transport, config=config())
    monkeypatch.setattr('src.writer.blog_writer.ModelClient', lambda: client)
    monkeypatch.setattr(BlogWriter, '_call_llm', lambda self, prompt: client.generate('draft', prompt, artifact_dir=self.artifact_dir / 'model-calls'))
    writer = BlogWriter(artifact_dir=tmp_path / 'global')
    if with_media:
        writer.media_catalog = catalog.model_copy(update={'assets': (catalog.assets[0].model_copy(update={'contexts': ('durable storage',)}),)})
    writer.researcher.research = AsyncMock(return_value={'packet': packet, 'status': 'RESEARCH_READY'})
    pipeline = EditorialPipeline(tmp_path / 'store', writer=writer)
    topic = CuratedTopic(rank=1, title='Queue', url=packet.sources[0].url, source='fixture', one_line_summary='queue', relevance_reason='reliability', suggested_angle=packet.question)
    artifact = asyncio.run(pipeline.generate(pipeline.register_topic(topic)))
    assert artifact.status == 'REVIEW_READY'
    directory = pipeline.store.directory(artifact.id)
    captures = [json.loads(p.read_text(encoding='utf-8')) for p in (directory / 'model-calls').glob('*.json')]
    assert {c['stage'] for c in captures} == {'draft', 'grounding'}
    assert sum(c['stage'] == 'grounding' for c in captures) == (2 if with_media else 1)
    from src.editorial.grounding import digest
    stored_draft = json.loads((directory / 'draft.json').read_text(encoding='utf-8'))
    assert stored_draft['grounding_review']['content_sha256'] == digest((directory / 'draft.md').read_text(encoding='utf-8'))
    assert not (tmp_path / 'global').exists()
    pipeline.store.verify(artifact)


@pytest.mark.parametrize('value', [10, 11])
def test_derived_draft_difference_is_checked_in_code(drafting_input, value):
    from src.editorial.models import DraftSection
    packet, brief = drafting_input
    metrics = metric_packet()
    packet = packet.model_copy(update={'sources': (*packet.sources, *metrics.sources), 'claims': (*packet.claims, *metrics.claims)})
    draft = korean(packet, brief)
    url = metrics.sources[0].url
    sentence = f'저자 보고값으로 계산한 차이는 accuracy 기준 {value}퍼센트포인트다. [E4]({url}) [E5]({url})'
    context = f'저자는 Baseline queue; same workload 조건을 보고한다. [E4]({url})'
    section = DraftSection(id='derived', text=sentence + '\n' + context, claims=(
        {'sentence': sentence, 'kind': 'source_claim', 'evidence_ids': ['E4', 'E5'], 'derived_from': ['E4', 'E5']},
        {'sentence': context, 'kind': 'source_claim', 'evidence_ids': ['E4']}))
    draft = draft.model_copy(update={'sections': (*draft.sections, section)})
    report = validate_draft(draft, packet, brief, grounding=review(draft, packet))
    assert (report.status == 'REVIEW_READY') == (value == 10), report.issues


def test_reported_number_allows_only_verified_model_ids():
    import hashlib
    from src.editorial.grounding import reported_number_supported
    packet = metric_packet()
    text = packet.sources[0].text.replace('Baseline queue;', 'Qwen2.5-7B; Baseline queue;')
    source = packet.sources[0].model_copy(update={'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()})
    claims = tuple(c.model_copy(update={'source_refs': (c.source_refs[0].model_copy(update={'sha256': source.sha256}),)}) for c in packet.claims)
    packet = packet.model_copy(update={'sources': (source,), 'claims': claims})
    assert reported_number_supported('저자는 Qwen2.5-7B accuracy가 90%라고 보고한다.', (claims[0],), packet)
    assert not reported_number_supported('저자는 Qwen9-72B accuracy가 90%라고 보고한다.', (claims[0],), packet)
    assert not reported_number_supported('저자는 Qwen2.5-7B accuracy가 90%이며 8회 실험했다고 보고한다.', (claims[0],), packet)


def test_identical_conditions_do_not_allow_sibling_study_subtraction():
    import hashlib
    from src.editorial.grounding import derived_difference, verified_metric
    packet = metric_packet()
    text = '# Study A\nBaseline queue; same workload.\naccuracy is 90%.\f# Study B\nBaseline queue; same workload.\naccuracy is 80%.'
    source = packet.sources[0].model_copy(update={'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'locations': ('section:0', 'section:1')})
    claims = tuple(c.model_copy(update={'source_refs': (c.source_refs[0].model_copy(update={'sha256': source.sha256, 'location': f'section:{i}'}),)}) for i, c in enumerate(packet.claims))
    packet = packet.model_copy(update={'sources': (source,), 'claims': claims})
    assert all(verified_metric(c, packet) for c in claims)
    with pytest.raises(ValueError, match='cross_study_comparison'):
        derived_difference(packet, ('E1', 'E2'))


def test_invalid_writer_span_can_be_repaired_before_review(drafting_input):
    from src.editorial.draft import write_draft
    from test_drafting import FakeLLM
    packet, brief = drafting_input
    bad = response()
    bad['sections'][0]['claims'][0]['sentence'] = 'Not a real span.'
    class Reviewer:
        def generate(self, prompt):
            data = json.loads(prompt.split('\nREVIEW_INPUT\n')[1])
            assert all(c['sentence'] != 'Not a real span.' for c in data['claims'])
            return json.dumps({'judgments': [{'id': c['id'], 'verdict': 'supported'} for c in data['claims']]})
    result = write_draft(brief, packet, FakeLLM(bad, {'sections': [response()['sections'][0]]}), reviewer=Reviewer())
    assert result.report.status == 'REVIEW_READY'
    assert result.revision_attempts == 1

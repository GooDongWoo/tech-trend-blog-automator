"""Actual adapter outage and durable configured Telegram briefing recovery."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from config import settings
from src.collector.base import TrendItem
from src.collector.orchestrator import TrendOrchestrator
from src.curator.matcher import TrendMatcher
from src.profiler.interest_profiler import UserProfile
from src.workflow.service import WorkflowService, ProductionAdapters


TOPIC = dict(rank=1, title='Queue', url='https://example.invalid/queue', source='fixture', one_line_summary='queue', relevance_reason='interest', suggested_angle='When should the queue be used?')
ITEM = TrendItem(title='Queue', url=TOPIC['url'], source='fixture')


class Profiler:
    def __init__(self): self.call_log = []
    @property
    def calls(self): return len(self.call_log)
    def build_profile(self, *, days):
        self.call_log.append(days)
        return UserProfile(core_interests=[], knowledge_depth={}, avoid_topics=[], target_domains=[], search_keywords=[])


class Collector:
    def __init__(self, unavailable=False):
        self.calls, self.unavailable = 0, unavailable
    async def collect(self, *, limit):
        self.calls += 1
        if self.unavailable: raise ConnectionError('offline outage')
        return [ITEM]


def actual_service(tmp_path, *, unavailable=False, matcher_output=None, sender=None):
    profiler, collector, matcher = Profiler(), Collector(unavailable), TrendMatcher()
    matcher._call_llm = lambda prompt: json.dumps([TOPIC]) if matcher_output is None else matcher_output()
    service = WorkflowService(tmp_path / 'runs', adapters=ProductionAdapters(profiler, TrendOrchestrator([collector]), matcher))
    return service, profiler, collector, matcher


def test_actual_orchestrator_outage_retries_only_collection(tmp_path):
    service, profiler, collector, _ = actual_service(tmp_path, unavailable=True)
    async def scenario():
        run = await service.request(reviewer='cli:alice')
        failed = await service.resume(run.id)
        assert failed.status == 'NEEDS_RESEARCH'
        assert 'collection' not in failed.checkpoints and 'curation' not in failed.checkpoints
        assert failed.failures['collection']['reason_codes'] == ['collection_empty_or_unavailable']
        collector.unavailable = False
        recovered = await service.resume(run.id)
        assert recovered.status == 'AWAITING_SELECTION'
        assert profiler.calls == 1 and collector.calls == 2
    asyncio.run(scenario())


def test_actual_matcher_empty_result_retries_only_curation(tmp_path):
    output = ['[]']
    service, profiler, collector, matcher = actual_service(tmp_path, matcher_output=lambda: output[0])
    async def scenario():
        run = await service.request(reviewer='cli:alice')
        failed = await service.resume(run.id)
        assert failed.status == 'NEEDS_RESEARCH'
        assert 'curation' not in failed.checkpoints and 'topics' not in failed.checkpoints
        assert failed.failures['curation']['reason_codes'] == ['curation_no_selectable_topics']
        output[0] = json.dumps([TOPIC])
        recovered = await service.resume(run.id)
        assert recovered.status == 'AWAITING_SELECTION'
        assert profiler.calls == 1 and collector.calls == 1
    asyncio.run(scenario())


def test_actual_bot_delivery_failure_resumes_frozen_payload_run_and_recipient(tmp_path, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, 'telegram_chat_id', '7')
    service, profiler, collector, matcher = actual_service(tmp_path)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=ConnectionError('offline send outage')))
    app = TrendBotApp(workflow=service)
    async def scenario():
        run = await app.trigger_briefing(context=SimpleNamespace(bot=bot))
        assert run.failures['briefing']['reason_codes'] == ['telegram_briefing_delivery_failed']
        assert run.briefing.delivered is False
        payload = dict(bot.send_message.call_args.kwargs)
        # Providers now offer a different card; retry must retain original run/payload.
        matcher._call_llm = lambda prompt: json.dumps([{**TOPIC, 'title': 'Changed'}])
        bot.send_message.side_effect = None
        restarted = TrendBotApp(workflow=WorkflowService(service.store.root, adapters=service.adapters))
        recovered = await restarted.trigger_briefing(run_id=run.id, context=SimpleNamespace(bot=bot))
        assert recovered.id == run.id and recovered.briefing.delivered
        assert 'briefing' not in recovered.failures
        assert profiler.calls == collector.calls == 1
        assert bot.send_message.call_args.kwargs == payload
        await restarted.trigger_briefing(run_id=run.id, context=SimpleNamespace(bot=bot))
        assert bot.send_message.call_count == 2
        assert recovered.approval is None and recovered.draft_id is None
    asyncio.run(scenario())


def test_structured_cli_briefing_options_and_safe_resume_sender(tmp_path, monkeypatch, capsys):
    import main
    monkeypatch.setattr(settings, 'telegram_chat_id', '7')
    service, profiler, collector, _ = actual_service(tmp_path)
    sent = []
    async def sender(payload):
        sent.append(payload)
        if len(sent) == 1: raise ConnectionError('offline delivery failure')
    service.briefing_sender = sender
    assert main.main(['briefing', '--mode', 'reviewed_trial', '--days', '9', '--topic-count', '2'], service=service) == 0
    failed = json.loads(capsys.readouterr().out)['run']
    assert failed['mode'] == 'reviewed_trial' and failed['days'] == 9 and failed['topic_count'] == 2
    assert failed['reviewer'] == 'telegram:7:7'
    assert failed['briefing']['chat_id'] == '7' and failed['briefing']['user_id'] == '7'
    assert main.main(['resume', failed['id']], service=service) == 0
    recovered = json.loads(capsys.readouterr().out)['run']
    assert recovered['briefing']['delivered'] and sent[0] == sent[1]
    assert profiler.calls == collector.calls == 1
    assert recovered['approval'] is None


def test_changed_configured_recipient_cannot_receive_saved_private_candidates(tmp_path, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, 'telegram_chat_id', '7')
    service, profiler, collector, _ = actual_service(tmp_path)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=ConnectionError('offline send failure')))
    app = TrendBotApp(workflow=service)
    run = asyncio.run(app.trigger_briefing(context=SimpleNamespace(bot=bot)))
    monkeypatch.setattr(settings, 'telegram_chat_id', '8')
    bot.send_message.side_effect = None
    recovered = asyncio.run(app.trigger_briefing(run_id=run.id, context=SimpleNamespace(bot=bot)))
    assert recovered.briefing.delivered is False
    assert bot.send_message.call_count == 1
    assert recovered.failures['briefing']['reason_codes'] == ['telegram_briefing_recipient_changed']
    assert profiler.calls == collector.calls == 1

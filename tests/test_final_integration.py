import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from config import settings
from test_workflow_briefing import actual_service


@pytest.mark.parametrize('custom', [False, True])
def test_cli_briefing_is_consumable_by_separately_constructed_bot(tmp_path, monkeypatch, capsys, custom):
    import main
    import src.workflow.service as module
    import src.workflow.telegram as telegram
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'src/workflow/service.py'))
    monkeypatch.setattr(settings, 'telegram_chat_id', '7')
    monkeypatch.setattr(settings, 'telegram_reviewer_user_id', '7')
    offline, *_ = actual_service(tmp_path)
    offline.adapters.research = lambda *args: {'status': 'NEEDS_RESEARCH', 'reasons': ['offline_fixture']}
    monkeypatch.setattr(module, 'ProductionAdapters', lambda: offline.adapters)
    sent = []
    async def sender(payload): sent.append(payload)
    monkeypatch.setattr(telegram, 'send_briefing', sender)
    root_args = ['--workflow-root', str(tmp_path / 'custom')] if custom else []
    assert main.main(['briefing', *root_args]) == 0
    run = json.loads(capsys.readouterr().out)['run']
    # Only startup is suppressed; main must wire the actual bot constructor.
    apps = []
    monkeypatch.setattr(TrendBotApp, 'run', lambda self: apps.append(self))
    assert main.main(['bot', *root_args]) == 0
    app = apps[0]
    assert app.workflow.status(run['id']).id == run['id']
    app.workflow.adapters = offline.adapters
    callback = sent[0]['rows'][0][0]['callback_data']
    assert callback == f"w:{run['id']}:1" and len(callback) <= 64
    query = SimpleNamespace(data=callback, message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7), answer=AsyncMock(), edit_message_text=AsyncMock())
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    selected = app.workflow.status(run['id'])
    assert selected.selected_rank == 1
    assert selected.status == 'NEEDS_RESEARCH'


def test_explicit_pipeline_keeps_its_isolated_workflow_root(tmp_path):
    from src.bot.telegram_bot import TrendBotApp
    from src.editorial.pipeline import EditorialPipeline
    pipeline = EditorialPipeline(tmp_path / 'isolated-review')
    assert TrendBotApp(pipeline=pipeline).workflow.store.root == pipeline.store.root / 'workflow'
    assert TrendBotApp(pipeline=pipeline, workflow_root=tmp_path / 'explicit').workflow.store.root == tmp_path / 'explicit'

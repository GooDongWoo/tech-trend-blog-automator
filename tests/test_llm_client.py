import json
import pytest
from config import Settings


def config(**kwargs):
    return Settings(_env_file=None, GEMINI_API_KEY='secret-key', GEMINI_MODEL='primary',
                    LLM_FALLBACK_MODELS=['gemini:backup'], **kwargs)


class Failure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('secret-key private prompt')


def test_transient_retry_success_and_output_limit():
    from src.llm.client import ModelClient
    calls = []
    def transport(**request):
        calls.append(request)
        if len(calls) == 1:
            raise Failure(503)
        return 'answer'
    assert ModelClient(transport=transport, config=config(), sleep=lambda _: None).generate('draft', 'input', max_output_tokens=12) == 'answer'
    assert len(calls) == 2
    assert all(call['max_output_tokens'] == 12 for call in calls)


def test_nonretryable_error_does_not_iterate_fallbacks():
    from src.llm.client import ModelClient, ModelCallError
    calls = []
    def transport(**request):
        calls.append(request)
        raise Failure(404)
    with pytest.raises(ModelCallError):
        ModelClient(transport=transport, config=config()).generate('draft', 'input')
    assert len(calls) == 1


def test_deadline_and_redacted_capture(tmp_path):
    from src.llm.client import ModelClient, ModelCallError
    now = [0.0]
    calls = []
    def transport(**request):
        calls.append(request)
        now[0] += 2
        raise Failure(503)
    with pytest.raises(ModelCallError, match='deadline'):
        ModelClient(transport=transport, config=config(LLM_STAGE_TIMEOUT_SECONDS=2), clock=lambda: now[0]).generate('draft', 'private prompt', artifact_dir=tmp_path)
    assert len(calls) == 1
    assert calls[0]['timeout'] == 2
    capture = next(tmp_path.glob('*.json')).read_text()
    assert 'secret-key' not in capture and 'private prompt' not in capture
    assert json.loads(capture)['status'] == 'deadline_exhausted'


def test_positive_limits():
    from src.llm.client import ModelClient
    with pytest.raises(ValueError):
        config(LLM_MAX_ATTEMPTS=0)
    with pytest.raises(ValueError):
        ModelClient(transport=lambda **_: 'text', config=config()).generate('draft', 'input', max_output_tokens=0)


def test_gemini_httpx_timeout_is_retryable():
    import httpx
    from src.llm.client import ModelClient
    calls = []
    def transport(**request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout('secret-key')
        return 'answer'
    assert ModelClient(transport=transport, config=config(), sleep=lambda _: None).generate('draft', 'input') == 'answer'


def test_attempt_budget_and_remaining_timeout():
    from src.llm.client import ModelClient, ModelCallError
    now, calls = [0.0], []
    def transport(**request):
        calls.append(request)
        now[0] += 1
        raise Failure(429)
    def sleep(seconds):
        now[0] += seconds
    with pytest.raises(ModelCallError, match='attempts_exhausted'):
        ModelClient(transport=transport, config=config(LLM_MAX_ATTEMPTS=2, LLM_STAGE_TIMEOUT_SECONDS=5),
                    clock=lambda: now[0], sleep=sleep).generate('draft', 'input')
    assert [call['timeout'] for call in calls] == [5, 3]
    assert [call['model'] for call in calls] == ['primary', 'backup']


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_sdk_limits_without_network(monkeypatch, provider):
    from types import SimpleNamespace
    from src.llm.client import ModelClient
    captured = {}
    def generate(**request):
        captured['request'] = request
        return SimpleNamespace(text='answer', choices=[SimpleNamespace(message=SimpleNamespace(content='answer'))])
    class Client:
        def __init__(self, **kwargs):
            captured['client'] = kwargs
            self.models = SimpleNamespace(generate_content=generate)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=generate))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    if provider == 'gemini':
        from google import genai
        monkeypatch.setattr(genai, 'Client', Client)
        cfg = config()
    else:
        import openai
        monkeypatch.setattr(openai, 'OpenAI', Client)
        cfg = Settings(_env_file=None, GEMINI_API_KEY='', OPENAI_API_KEY='secret-key')
    assert ModelClient(config=cfg).generate('draft', 'input', max_output_tokens=17) == 'answer'
    if provider == 'gemini':
        assert captured['client']['http_options'].retry_options.attempts == 1
        assert 0 < captured['client']['http_options'].timeout <= 30000
        assert captured['request']['config'].max_output_tokens == 17
    else:
        assert captured['client']['max_retries'] == 0
        assert 0 < captured['client']['timeout'] <= 30
        assert captured['request']['max_completion_tokens'] == 17

@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_sdk_usage_and_measured_elapsed_are_captured(monkeypatch, tmp_path, provider):
    from types import SimpleNamespace
    from src.llm.client import ModelClient
    now = [0.0]
    def generate(**request):
        now[0] += 1.25
        return SimpleNamespace(text='answer', choices=[SimpleNamespace(message=SimpleNamespace(content='answer'))],
            usage_metadata=SimpleNamespace(prompt_token_count=9, candidates_token_count=4, total_token_count=13),
            usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4, total_tokens=13))
    class Client:
        def __init__(self, **kwargs):
            self.models = SimpleNamespace(generate_content=generate)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=generate))
        def __enter__(self): return self
        def __exit__(self, *args): pass
    if provider == 'gemini':
        from google import genai
        monkeypatch.setattr(genai, 'Client', Client)
        cfg = config()
    else:
        import openai
        monkeypatch.setattr(openai, 'OpenAI', Client)
        cfg = Settings(_env_file=None, GEMINI_API_KEY='', OPENAI_API_KEY='secret')
    assert ModelClient(config=cfg, clock=lambda: now[0]).generate('draft', 'private', artifact_dir=tmp_path) == 'answer'
    record = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert record['elapsed_seconds'] == 1.25
    assert record['attempts'][0]['usage'] == {'input_tokens': 9, 'output_tokens': 4, 'total_tokens': 13}


def test_text_transport_usage_is_unknown(tmp_path):
    from src.llm.client import ModelClient
    ModelClient(transport=lambda **_: 'answer', config=config()).generate('draft', 'private', artifact_dir=tmp_path)
    record = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert record['attempts'][0]['usage'] is None
    assert record['elapsed_seconds'] >= 0

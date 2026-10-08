"""Bounded provider calls; diagnostics contain metadata, never request content."""
import hashlib
import json
import time
import uuid
from pathlib import Path
import httpx

from config import settings


class ModelCallError(RuntimeError):
    """Safe machine-readable provider failure, without provider response text."""


def _sdk_transport(*, provider, model, prompt, timeout, max_output_tokens, config):
    if provider == 'gemini':
        from google import genai
        from google.genai import types
        with genai.Client(api_key=config.gemini_api_key,
                         http_options=types.HttpOptions(timeout=max(1, int(timeout * 1000)),
                             retry_options=types.HttpRetryOptions(attempts=1))) as client:
            response = client.models.generate_content(model=model, contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=max_output_tokens))
            return response.text or ''
    from openai import OpenAI
    with OpenAI(api_key=config.openai_api_key, timeout=timeout, max_retries=0) as client:
        response = client.chat.completions.create(model=model,
            messages=[{'role': 'user', 'content': prompt}], max_completion_tokens=max_output_tokens)
        return response.choices[0].message.content or ''


class ModelClient:
    def __init__(self, *, transport=None, clock=time.monotonic, sleep=time.sleep, config=None):
        self.config = config or settings
        self.transport = transport
        self.clock = clock
        self.sleep = sleep

    def generate(self, stage: str, prompt: str, *, max_output_tokens: int = 4096,
                 artifact_dir: Path | None = None) -> str:
        if max_output_tokens <= 0:
            raise ValueError('max_output_tokens must be positive')
        cfg = self.config
        primary = ('gemini', cfg.gemini_model) if cfg.gemini_api_key else ('openai', cfg.openai_model)
        models = [primary]
        for value in cfg.llm_fallback_models:
            provider, separator, model = value.partition(':')
            if not separator or provider not in {'gemini', 'openai'} or not model:
                raise ValueError('fallback models require gemini:model or openai:model')
            if (provider, model) not in models:
                models.append((provider, model))
        deadline = self.clock() + cfg.llm_stage_timeout_seconds
        record = {'stage': stage, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                  'max_output_tokens': max_output_tokens, 'attempts': [], 'status': 'unavailable'}
        try:
            if not cfg.gemini_api_key and not cfg.openai_api_key and self.transport is None:
                raise ModelCallError('llm_unavailable')
            for attempt in range(cfg.llm_max_attempts):
                remaining = deadline - self.clock()
                if remaining <= 0:
                    record['status'] = 'deadline_exhausted'
                    raise ModelCallError('llm_deadline_exhausted')
                provider, model = models[min(attempt, len(models) - 1)]
                if self.transport is None and not getattr(cfg, f'{provider}_api_key'):
                    record['status'] = 'unavailable'
                    raise ModelCallError('llm_unavailable')
                timeout = min(cfg.llm_request_timeout_seconds, remaining)
                entry = {'provider': provider, 'model': model, 'timeout_seconds': timeout}
                record['attempts'].append(entry)
                try:
                    request = dict(provider=provider, model=model, prompt=prompt,
                                   timeout=timeout, max_output_tokens=max_output_tokens)
                    output = self.transport(**request) if self.transport else _sdk_transport(**request, config=cfg)
                except Exception as error:
                    code = getattr(error, 'status_code', None) or getattr(error, 'code', None)
                    entry['http_status'] = code if isinstance(code, int) else None
                    retryable = code in (408, 429) or isinstance(code, int) and 500 <= code < 600
                    retryable = retryable or isinstance(error, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError)) or type(error).__name__ in {'APITimeoutError', 'APIConnectionError'}
                    record['status'] = 'provider_error'
                    if not retryable:
                        raise ModelCallError('llm_nonretryable_error') from None
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        record['status'] = 'deadline_exhausted'
                        raise ModelCallError('llm_deadline_exhausted') from None
                    if attempt + 1 < cfg.llm_max_attempts:
                        self.sleep(min(2 ** attempt, remaining))
                    continue
                if self.clock() >= deadline:
                    record['status'] = 'deadline_exhausted'
                    raise ModelCallError('llm_deadline_exhausted')
                if not isinstance(output, str) or not output.strip():
                    record['status'] = 'empty_output'
                    raise ModelCallError('llm_empty_output')
                record['status'] = 'success'
                record['output_sha256'] = hashlib.sha256(output.encode()).hexdigest()
                return output
            raise ModelCallError('llm_attempts_exhausted')
        finally:
            if artifact_dir is not None:
                try:
                    Path(artifact_dir).mkdir(parents=True, exist_ok=True)
                    (Path(artifact_dir) / f'model-call-{uuid.uuid4().hex}.json').write_text(
                        json.dumps(record, indent=2), encoding='utf-8')
                except OSError:
                    raise ModelCallError('llm_capture_write_failed') from None

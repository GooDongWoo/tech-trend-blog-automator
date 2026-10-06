"""Fail closed on network/process use and isolate configured output paths."""
import socket
import subprocess

import httpx
import pytest

from config import settings


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch, tmp_path):
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair

    def internal_socketpair(*args, **kwargs):
        # Windows implements asyncio's private wakeup pipe through a loopback
        # socketpair. Allow only that synchronous stdlib construction, not an
        # arbitrary application's localhost connection.
        with monkeypatch.context() as local_patch:
            local_patch.setattr(socket.socket, "connect", original_connect)
            return original_socketpair(*args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must not use network or subprocesses")

    async def forbidden_async(*args, **kwargs):
        forbidden()

    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name in ("gemini_api_key", "openai_api_key", "telegram_bot_token"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "blog_repo_path", tmp_path / "blog")
    monkeypatch.setattr(settings, "obsidian_vault_path", tmp_path / "vault")
    monkeypatch.setattr(settings, "blog_base_url", "https://blog.example.invalid")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "socketpair", internal_socketpair)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden_async)

    # All writer invocations default to a fake empty response, even with local .env.
    from src.writer.blog_writer import BlogWriter
    monkeypatch.setattr(BlogWriter, "_call_llm", lambda self, prompt: "")

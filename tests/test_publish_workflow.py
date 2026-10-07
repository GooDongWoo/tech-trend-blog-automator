"""Local Git + temporary Vault proof; only push/remote inspection are faked."""
import asyncio
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import settings
from drafting_fixtures import drafting_input
from test_pipeline import setup_pipeline
from test_media import catalog
from src.editorial.pipeline import approval_callback
from src.publisher.git_publisher import GitPublisher
from src.publisher.obsidian_sync import ObsidianSync

REAL_POPEN = subprocess.Popen


@pytest.fixture
def publication(tmp_path, monkeypatch, drafting_input):
    repo = settings.blog_repo_path
    repo.mkdir()
    def local_process(args, *pos, **kwargs):
        assert args[0] == "git"
        assert not any(part in {"push", "fetch", "ls-remote"} for part in args)
        assert Path(kwargs["cwd"]).resolve().is_relative_to(tmp_path.resolve())
        return REAL_POPEN(args, *pos, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", local_process)
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-b", "main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    git("config", "core.autocrlf", "true")
    (repo / "README.md").write_text("fixture")
    git("add", "README.md")
    git("commit", "-m", "fixture baseline")
    git("remote", "add", "origin", str(tmp_path / "unused-remote"))
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("config", "branch.main.remote", "origin")
    git("config", "branch.main.merge", "refs/heads/main")
    settings.obsidian_vault_path.mkdir()
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    events = []
    state = {"push_error": None, "git_error": None, "remote_sha": None, "transport_calls": [], "push_output": None}
    def run(args, **kwargs):
        if args[1] == "push":
            destinations = git("remote", "get-url", "--push", "--all", "origin").splitlines()
            assert len(destinations) == 1
            assert args[2:] == ["--porcelain", "--no-follow-tags", destinations[0], git("rev-parse", "HEAD") + ":refs/heads/main"]
            state["transport_calls"].append(list(args))
            events.append("push")
            if state["push_error"] == "timeout":
                raise subprocess.TimeoutExpired(args, 60)
            if state["push_error"]:
                output = state["push_output"]
                if output is None and "[rejected]" in state["push_error"]:
                    output = "!\t" + git("rev-parse", "HEAD") + ":refs/heads/main\t[rejected] (fixture)\n"
                raise subprocess.CalledProcessError(1, args, output=output, stderr=state["push_error"])
            state["remote_sha"] = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            return subprocess.CompletedProcess(args, 0, state["push_output"] or ("To fixture\n \t" + state["remote_sha"] + ":refs/heads/main\tfixture -> main\nDone\n"), "")
        if args[1] == "ls-remote":
            assert args[2:] == ["--heads", git("remote", "get-url", "--push", "--all", "origin"), "refs/heads/main"]
            state["transport_calls"].append(list(args))
            return subprocess.CompletedProcess(args, 0, (state["remote_sha"] or "0" * 40) + "\trefs/heads/main\n", "")
        if args[1] == state["git_error"]:
            raise subprocess.CalledProcessError(1, args, stderr="fixture " + args[1] + " rejected")
        return subprocess.run(args, **kwargs)
    publisher = GitPublisher(repo, runner=run)
    sync = ObsidianSync(settings.obsidian_vault_path)
    return SimpleNamespace(repo=repo, pipeline=pipeline, artifact=artifact, git=git,
        publisher=publisher, sync=sync, events=events, state=state)


def approve(p):
    p.artifact = p.pipeline.approve(p.artifact.id, p.artifact.content_sha256)


def publish(p, **kwargs):
    return p.publisher.publish(p.pipeline.store, p.artifact.id, p.artifact.content_sha256, sync=p.sync, **kwargs)


def test_only_approved_reviewed_draft_can_write(publication):
    p = publication
    result = publish(p)
    assert not result["success"] and "APPROVED" in result["error"]
    assert not (p.repo / "_posts").exists()
    approve(p)
    result = p.publisher.publish(p.pipeline.store, p.artifact.id, "0" * 64, sync=p.sync)
    assert not result["success"] and "hash" in result["error"]
    assert not (p.repo / "_posts").exists()


@pytest.mark.parametrize("filename", ["draft.md", "packet.json", "validation.json", "manifest.json"])
def test_changed_bundle_blocks_copy(publication, filename):
    p = publication
    approve(p)
    (p.artifact.content_path.parent / filename).write_text("tampered")
    result = publish(p)
    assert not result["success"] and "changed" in result["error"]
    assert not (p.repo / "_posts").exists()


def test_success_scoped_commit_ignores_unrelated_index_and_duplicate(publication):
    p = publication
    (p.repo / "unrelated.txt").write_text("do not publish")
    p.git("add", "unrelated.txt")
    approve(p)
    result = publish(p)
    assert result["success"] and result["status"] == "PUBLISHED" and result["sync_status"] == "SYNCED", result
    assert p.git("rev-parse", "HEAD") == result["commit_sha"]
    assert p.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "_posts/2026-10-07-" + p.artifact.id + ".md"
    assert p.git("diff", "--cached", "--name-only") == "unrelated.txt"
    assert len(list(p.sync.wiki_dir.glob("*.md"))) == 1
    again = publish(p)
    assert again["commit_sha"] == result["commit_sha"]
    assert p.events == ["push"] and len(list((p.repo / "_posts").glob("*.md"))) == 1


def test_own_media_is_committed_and_other_assets_untouched(publication, catalog):
    p = publication
    item = catalog.assets[0].model_copy(update={"contexts": ("durable storage",)})
    p.pipeline.writer.media_catalog = catalog.model_copy(update={"assets": (item,)})
    p.artifact = asyncio.run(p.pipeline.generate(p.artifact.topic_id))
    (p.repo / "assets").mkdir()
    (p.repo / "assets" / "unrelated.gif").write_bytes(b"unrelated")
    p.git("add", "assets/unrelated.gif")
    approve(p)
    result = publish(p)
    assert result["success"]
    paths = p.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
    assert set(paths) == {"_posts/2026-10-07-" + p.artifact.id + ".md", "assets/images/memes/pixel.gif"}
    assert (p.repo / "assets/images/memes/pixel.gif").read_bytes() == p.artifact.media_paths[0].read_bytes()
    assert p.git("diff", "--cached", "--name-only") == "assets/unrelated.gif"


@pytest.mark.parametrize("command", ["add", "commit", "push"])
def test_git_failure_is_specific_and_never_syncs(publication, command):
    p = publication
    approve(p)
    if command == "push":
        p.state["push_error"] = "[rejected] fixture push rejected"
    else:
        p.state["git_error"] = command
    result = publish(p)
    assert not result["success"] and ("fixture " + command + " rejected") in result["error"]
    assert p.pipeline.get_draft(p.artifact.id).status == "APPROVED"
    assert not p.sync.wiki_dir.exists()


def test_push_failure_retry_reuses_same_commit(publication):
    p = publication
    approve(p)
    p.state["push_error"] = "[rejected] fixture push rejected"
    first = publish(p)
    assert first["commit_sha"] == p.git("rev-parse", "HEAD")
    p.state["push_error"] = None
    second = publish(p)
    assert second["success"] and second["commit_sha"] == first["commit_sha"]
    assert p.git("rev-list", "--count", "HEAD") == "2"


def test_sync_failure_preserves_published_and_retry_does_not_push(publication, monkeypatch):
    p = publication
    approve(p)
    real_sync = p.sync.sync_post
    monkeypatch.setattr(p.sync, "sync_post", lambda info: {"success": False, "error": "fixture Vault denied"})
    result = publish(p)
    assert result["success"] and result["sync_status"] == "SYNC_FAILED" and "Vault denied" in result["sync_error"]
    assert p.pipeline.get_draft(p.artifact.id).status == "PUBLISHED"
    p.sync = ObsidianSync(p.sync.vault_path)
    result = publish(p)
    assert result["success"] and result["sync_status"] == "SYNCED"
    assert p.events == ["push"]


def test_uncertain_push_requires_remote_reconciliation_before_sync(publication):
    p = publication
    approve(p)
    p.state["push_error"] = "timeout"
    first = publish(p)
    assert not first["success"] and first["status"] == "PUSH_UNCERTAIN"
    p.state["push_error"] = None
    assert not publish(p)["success"]
    assert not publish(p, reconcile=True)["success"]
    assert p.events == ["push"] and not p.sync.wiki_dir.exists()
    p.state["remote_sha"] = first["commit_sha"]
    recovered = publish(p, reconcile=True)
    assert recovered["success"] and recovered["sync_status"] == "SYNCED"
    assert p.events == ["push"]


def test_repository_root_and_existing_post_are_protected(publication):
    p = publication
    approve(p)
    bad = GitPublisher(p.repo / "nested", runner=p.publisher.runner)
    result = bad.publish(p.pipeline.store, p.artifact.id, p.artifact.content_sha256, sync=p.sync)
    assert not result["success"]
    (p.repo / "_posts").mkdir()
    path = p.repo / "_posts" / ("2026-10-07-" + p.artifact.id + ".md")
    path.write_text("already published")
    result = publish(p)
    assert not result["success"] and "exists" in result["error"]
    assert path.read_text() == "already published"


def test_unpushed_unrelated_history_blocks_publication(publication):
    p = publication
    (p.repo / "README.md").write_text("unrelated commit")
    p.git("add", "README.md")
    p.git("commit", "-m", "unpublished unrelated")
    approve(p)
    result = publish(p)
    assert not result["success"] and "upstream" in result["error"]
    assert not (p.repo / "_posts").exists()


def test_approved_bot_offers_hash_bound_publish_and_checks_reviewer(publication, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    p = publication
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    app = TrendBotApp(pipeline=p.pipeline, publisher=p.publisher, sync=p.sync)
    p.pipeline.bind_reviewer(p.artifact.id, "7", "7")
    query = SimpleNamespace(data=approval_callback(p.artifact), answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    button = query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data.startswith("p:" + p.artifact.id + ":")
    query.data = button.callback_data
    query.from_user.id = 8
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    assert not (p.repo / "_posts").exists()
    query.from_user.id = 7
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    assert p.pipeline.get_draft(p.artifact.id).status == "PUBLISHED"
    assert "SYNCED" in query.edit_message_text.call_args.args[0]


def test_legacy_script_import_and_missing_id_do_not_write(publication):
    from scripts import publish_and_sync
    p = publication
    with pytest.raises(SystemExit) as caught:
        publish_and_sync.main([])
    assert caught.value.code != 0
    assert not (p.repo / "_posts").exists() and not p.sync.wiki_dir.exists()


def test_tampered_retry_paths_cannot_commit_unrelated_files(publication):
    p = publication
    approve(p)
    p.state["git_error"] = "add"
    assert not publish(p)["success"]
    path = p.artifact.content_path.parent / "publication.json"
    journal = json.loads(path.read_text())
    (p.repo / "unrelated.txt").write_text("must stay private")
    journal["paths"].append("unrelated.txt")
    path.write_text(json.dumps(journal))
    p.state["git_error"] = None
    result = publish(p)
    assert not result["success"] and "paths" in result["error"]
    assert p.git("rev-list", "--count", "HEAD") == "1"
    assert not p.sync.wiki_dir.exists()


def test_post_publish_local_edit_does_not_push_during_sync_retry(publication, monkeypatch):
    p = publication
    approve(p)
    monkeypatch.setattr(p.sync, "sync_post", lambda info: {"success": False, "error": "unavailable"})
    first = publish(p)
    assert first["sync_status"] == "SYNC_FAILED"
    p.artifact.content_path.write_text("unreviewed edit")
    p.sync = ObsidianSync(p.sync.vault_path)
    result = publish(p)
    assert result["success"] and result["sync_status"] == "SYNCED"
    assert p.events == ["push"]
    assert "unreviewed edit" not in next(p.sync.wiki_dir.glob("*.md")).read_text(encoding="utf-8")


def test_interrupted_commit_is_not_guessed_or_recommitted(publication):
    p = publication
    approve(p)
    original = p.publisher.runner
    def crash(args, **kwargs):
        if args[1] == "commit":
            raise KeyboardInterrupt("simulated process death at commit boundary")
        return original(args, **kwargs)
    p.publisher.runner = crash
    with pytest.raises(KeyboardInterrupt):
        publish(p)
    p.publisher.runner = original
    result = publish(p)
    assert not result["success"] and "interrupted commit" in result["error"]
    assert p.git("rev-list", "--count", "HEAD") == "1"


def test_interrupted_push_journal_requires_explicit_remote_check(publication):
    p = publication
    approve(p)
    original = p.publisher.runner
    def crash(args, **kwargs):
        if args[1] == "push":
            # The remote may accept before the worker dies.
            p.state["remote_sha"] = p.git("rev-parse", "HEAD")
            raise KeyboardInterrupt("simulated process death after sending push")
        return original(args, **kwargs)
    p.publisher.runner = crash
    with pytest.raises(KeyboardInterrupt):
        publish(p)
    p.publisher.runner = original
    assert publish(p)["status"] == "PUSH_UNCERTAIN"
    result = publish(p, reconcile=True)
    assert result["success"] and result["sync_status"] == "SYNCED"
    assert p.events == []


def test_changed_remote_blocks_failed_push_retry(publication):
    p = publication
    approve(p)
    p.state["push_error"] = "rejected"
    assert not publish(p)["success"]
    p.git("remote", "set-url", "origin", str(p.repo.parent / "different-remote"))
    p.state["push_error"] = None
    result = publish(p)
    assert not result["success"] and "remote changed" in result["error"]
    assert p.events == ["push"]


def test_partial_vault_failure_is_retried_without_duplicate_note_or_daily_link(publication):
    p = publication
    approve(p)
    daily_dir = p.sync.vault_path / "10_Daily" / "2026"
    daily_dir.mkdir(parents=True)
    daily = daily_dir / "2026-10-07.md"
    daily.mkdir()  # real filesystem error after the knowledge note is written
    result = publish(p)
    assert result["success"] and result["sync_status"] == "SYNC_FAILED"
    daily.rmdir()
    daily.write_text("# Existing daily entry\n")
    result = publish(p)
    assert result["sync_status"] == "SYNCED"
    assert daily.read_text(encoding="utf-8").count("신규 기술 학습") == 1
    assert len(list(p.sync.wiki_dir.glob("*.md"))) == 1 and p.events == ["push"]


def test_missing_vault_is_not_recreated(publication):
    p = publication
    approve(p)
    p.sync.vault_path.rmdir()
    result = publish(p)
    assert result["status"] == "PUBLISHED" and result["sync_status"] == "SYNC_FAILED"
    assert not p.sync.vault_path.exists()


def test_destination_symlink_cannot_escape_repo(publication, tmp_path):
    p = publication
    approve(p)
    external = tmp_path / "outside"
    external.mkdir()
    try:
        (p.repo / "_posts").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Windows does not permit creating symlinks in this test session")
    result = publish(p)
    assert not result["success"] and "escaped" in result["error"]
    assert not list(external.iterdir())


def test_publish_callback_rejects_changed_approved_content(publication, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    p = publication
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    approve(p)
    p.pipeline.bind_reviewer(p.artifact.id, "7", "7")
    p.artifact.content_path.write_text("changed after approval")
    query = SimpleNamespace(data="p:" + approval_callback(p.artifact)[2:], answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    app = TrendBotApp(pipeline=p.pipeline, publisher=p.publisher, sync=p.sync)
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    assert p.pipeline.get_draft(p.artifact.id).status == "NEEDS_REVISION"
    assert not (p.repo / "_posts").exists() and p.events == []


def test_sync_requires_confirmed_receipt(publication):
    p = publication
    result = p.sync.sync_post({"draft_id": p.artifact.id})
    assert not result["success"] and "confirmed" in result["error"]
    assert not p.sync.wiki_dir.exists()


def test_crash_leftover_lock_requires_inspection(publication):
    p = publication
    approve(p)
    lock = p.artifact.content_path.parent / "publication.lock"
    lock.write_text("abandoned worker")
    result = publish(p)
    assert not result["success"] and "locked" in result["error"]
    assert not (p.repo / "_posts").exists()
    lock.unlink()  # explicit operator action after checking the abandoned worker
    assert publish(p)["success"]


def test_nonzero_transport_push_error_is_uncertain_and_not_repeated(publication):
    p = publication
    approve(p)
    p.state["push_error"] = "fatal: remote connection closed unexpectedly"
    result = publish(p)
    assert result["status"] == "PUSH_UNCERTAIN" and not result["success"]
    p.state["push_error"] = None
    again = publish(p)
    assert again["status"] == "PUSH_UNCERTAIN" and p.events == ["push"]
    assert not p.sync.wiki_dir.exists()


def test_confirmed_push_with_local_state_failure_reports_published_and_recovers(publication, monkeypatch):
    p = publication
    approve(p)
    saved = p.pipeline.store.save
    monkeypatch.setattr(p.pipeline.store, "save", lambda artifact: (_ for _ in ()).throw(OSError("local state disk failed")))
    first = publish(p)
    assert first["status"] == "PUBLISHED" and first["success"]
    assert first["local_state_error"] == "local state disk failed"
    assert not p.sync.wiki_dir.exists()
    monkeypatch.setattr(p.pipeline.store, "save", saved)
    recovered = publish(p)
    assert recovered["sync_status"] == "SYNCED" and recovered["commit_sha"] == first["commit_sha"]
    assert p.events == ["push"]


def test_reconcile_command_cannot_start_a_new_publication(publication):
    p = publication
    approve(p)
    result = publish(p, reconcile=True)
    assert not result["success"] and "reconciliation" in result["error"]
    assert not (p.repo / "_posts").exists() and p.events == []


def test_prepared_retry_cannot_push_intervening_unrelated_history(publication):
    p = publication
    approve(p)
    p.state["git_error"] = "add"
    assert not publish(p)["success"]
    (p.repo / "unrelated.txt").write_text("private intervening commit")
    p.git("add", "unrelated.txt")
    p.git("commit", "-m", "private intervening change")
    p.state["git_error"] = None
    result = publish(p)
    assert not result["success"] and "HEAD changed" in result["error"]
    assert p.git("rev-list", "--count", "HEAD") == "2" and p.events == []


def test_effective_push_destination_is_pinned_for_push_and_reconciliation(publication):
    p = publication
    destination = str(p.repo.parent / "actual-push-target")
    p.git("config", "remote.origin.pushurl", destination)
    approve(p)
    p.state["push_error"] = "timeout"
    result = publish(p)
    assert result["status"] == "PUSH_UNCERTAIN"
    assert p.state["transport_calls"][0][-2] == destination
    journal = json.loads((p.artifact.content_path.parent / "publication.json").read_text())
    assert journal["push_destination"] == destination
    p.state["push_error"] = None
    p.state["remote_sha"] = result["commit_sha"]
    recovered = publish(p, reconcile=True)
    assert recovered["success"]
    assert p.state["transport_calls"][-1][-2] == destination


def test_changed_effective_pushurl_blocks_same_sha_retry(publication):
    p = publication
    p.git("config", "remote.origin.pushurl", str(p.repo.parent / "initial-target"))
    approve(p)
    p.state["push_error"] = "[rejected] branch rejected"
    assert not publish(p)["success"]
    p.git("config", "remote.origin.pushurl", str(p.repo.parent / "different-target"))
    p.state["push_error"] = None
    result = publish(p)
    assert not result["success"] and "push destination changed" in result["error"]
    assert p.events == ["push"]


def test_multiple_pushurls_are_rejected_before_copy(publication):
    p = publication
    p.git("config", "--add", "remote.origin.pushurl", str(p.repo.parent / "target-one"))
    p.git("config", "--add", "remote.origin.pushurl", str(p.repo.parent / "target-two"))
    approve(p)
    result = publish(p)
    assert not result["success"] and "exactly one" in result["error"]
    assert not (p.repo / "_posts").exists() and p.events == []


def test_push_disables_followtags_and_proves_only_requested_branch(publication):
    p = publication
    p.git("config", "push.followTags", "true")
    p.git("tag", "-a", "unrelated-annotated", "-m", "unrelated tag")
    approve(p)
    result = publish(p)
    assert result["success"]
    assert "--no-follow-tags" in p.state["transport_calls"][0]


def test_branch_success_with_tag_rejection_requires_reconciliation(publication):
    p = publication
    approve(p)
    # Fake the transport result below the real commit, retaining its known SHA.
    original = p.publisher.runner
    def mixed(args, **kwargs):
        if args[1] == "push":
            sha = p.git("rev-parse", "HEAD")
            p.events.append("push")
            p.state["remote_sha"] = sha
            output = f"To fixture\n \t{sha}:refs/heads/main\tbranch accepted\n!\trefs/tags/unrelated:refs/tags/unrelated\t[remote rejected] (tag denied)\n"
            raise subprocess.CalledProcessError(1, args, output=output, stderr="error: failed to push some refs")
        return original(args, **kwargs)
    p.publisher.runner = mixed
    first = publish(p)
    assert not first["success"] and first["status"] == "PUSH_UNCERTAIN"
    p.publisher.runner = original
    assert publish(p)["status"] == "PUSH_UNCERTAIN" and p.events == ["push"]
    assert not p.sync.wiki_dir.exists()
    assert publish(p, reconcile=True)["success"] and p.events == ["push"]


def test_zero_exit_without_branch_receipt_is_uncertain(publication):
    p = publication
    approve(p)
    p.state["push_output"] = "To fixture\nDone\n"
    result = publish(p)
    assert not result["success"] and result["status"] == "PUSH_UNCERTAIN"
    assert not p.sync.wiki_dir.exists()


@pytest.mark.parametrize("exit_code,output,expected", [
    (0, "To fixture\n \taaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/main\taccepted\nDone\n", "confirmed"),
    (0, "=\taaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/main\t[up to date]\n", "confirmed"),
    (1, "!\taaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/main\t[rejected] (non-fast-forward)\n", "rejected"),
    (1, "!\trefs/tags/unrelated:refs/tags/unrelated\t[rejected] (tag)\n", "uncertain"),
    (1, " \taaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/main\taccepted\n!\trefs/tags/unrelated:refs/tags/unrelated\t[remote rejected] (tag)\n", "uncertain"),
    (0, "Done\n", "uncertain"),
    (1, "fatal: connection closed\n", "uncertain"),
    (0, "!\taaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/main\t[rejected]\n", "uncertain"),
])
def test_porcelain_receipt_requires_exact_intended_branch(exit_code, output, expected):
    assert GitPublisher._push_outcome(output, "a" * 40, "main", exit_code) == expected


def test_relative_push_path_matching_remote_alias_is_explicit_local_destination(publication):
    p = publication
    p.git("config", "remote.origin.pushurl", "deployment")
    p.git("remote", "add", "deployment", str(p.repo.parent / "wrong-alias-target"))
    approve(p)
    original = p.publisher.runner
    calls = []
    def transport(args, **kwargs):
        if args[1] == "push":
            calls.append(args)
            sha = p.git("rev-parse", "HEAD")
            return subprocess.CompletedProcess(args, 0, f"To fixture\n \t{sha}:refs/heads/main\taccepted\nDone\n", "")
        return original(args, **kwargs)
    p.publisher.runner = transport
    result = publish(p)
    assert result["success"]
    assert calls[0][-2] == str((p.repo / "deployment").resolve())


@pytest.mark.parametrize("rewrite_kind", ["insteadOf", "pushInsteadOf"])
def test_expanded_push_destination_cannot_be_rewritten_again(publication, rewrite_kind):
    p = publication
    p.git("config", "remote.origin.pushurl", "https://alias.example.invalid/repo")
    p.git("config", "url.https://first.example.invalid/.insteadOf", "https://alias.example.invalid/")
    p.git("config", "url.https://second.example.invalid/." + rewrite_kind, "https://first.example.invalid/")
    assert p.git("remote", "get-url", "--push", "--all", "origin") == "https://first.example.invalid/repo"
    approve(p)
    original = p.publisher.runner
    def transport(args, **kwargs):
        if args[1] == "push":
            p.events.append("push")
            sha = p.git("rev-parse", "HEAD")
            return subprocess.CompletedProcess(args, 0, f"To fixture\n \t{sha}:refs/heads/main\taccepted\nDone\n", "")
        return original(args, **kwargs)
    p.publisher.runner = transport
    result = publish(p)
    assert not result["success"] and "rewrite" in result["error"]
    assert not (p.repo / "_posts").exists() and p.events == []

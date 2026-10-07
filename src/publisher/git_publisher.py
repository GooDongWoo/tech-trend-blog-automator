"""Publish only hash-bound approvals, with a durable commit/push/sync journal."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import yaml

from config import settings
from src.editorial.media import IMAGE
from src.editorial.models import DraftArtifact, DraftStatus
from src.editorial.store import DraftStore, write_json


class GitPublisher:
    def __init__(self, repo_path: Path | None = None, *, runner=None):
        self.repo_path = Path(repo_path or settings.blog_repo_path).resolve()
        self.runner = runner or subprocess.run

    def _git(self, *args):
        # Preserve the exact reviewed bytes on Windows. Repository attributes or
        # filters that still transform them are rejected by the blob check.
        env = dict(os.environ)
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        env.update({"GIT_CONFIG_COUNT": str(count + 1), f"GIT_CONFIG_KEY_{count}": "core.autocrlf",
                    f"GIT_CONFIG_VALUE_{count}": "false"})
        result = self.runner(["git", *args], cwd=self.repo_path, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", check=True, timeout=60, env=env)
        # An injected runner must obey the same checked-result contract.
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
        return result.stdout.strip()

    def _repository(self):
        if not self.repo_path.is_dir() or self.repo_path.is_symlink():
            raise ValueError("destination repository does not exist")
        if Path(self._git("rev-parse", "--show-toplevel")).resolve() != self.repo_path:
            raise ValueError("destination must be the Git repository root")
        if self._git("rev-parse", "--is-bare-repository") != "false":
            raise ValueError("destination cannot be a bare repository")
        if self._git("rev-parse", "--show-object-format") != "sha1":
            raise ValueError("publication currently requires a SHA-1 Git repository")
        branch = self._git("branch", "--show-current")
        if not branch:
            raise ValueError("destination must have a checked-out branch")
        self._git("remote", "get-url", "origin")
        if self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") != "origin/" + branch:
            raise ValueError("destination upstream must be origin/current-branch")
        return branch

    def _push_destination(self):
        # get-url resolves pushurl and configured URL rewrites. Never push a
        # remote alias: one alias can fan out to multiple push destinations.
        destinations = self._git("remote", "get-url", "--push", "--all", "origin").splitlines()
        if len(destinations) != 1 or not destinations[0] or destinations[0].startswith("-"):
            raise ValueError("publication requires exactly one effective push destination")
        destination = destinations[0]
        # A relative local URL can equal another remote's name. Make file paths
        # absolute so push/ls-remote cannot reinterpret the pinned path as an alias.
        if ":" not in destination or Path(destination).drive:
            destination = str((self.repo_path / destination).resolve())
        # Git rewrites explicit URLs too. An expanded URL that still matches a
        # rewrite prefix would target a different endpoint on the second lookup.
        try:
            rewrites = self._git("config", "--null", "--get-regexp", r"^url\..*\.(insteadof|pushinsteadof)$")
        except subprocess.CalledProcessError as error:
            if error.returncode != 1 or error.stdout or error.stderr:
                raise
            rewrites = ""  # git config uses exit 1 for no matching keys.
        for record in filter(None, rewrites.split("\0")):
            key, separator, prefix = record.partition("\n")
            if not separator or destination.startswith(prefix):
                raise ValueError("effective push destination remains subject to a URL rewrite; use a canonical target")
        return destination

    @staticmethod
    def _push_outcome(output, sha, branch, returncode):
        """Accept only one porcelain receipt for the exact requested refspec.

        A tag rejection cannot establish branch rejection. Mixed, missing and
        malformed receipts leave the outcome uncertain even with exit code 0.
        """
        rows = [line.split("\t") for line in output.splitlines() if "\t" in line]
        if len(rows) != 1 or len(rows[0]) != 3:
            return "uncertain"
        flag, refs, detail = rows[0]
        if refs != sha + ":refs/heads/" + branch:
            return "uncertain"
        if returncode == 0 and flag in {" ", "=", "*", "+"}:
            return "confirmed"
        if returncode != 0 and flag == "!" and detail.startswith(("[rejected]", "[remote rejected]")):
            return "rejected"
        return "uncertain"

    @staticmethod
    def _publication_ref(remote, push_destination, branch):
        # Explicit URL pushes may leave origin's fetch tracking ref untouched.
        # This private ref is a confirmed publication checkpoint, not a claim
        # that the fetch endpoint advanced (it can differ from the push target).
        identity = json.dumps([remote, push_destination, branch], ensure_ascii=True).encode()
        return "refs/editorial/published/" + hashlib.sha256(identity).hexdigest()

    def _checkpoint_sha(self, ref):
        try:
            return self._git("rev-parse", "--verify", "--quiet", ref)
        except subprocess.CalledProcessError as error:
            if error.returncode != 1 or error.stdout or error.stderr:
                raise
            return None

    def _is_ancestor(self, ancestor, descendant):
        try:
            self._git("merge-base", "--is-ancestor", ancestor, descendant)
            return True
        except subprocess.CalledProcessError as error:
            if error.returncode != 1 or error.stdout or error.stderr:
                raise
            return False

    def _record_checkpoint(self, ref, journal):
        sha = journal["commit_sha"]
        previous = self._checkpoint_sha(ref)
        if previous == sha:
            return
        if previous:
            # A sync retry for an older publication must not rewind the tip.
            if self._is_ancestor(sha, previous):
                return
            if not self._is_ancestor(previous, journal["base_sha"]):
                raise ValueError("confirmed publication checkpoint has unrelated history; inspect local state")
        # Compare-and-swap prevents an external ref change from being overwritten.
        self._git("update-ref", ref, sha, previous or "0" * 40)

    def _target(self, name):
        target = self.repo_path / name
        if not target.resolve().is_relative_to(self.repo_path):
            raise ValueError("publication path escaped destination repository")
        return target

    def _snapshot(self, store, artifact):
        # Re-verify immediately before consuming bytes, then bind each read to
        # the verified manifest. Approval does not freeze files on disk.
        try:
            store.verify(artifact)
        except ValueError:
            if artifact.status == DraftStatus.APPROVED:
                store.save(artifact.transition(DraftStatus.NEEDS_REVISION))
            raise
        directory = store.directory(artifact.id)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest() != artifact.review_sha256:
            raise ValueError("review manifest changed")
        snapshots = {}
        for path in (artifact.content_path, *artifact.media_paths):
            name = path.relative_to(directory).as_posix()
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest.get(name):
                raise ValueError("review file changed or absent from manifest: " + name)
            snapshots[path] = data
        body = snapshots[artifact.content_path].decode("utf-8")
        if hashlib.sha256(snapshots[artifact.content_path]).hexdigest() != artifact.approved_sha256:
            raise ValueError("approved content hash changed")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", body, re.S)
        if not match:
            raise ValueError("reviewed article has invalid frontmatter")
        front = yaml.safe_load(match.group(1))
        if not isinstance(front, dict) or not isinstance(front.get("title"), str) or not front["title"].strip():
            raise ValueError("reviewed title missing")
        date = str(front.get("date", ""))[:10]
        from datetime import date as Date
        Date.fromisoformat(date)
        post = "_posts/" + date + "-" + artifact.id + ".md"
        files = {post: snapshots[artifact.content_path]}
        urls = [url for _, url in IMAGE.findall(body)]
        expected = ["/assets/images/memes/" + path.name for path in artifact.media_paths]
        if sorted(urls) != sorted(expected) or len(set(expected)) != len(expected):
            raise ValueError("article media references do not match this draft's assets")
        for path in artifact.media_paths:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.gif", path.name):
                raise ValueError("media path not allowed")
            files["assets/images/memes/" + path.name] = snapshots[path]
        # Compute the terminal artifact before push so later local mutation cannot
        # erase a confirmed external publication from the durable state.
        published = artifact.transition(DraftStatus.PUBLISHED, content_sha256=artifact.content_sha256)
        reviewed_topic = store.topic(artifact.topic_id)
        if hashlib.sha256(reviewed_topic.model_dump_json().encode()).hexdigest() != artifact.topic_id:
            raise ValueError("registered topic hash changed")
        topic = reviewed_topic.model_dump(mode="json")
        info = {"draft_id": artifact.id, "title": front["title"], "slug": artifact.id,
                "date": date, "topic": topic, "file_path": str(self._target(post))}
        return files, published, info

    def publish(self, store: DraftStore, draft_id: str, expected_sha256: str, *, sync=None, reconcile=False):
        """Publish/retry one approved revision; ambiguous pushes require reconciliation.

        A leftover lock after process death deliberately blocks publication. An
        operator must confirm no worker runs before removing that local lock.
        Interrupted COMMITTING also requires inspection; no commit is guessed.
        """
        lock = None
        repo_lock = None
        push_confirmed = False
        journal = {}
        try:
            artifact = store.get_draft(draft_id)
            if artifact.content_sha256 != expected_sha256 or artifact.approved_sha256 != expected_sha256:
                raise ValueError("explicit approved content hash mismatch; APPROVED draft required")
            if artifact.status not in {DraftStatus.APPROVED, DraftStatus.PUBLISHED}:
                raise ValueError("only APPROVED reviewed drafts may be published")
            directory = store.directory(draft_id)
            lock_path = directory / "publication.lock"
            try:
                lock = lock_path.open("x")
            except FileExistsError:
                raise ValueError("publication locked; inspect interrupted worker before recovery")
            journal_path = directory / "publication.json"
            if journal_path.exists():
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                if journal["repo"] != str(self.repo_path) or journal["content_sha256"] != expected_sha256:
                    raise ValueError("publication journal destination/hash mismatch")
            if reconcile and journal.get("phase") not in {"PUSHING", "PUSH_UNCERTAIN", "PUSHED"}:
                raise ValueError("reconciliation only checks an uncertain/confirmed push; no publication started")
            branch = self._repository()
            repo_lock_path = Path(self._git("rev-parse", "--git-path", "evidence-publication.lock"))
            if not repo_lock_path.is_absolute():
                repo_lock_path = self.repo_path / repo_lock_path
            try:
                repo_lock = repo_lock_path.open("x")
            except FileExistsError:
                raise ValueError("repository publication locked; inspect interrupted worker before recovery")
            remote = self._git("remote", "get-url", "origin")
            push_destination = self._push_destination()
            publication_ref = self._publication_ref(remote, push_destination, branch)
            if journal and journal["remote"] != remote:
                raise ValueError("publication journal remote changed")
            if journal and journal.get("push_destination") != push_destination:
                raise ValueError("publication journal push destination changed or was not pinned")
            if journal and journal["branch"] != branch:
                raise ValueError("publication journal branch changed")
            if journal.get("phase") == "COMMITTING":
                raise ValueError("interrupted commit requires inspection; no automatic second commit")
            if not journal:
                if artifact.status != DraftStatus.APPROVED:
                    raise ValueError("published draft missing its push receipt")
                base_sha = self._git("rev-parse", "HEAD")
                upstream_sha = self._git("rev-parse", "@{upstream}")
                if base_sha != upstream_sha and not (
                    base_sha == self._checkpoint_sha(publication_ref)
                    and self._is_ancestor(upstream_sha, base_sha)
                ):
                    raise ValueError("destination HEAD differs from upstream; unrelated history must be reconciled")
                files, published, info = self._snapshot(store, artifact)
                for name, data in files.items():
                    target = self._target(name)
                    if target.exists():
                        # Existing shared asset bytes are allowed only when tracked,
                        # clean and identical; published posts are never overwritten.
                        if name.startswith("_posts/"):
                            raise ValueError("destination post already exists")
                        if target.read_bytes() != data or self._git("status", "--porcelain", "--", name):
                            raise ValueError("destination media exists with different/uncommitted bytes")
                        self._git("ls-files", "--error-unmatch", "--", name)
                for name, data in files.items():
                    target = self._target(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Recheck containment after parent creation (including symlinks).
                    target = self._target(name)
                    if not target.exists():
                        with target.open("xb") as output:
                            output.write(data)
                journal = {"phase": "PREPARED", "repo": str(self.repo_path), "branch": branch, "remote": remote, "push_destination": push_destination,
                           "content_sha256": expected_sha256, "base_sha": base_sha, "paths": list(files), "info": info,
                           "published_artifact": published.model_dump(mode="json"), "sync_status": "NOT_STARTED"}
                write_json(journal_path, journal)
            if journal["phase"] == "PREPARED":
                if self._git("rev-parse", "HEAD") != journal["base_sha"]:
                    raise ValueError("destination HEAD changed since preparation; reconcile unrelated history")
                # Retry before commit still requires the complete approved bundle.
                files, published, info = self._snapshot(store, artifact)
                if journal["paths"] != list(files):
                    raise ValueError("publication retry paths do not match the approved draft")
                if journal["info"] != info or journal["published_artifact"] != published.model_dump(mode="json"):
                    raise ValueError("publication retry metadata differs from the approved draft")
                for name, data in files.items():
                    if self._target(name).read_bytes() != data:
                        raise ValueError("prepared publication files changed")
                self._git("add", "--", *journal["paths"])
                journal["phase"] = "COMMITTING"
                write_json(journal_path, journal)
                try:
                    # --only excludes unrelated entries already staged by the user.
                    self._git("commit", "--only", "-m", "feat(blog): publish reviewed draft " + draft_id,
                              "--", *journal["paths"])
                except subprocess.CalledProcessError:
                    journal["phase"] = "PREPARED"
                    write_json(journal_path, journal)
                    raise
                sha = self._git("rev-parse", "HEAD")
                journal["commit_sha"] = sha
                if self._git("rev-parse", sha + "^") != journal["base_sha"]:
                    raise ValueError("destination HEAD changed during commit; publication stopped")
                changed = set(self._git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines())
                if not changed or not changed.issubset(journal["paths"]):
                    raise ValueError("commit contains unexpected paths; publication stopped")
                for name, data in files.items():
                    # cat-file output is binary for GIF, so use a hash from Git's
                    # blob object ID against the reviewed bytes rather than text.
                    expected_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                    if self._git("rev-parse", sha + ":" + name) != expected_blob:
                        raise ValueError("committed content differs from approved snapshot")
                journal.update(phase="COMMITTED", commit_sha=sha)
                write_json(journal_path, journal)
            if journal["phase"] in {"PUSHING", "PUSH_UNCERTAIN"}:
                if not reconcile:
                    raise ValueError("push outcome uncertain; explicitly reconcile remote SHA before retry")
                rows = self._git("ls-remote", "--heads", journal["push_destination"], "refs/heads/" + branch).splitlines()
                remote = [row.split()[0] for row in rows if row.split()[1:] == ["refs/heads/" + branch]]
                if remote != [journal["commit_sha"]]:
                    raise ValueError("remote does not confirm this commit; push remains uncertain")
                push_confirmed = True
                journal["phase"] = "PUSHED"
                write_json(journal_path, journal)
            if journal["phase"] in {"COMMITTED", "PUSH_FAILED"}:
                if self._git("rev-parse", "HEAD") != journal["commit_sha"]:
                    raise ValueError("destination HEAD changed since draft commit; reconcile before pushing")
                store.verify(artifact)
                journal["phase"] = "PUSHING"
                write_json(journal_path, journal)
                try:
                    output = self._git("push", "--porcelain", "--no-follow-tags", journal["push_destination"],
                                       journal["commit_sha"] + ":refs/heads/" + branch)
                    if self._push_outcome(output, journal["commit_sha"], branch, 0) != "confirmed":
                        journal.update(phase="PUSH_UNCERTAIN", error="push receipt does not prove only the intended branch: " + output)
                        write_json(journal_path, journal)
                        raise ValueError(journal["error"])
                except subprocess.CalledProcessError as error:
                    rejected = self._push_outcome(error.stdout or "", journal["commit_sha"], branch, error.returncode) == "rejected"
                    journal.update(phase="PUSH_FAILED" if rejected else "PUSH_UNCERTAIN",
                                   error=error.stderr or error.stdout or str(error))
                    write_json(journal_path, journal)
                    raise
                except (OSError, subprocess.TimeoutExpired) as error:
                    journal.update(phase="PUSH_UNCERTAIN", error=str(error))
                    write_json(journal_path, journal)
                    raise
                push_confirmed = True
                journal["phase"] = "PUSHED"
                write_json(journal_path, journal)
            if journal["phase"] != "PUSHED":
                raise ValueError("unrecognized publication phase")
            push_confirmed = True
            self._record_checkpoint(publication_ref, journal)
            store.save(DraftArtifact.model_validate(journal["published_artifact"]))
            if sync is not None and journal["sync_status"] != "SYNCED":
                try:
                    outcome = sync.sync_post({**journal["info"], "commit_sha": journal["commit_sha"], "push_confirmed": True})
                    if not outcome.get("success"):
                        raise ValueError(outcome.get("error") or outcome.get("message") or "Vault sync failed")
                    journal.update(sync_status="SYNCED", sync_error=None)
                except Exception as error:
                    journal.update(sync_status="SYNC_FAILED", sync_error=str(error))
                write_json(journal_path, journal)
            return {"success": True, "status": "PUBLISHED", "commit_sha": journal["commit_sha"],
                    "sync_status": journal["sync_status"], "sync_error": journal.get("sync_error")}
        except (ValueError, OSError, KeyError, yaml.YAMLError, subprocess.SubprocessError) as error:
            detail = (error.stderr or error.stdout or str(error)) if isinstance(error, subprocess.CalledProcessError) else str(error)
            if push_confirmed:
                return {"success": True, "status": "PUBLISHED", "commit_sha": journal["commit_sha"],
                        "sync_status": journal.get("sync_status", "NOT_STARTED"),
                        "sync_error": journal.get("sync_error"), "local_state_error": detail}
            return {"success": False, "status": "PUSH_UNCERTAIN" if journal.get("phase") in {"PUSHING", "PUSH_UNCERTAIN"} else "PUBLISH_FAILED",
                    "error": detail, "commit_sha": journal.get("commit_sha"), "sync_status": journal.get("sync_status", "NOT_STARTED")}
        finally:
            if repo_lock is not None:
                repo_lock.close()
                repo_lock_path.unlink()
            if lock is not None:
                lock.close()
                lock_path.unlink()

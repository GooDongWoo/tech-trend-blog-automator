"""Idempotent Vault delivery after a confirmed push; never indexes the Vault."""
from datetime import date
from pathlib import Path
import re
import secrets

from config import settings


class ObsidianSync:
    def __init__(self, vault_path: Path | None = None):
        self.vault_path = Path(vault_path or settings.obsidian_vault_path).resolve()
        self.wiki_dir = self.vault_path / "40_Resources" / "42_기술_학습_위키"
        self.daily_dir = self.vault_path / "10_Daily" / str(date.today().year)

    def _inside(self, path):
        if not path.resolve().is_relative_to(self.vault_path):
            raise ValueError("sync path escaped Vault")
        return path

    def _write(self, path, content):
        path = self._inside(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._inside(path)
        temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def sync_post(self, info):
        try:
            if info.get("push_confirmed") is not True or not re.fullmatch(r"[a-f0-9]{40,64}", info.get("commit_sha", "")):
                raise ValueError("Vault sync requires a confirmed publication commit")
            identity = info.get("draft_id", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{16}", identity):
                raise ValueError("sync requires a reviewed draft ID")
            if not self.vault_path.is_dir():
                raise ValueError("configured Vault is missing; sync stopped")
            published_date = date.fromisoformat(info["date"])
            title = info["title"]
            clean_title = re.sub(r'[\\/*?:"<>|\[\]\r\n]', "", title).strip()[:100]
            if not clean_title:
                raise ValueError("invalid Vault title")
            filename = f"[학습] {clean_title} - {identity}.md"
            note_path = self._inside(self.wiki_dir / filename)
            topic = info.get("topic") or {}
            blog_url = settings.blog_base_url.rstrip("/") + "/" + info["slug"] + "/"
            content = (f"---\ntags: [knowledge, trend, tech]\ntype: resource\ncreated: {published_date}\n"
                       f"draft_id: {identity}\ncommit_sha: {info['commit_sha']}\n---\n\n# {clean_title}\n\n"
                       f"- 블로그: [{title}]({blog_url})\n- 원문: [{topic.get('source', '')}]({topic.get('url', '')})\n\n"
                       f"{topic.get('one_line_summary', '')}\n\n{topic.get('suggested_angle', '')}\n")
            if note_path.exists():
                if note_path.read_text(encoding="utf-8") != content:
                    raise ValueError("existing Vault note differs; preserved for review")
            else:
                self._write(note_path, content)
            daily = self._inside(self.vault_path / "10_Daily" / str(published_date.year) / f"{published_date}.md")
            if daily.exists():
                existing = daily.read_text(encoding="utf-8")
                link = f"- 신규 기술 학습: [[40_Resources/42_기술_학습_위키/{filename[:-3]}|{clean_title}]] (블로그 포스팅 완료)"
                if link not in existing:
                    self._write(daily, existing + "\n\n" + link + "\n")
            return {"success": True, "note_path": str(note_path), "note_title": clean_title}
        except (OSError, ValueError, KeyError, TypeError) as error:
            return {"success": False, "error": str(error)}

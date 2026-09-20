import datetime
import re
from pathlib import Path
from typing import Dict, Any

from config import settings


class ObsidianSync:
    """Syncs published blog posts back to Obsidian Vault as knowledge notes."""

    def __init__(self, vault_path: Path | None = None):
        self.vault_path = Path(vault_path or settings.obsidian_vault_path)
        self.wiki_dir = self.vault_path / "40_Resources" / "42_기술_학습_위키"
        self.daily_dir = self.vault_path / "10_Daily" / datetime.date.today().strftime("%Y")

    def _clean_title(self, title: str) -> str:
        """Remove invalid filename characters."""
        return re.sub(r'[\\/*?:"<>|]', "", title).strip()

    def sync_post(self, draft_info: Dict[str, Any]) -> Dict[str, Any]:
        """Create a knowledge note in Obsidian and link to today's daily note."""
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        title = draft_info.get("title", "기술 트렌드 학습")
        clean_name = self._clean_title(title)
        note_filename = f"[학습] {clean_name}.md"
        note_path = self.wiki_dir / note_filename

        topic = draft_info.get("topic")
        url = getattr(topic, "url", "") if topic else ""
        source = getattr(topic, "source", "") if topic else ""
        slug = draft_info.get("slug", "")
        blog_url = f"{settings.blog_base_url.rstrip('/')}/{slug}/"


        # 1. Create Knowledge Note
        note_content = f"""---
tags: [knowledge, trend, tech]
type: resource
created: {today_str}
---

# [학습] {clean_name}

## 1. 개요 및 배경
- **출처**: [{source}]({url})
- **블로그 포스트**: [{title}]({blog_url})
- **학습일**: {today_str}

## 2. 핵심 내용 및 아키텍처
{getattr(topic, 'one_line_summary', '')}

## 3. 실무 인사이트 및 관점
{getattr(topic, 'suggested_angle', '')}

---

## 🔗 연결된 지식 (Links)
- 상위 분류: [[40_Resources/42_기술_학습_위키/[학습] 개인공부 대시보드|[학습] 개인공부 대시보드]]
- 관련 일기: [[10_Daily/{datetime.date.today().strftime('%Y')}/{today_str}|{today_str}]]
"""
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        note_path.write_text(note_content, encoding="utf-8")

        # 2. Append link to today's daily note if it exists
        daily_note_path = self.daily_dir / f"{today_str}.md"
        if daily_note_path.exists():
            try:
                daily_content = daily_note_path.read_text(encoding="utf-8")
                link_line = f"- 신규 기술 학습: [[40_Resources/42_기술_학습_위키/{note_filename[:-3]}|{clean_name}]] (블로그 포스팅 완료)"
                if link_line not in daily_content:
                    if "## 🔗 연결된 지식" in daily_content:
                        daily_content = daily_content.replace(
                            "## 🔗 연결된 지식",
                            f"## 🔗 연결된 지식\n{link_line}"
                        )
                    else:
                        daily_content += f"\n\n## 🔗 연결된 지식 (Links)\n{link_line}\n"
                    daily_note_path.write_text(daily_content, encoding="utf-8")
            except Exception as e:
                print(f"[ObsidianSync] Failed to update daily note: {e}")

        return {
            "success": True,
            "note_path": str(note_path),
            "note_title": clean_name
        }

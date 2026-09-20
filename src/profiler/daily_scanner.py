import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any


class DailyScanner:
    """Scans recent daily notes and active projects in Obsidian Vault."""

    def __init__(self, vault_path: Path):
        self.vault_path = Path(vault_path)
        self.daily_dir = self.vault_path / "10_Daily"
        self.projects_dir = self.vault_path / "20_Projects"

    def get_recent_daily_notes(self, days: int = 14) -> List[Dict[str, Any]]:
        """Retrieve recent daily notes within the last N days."""
        notes = []
        if not self.daily_dir.exists():
            return notes

        # Find all YYYY-MM-DD.md files in 10_Daily/YYYY/
        pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
        note_files = []
        for root, _, files in os.walk(self.daily_dir):
            for f in files:
                m = pattern.match(f)
                if m:
                    file_path = Path(root) / f
                    note_files.append((m.group(1), file_path))

        # Sort descending by date string
        note_files.sort(key=lambda x: x[0], reverse=True)

        # Pick recent N files
        for date_str, path in note_files[:days]:
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                notes.append({
                    "date": date_str,
                    "path": str(path),
                    "content": content
                })
            except Exception as e:
                print(f"[DailyScanner] Error reading {path}: {e}")

        return notes

    def get_active_projects(self) -> List[Dict[str, Any]]:
        """Scan 20_Projects for active project notes."""
        projects = []
        if not self.projects_dir.exists():
            return projects

        for root, _, files in os.walk(self.projects_dir):
            for f in files:
                if f.endswith(".md"):
                    path = Path(root) / f
                    try:
                        content = path.read_text(encoding="utf-8", errors="ignore")
                        # Look for status: in-progress or project tags
                        if "status: in-progress" in content or "status: active" in content or "[개인]" in f:
                            projects.append({
                                "name": f.replace(".md", ""),
                                "path": str(path),
                                "content": content
                            })
                    except Exception as e:
                        print(f"[DailyScanner] Error reading {path}: {e}")

        return projects

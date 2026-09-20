import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any


class RAGChecker:
    """Checks knowledge depth for extracted interest keywords."""

    def __init__(self, vault_path: Path, daemon_url: str = "http://localhost:8765"):
        self.vault_path = Path(vault_path)
        self.daemon_url = daemon_url
        self.resources_dir = self.vault_path / "40_Resources"

    def is_daemon_alive(self) -> bool:
        """Check if local obsidian-knowledge daemon is responding."""
        try:
            req = urllib.request.Request(f"{self.daemon_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def query_knowledge_depth(self, keyword: str) -> List[Dict[str, Any]]:
        """Query existing knowledge for a keyword. Uses daemon if alive, else fallback to vault scan."""
        # 1. Fallback local scan across 40_Resources
        results = []
        if self.resources_dir.exists():
            for file in self.resources_dir.rglob("*.md"):
                try:
                    content = file.read_text(encoding="utf-8", errors="ignore")
                    if keyword.lower() in file.name.lower() or keyword.lower() in content.lower():
                        # Extract title and first 300 chars
                        lines = [line.strip() for line in content.split("\n") if line.strip() and not line.startswith("---")]
                        snippet = " ".join(lines[:5])[:300]
                        results.append({
                            "title": file.stem,
                            "path": str(file),
                            "snippet": snippet
                        })
                except Exception:
                    continue

        return results[:3]

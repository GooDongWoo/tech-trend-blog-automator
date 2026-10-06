import hashlib
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any


class RAGChecker:
    """Find note references; local matches establish interest, not knowledge depth."""

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
        """Return explicitly labeled local matches, never pretend they are RAG.

        No daemon search contract is configured here. Even a successful health
        check does not establish that retrieval used the daemon.
        """
        daemon_status = "available_not_queried" if self.is_daemon_alive() else "unavailable"
        if not keyword.strip():
            return []
        results = []
        if self.resources_dir.exists():
            for file in sorted(self.resources_dir.rglob("*.md")):
                try:
                    raw = file.read_bytes()
                    content = raw.decode("utf-8")
                    if keyword.lower() in file.name.lower() or keyword.lower() in content.lower():
                        # Extract title and first 300 chars
                        lines = [line.strip() for line in content.split("\n") if line.strip() and not line.startswith("---")]
                        snippet = " ".join(lines[:5])[:300]
                        results.append({
                            "title": file.stem,
                            "path": str(file),
                            "snippet": snippet,
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "retrieval_method": "local_search",
                            "daemon_status": daemon_status,
                            "depth": "unknown",
                        })
                except Exception:
                    continue

        return results[:3]

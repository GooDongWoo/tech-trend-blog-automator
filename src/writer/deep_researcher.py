import re
from typing import Dict, Any
import httpx
from bs4 import BeautifulSoup

from src.curator.matcher import CuratedTopic


class DeepResearcher:
    """Performs deep research on the selected topic by scraping raw docs/READMEs."""

    async def research(self, topic: CuratedTopic) -> Dict[str, Any]:
        url = topic.url
        content_text = ""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            # 1. GitHub repo handling
            if "github.com/" in url:
                # Try fetching raw README directly
                # e.g., https://github.com/owner/repo -> https://raw.githubusercontent.com/owner/repo/main/README.md
                match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
                if match:
                    owner, repo = match.group(1), match.group(2).replace(".git", "")
                    for branch in ["main", "master"]:
                        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
                        try:
                            r = await client.get(raw_url)
                            if r.status_code == 200:
                                content_text = r.text
                                break
                        except Exception:
                            pass

            # 2. General webpage scraping
            if not content_text:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        # Remove script/style/nav/footer
                        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                            tag.decompose()
                        text = soup.get_text(separator="\n")
                        # Clean multiple blank lines
                        lines = [l.strip() for l in text.split("\n") if l.strip()]
                        content_text = "\n".join(lines[:120])
                except Exception as e:
                    print(f"[DeepResearcher] Failed to scrape {url}: {e}")

        return {
            "topic": topic.title,
            "url": topic.url,
            "raw_content": content_text[:4000] or topic.one_line_summary,
            "suggested_angle": topic.suggested_angle,
            "relevance_reason": topic.relevance_reason
        }

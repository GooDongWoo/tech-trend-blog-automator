import re
import xml.etree.ElementTree as ET
from typing import List
import httpx

from .base import BaseCollector, TrendItem


class ArXivCollector(BaseCollector):
    name: str = "ArXiv"
    API_URL = (
        "http://export.arxiv.org/api/query?"
        "search_query=cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.SE&"
        "sortBy=submittedDate&sortOrder=descending&max_results=8"
    )

    async def collect(self, limit: int = 8) -> List[TrendItem]:
        items = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self.API_URL)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    # Atom namespace
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    for entry in root.findall("atom:entry", ns)[:limit]:
                        title = entry.findtext("atom:title", "", ns).strip()
                        title = re.sub(r"\s+", " ", title)
                        summary = entry.findtext("atom:summary", "", ns).strip()
                        summary = re.sub(r"\s+", " ", summary)
                        id_url = entry.findtext("atom:id", "", ns).strip()
                        published = entry.findtext("atom:published", "", ns).strip()

                        # First author
                        author_tag = entry.find("atom:author", ns)
                        author = author_tag.findtext("atom:name", "", ns) if author_tag is not None else ""

                        items.append(TrendItem(
                            title=f"ArXiv: {title}",
                            url=id_url,
                            source="ArXiv",
                            summary=summary[:250],
                            author=author,
                            published_at=published,
                            tags=["ArXiv", "Research"]
                        ))
        except Exception as e:
            print(f"[ArXivCollector] Failed: {e}")

        return items

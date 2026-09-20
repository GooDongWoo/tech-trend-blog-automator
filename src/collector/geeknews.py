import re
import xml.etree.ElementTree as ET
from typing import List
import httpx
from bs4 import BeautifulSoup

from .base import BaseCollector, TrendItem


class GeekNewsCollector(BaseCollector):
    name: str = "GeekNews"
    RSS_URL: str = "https://news.hada.io/rss/news"
    WEB_URL: str = "https://news.hada.io/"

    async def collect(self, limit: int = 15) -> List[TrendItem]:
        items = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # 1. Try RSS first
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                resp = await client.get(self.RSS_URL)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    channel = root.find("channel")
                    if channel is not None:
                        for entry in channel.findall("item")[:limit]:
                            title = entry.findtext("title", "").strip()
                            link = entry.findtext("link", "").strip()
                            description = entry.findtext("description", "").strip()
                            pub_date = entry.findtext("pubDate", "").strip()

                            # Clean HTML tags in description
                            clean_desc = re.sub(r"<[^>]+>", "", description).strip()

                            items.append(TrendItem(
                                title=title,
                                url=link,
                                source="GeekNews",
                                summary=clean_desc[:300],
                                published_at=pub_date
                            ))
                        if items:
                            return items
        except Exception as e:
            print(f"[GeekNewsCollector] RSS parse failed ({e}), falling back to HTML.")

        # 2. HTML Fallback
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                resp = await client.get(self.WEB_URL)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    topics = soup.select(".topic_row")
                    for topic in topics[:limit]:
                        title_tag = topic.select_one(".topictitle a")
                        if not title_tag:
                            continue
                        title = title_tag.get_text(strip=True)
                        link = title_tag.get("href", "")
                        if link.startswith("/"):
                            link = f"https://news.hada.io{link}"

                        desc_tag = topic.select_one(".topicdesc")
                        desc = desc_tag.get_text(strip=True) if desc_tag else ""

                        items.append(TrendItem(
                            title=title,
                            url=link,
                            source="GeekNews",
                            summary=desc[:300]
                        ))
        except Exception as e:
            print(f"[GeekNewsCollector] HTML parse failed: {e}")

        return items

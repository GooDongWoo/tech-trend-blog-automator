import asyncio
from typing import List
import httpx

from .base import BaseCollector, TrendItem


class HackerNewsCollector(BaseCollector):
    name: str = "Hacker News"
    TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
    SHOW_HN_URL = "https://hacker-news.firebaseio.com/v0/showstories.json"
    ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"

    async def _fetch_item(self, client: httpx.AsyncClient, item_id: int) -> TrendItem | None:
        try:
            resp = await client.get(self.ITEM_URL.format(item_id=item_id))
            if resp.status_code == 200:
                data = resp.json()
                if not data or data.get("type") != "story":
                    return None
                title = data.get("title", "")
                url = data.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
                score = data.get("score", 0)
                by = data.get("by", "")
                return TrendItem(
                    title=f"HN: {title}",
                    url=url,
                    source="Hacker News",
                    summary=f"Score: {score} by {by}",
                    score=score,
                    author=by
                )
        except Exception:
            return None

    async def collect(self, limit: int = 15) -> List[TrendItem]:
        items = []
        async with httpx.AsyncClient(timeout=8.0) as client:
            try:
                # Top stories + Show HN
                top_resp = await client.get(self.TOP_STORIES_URL)
                show_resp = await client.get(self.SHOW_HN_URL)

                top_ids = top_resp.json()[:10] if top_resp.status_code == 200 else []
                show_ids = show_resp.json()[:5] if show_resp.status_code == 200 else []
                target_ids = list(dict.fromkeys(top_ids + show_ids))[:limit]

                tasks = [self._fetch_item(client, i_id) for i_id in target_ids]
                results = await asyncio.gather(*tasks)
                items = [r for r in results if r is not None]
            except Exception as e:
                print(f"[HackerNewsCollector] Failed to fetch: {e}")

        return items

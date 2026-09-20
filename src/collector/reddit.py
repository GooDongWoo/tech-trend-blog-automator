from typing import List
import httpx

from .base import BaseCollector, TrendItem


class RedditCollector(BaseCollector):
    name: str = "Reddit"
    SUBREDDITS = ["MachineLearning", "LocalLLaMA", "programming"]

    async def _fetch_subreddit(self, client: httpx.AsyncClient, sub: str, limit: int = 4) -> List[TrendItem]:
        items = []
        url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={limit}"
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                children = data.get("data", {}).get("children", [])
                for child in children:
                    post = child.get("data", {})
                    title = post.get("title", "")
                    permalink = post.get("permalink", "")
                    post_url = post.get("url", f"https://reddit.com{permalink}")
                    score = post.get("score", 0)
                    selftext = post.get("selftext", "")[:250]
                    author = post.get("author", "")

                    items.append(TrendItem(
                        title=f"r/{sub}: {title}",
                        url=post_url,
                        source=f"Reddit (r/{sub})",
                        summary=selftext or f"Upvotes: {score}",
                        score=score,
                        author=author,
                        tags=[sub]
                    ))
        except Exception as e:
            print(f"[RedditCollector] Failed for r/{sub}: {e}")
        return items

    async def collect(self, limit: int = 15) -> List[TrendItem]:
        items = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(timeout=8.0, headers=headers, follow_redirects=True) as client:
            for sub in self.SUBREDDITS:
                sub_items = await self._fetch_subreddit(client, sub, limit=4)
                items.extend(sub_items)

        return items[:limit]

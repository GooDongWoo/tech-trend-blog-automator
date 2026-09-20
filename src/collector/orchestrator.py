import asyncio
from typing import List

from .base import BaseCollector, TrendItem
from .geeknews import GeekNewsCollector
from .github_trending import GitHubTrendingCollector
from .hackernews import HackerNewsCollector
from .reddit import RedditCollector
from .huggingface import HuggingFaceCollector
from .arxiv_collector import ArXivCollector


class TrendOrchestrator:
    """Runs all collectors concurrently and aggregates trend items."""

    def __init__(self, collectors: List[BaseCollector] | None = None):
        self.collectors = collectors or [
            GeekNewsCollector(),
            GitHubTrendingCollector(),
            HackerNewsCollector(),
            RedditCollector(),
            HuggingFaceCollector(),
            ArXivCollector(),
        ]

    async def collect_all(self, limit_per_source: int = 10) -> List[TrendItem]:
        """Collect trends from all sources concurrently."""
        tasks = [c.collect(limit=limit_per_source) for c in self.collectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items: List[TrendItem] = []
        seen_urls = set()
        seen_titles = set()

        for res in results:
            if isinstance(res, Exception):
                print(f"[TrendOrchestrator] A collector raised an exception: {res}")
                continue
            for item in res:
                # Deduplicate by url and title
                norm_title = item.title.lower().strip()
                if item.url in seen_urls or norm_title in seen_titles:
                    continue
                seen_urls.add(item.url)
                seen_titles.add(norm_title)
                all_items.append(item)

        return all_items

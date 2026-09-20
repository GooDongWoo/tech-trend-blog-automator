import re
from typing import List
import httpx
from bs4 import BeautifulSoup

from .base import BaseCollector, TrendItem


class GitHubTrendingCollector(BaseCollector):
    name: str = "GitHub Trending"
    LANGUAGES = ["", "python", "typescript", "rust", "c++"]

    async def _fetch_language_trending(self, client: httpx.AsyncClient, lang: str, limit: int = 5) -> List[TrendItem]:
        items = []
        url = f"https://github.com/trending/{lang}" if lang else "https://github.com/trending"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return items

            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("article.Box-row")
            for row in rows[:limit]:
                title_tag = row.select_one("h2 a")
                if not title_tag:
                    continue
                repo_path = "".join(title_tag.get_text().split())
                repo_url = f"https://github.com/{repo_path}"

                desc_tag = row.select_one("p")
                desc = desc_tag.get_text(strip=True) if desc_tag else ""

                # Language
                lang_tag = row.select_one("[itemprop='programmingLanguage']")
                detected_lang = lang_tag.get_text(strip=True) if lang_tag else (lang or "Unknown")

                # Stars today
                stars_today_tag = row.select_one("span.d-inline-block.float-sm-right")
                stars_today_text = stars_today_tag.get_text(strip=True) if stars_today_tag else ""
                stars_match = re.search(r"(\d[\d,]*)", stars_today_text)
                stars_today = int(stars_match.group(1).replace(",", "")) if stars_match else 0

                items.append(TrendItem(
                    title=f"GitHub: {repo_path}",
                    url=repo_url,
                    source="GitHub Trending",
                    summary=desc,
                    tags=[detected_lang] if detected_lang else [],
                    score=stars_today
                ))
        except Exception as e:
            print(f"[GitHubTrendingCollector] Failed for {lang}: {e}")
        return items

    async def collect(self, limit: int = 20) -> List[TrendItem]:
        items = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            for lang in self.LANGUAGES:
                sub_items = await self._fetch_language_trending(client, lang, limit=4)
                items.extend(sub_items)

        # Deduplicate by url
        seen = set()
        deduped = []
        for it in items:
            if it.url not in seen:
                seen.add(it.url)
                deduped.append(it)
        return deduped[:limit]

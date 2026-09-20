from typing import List
import httpx

from .base import BaseCollector, TrendItem


class HuggingFaceCollector(BaseCollector):
    name: str = "Hugging Face"
    PAPERS_URL = "https://huggingface.co/api/daily_papers"
    MODELS_URL = "https://huggingface.co/api/models?sort=likes30d&direction=-1&limit=8"

    async def collect(self, limit: int = 15) -> List[TrendItem]:
        items = []
        headers = {"User-Agent": "TechTrendBlogAutomator/1.0"}
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            # 1. Daily Papers
            try:
                p_resp = await client.get(self.PAPERS_URL)
                if p_resp.status_code == 200:
                    papers = p_resp.json()
                    for p in papers[:7]:
                        title = p.get("title", "")
                        paper_id = p.get("paper", {}).get("id") or p.get("id")
                        summary = p.get("paper", {}).get("summary") or p.get("summary", "")
                        upvotes = p.get("paper", {}).get("upvotes", 0)
                        url = f"https://huggingface.co/papers/{paper_id}" if paper_id else "https://huggingface.co/papers"

                        items.append(TrendItem(
                            title=f"HF Paper: {title}",
                            url=url,
                            source="Hugging Face",
                            summary=summary[:250],
                            score=upvotes,
                            tags=["AI Paper", "Hugging Face"]
                        ))
            except Exception as e:
                print(f"[HuggingFaceCollector] Papers failed: {e}")

            # 2. Trending Models
            try:
                m_resp = await client.get(self.MODELS_URL)
                if m_resp.status_code == 200:
                    models = m_resp.json()
                    for m in models[:5]:
                        model_id = m.get("id", "")
                        likes = m.get("likes", 0)
                        downloads = m.get("downloads", 0)
                        url = f"https://huggingface.co/{model_id}"

                        items.append(TrendItem(
                            title=f"HF Model: {model_id}",
                            url=url,
                            source="Hugging Face",
                            summary=f"Likes: {likes}, Downloads: {downloads}",
                            score=likes,
                            tags=["Model", "Hugging Face"]
                        ))
            except Exception as e:
                print(f"[HuggingFaceCollector] Models failed: {e}")

        return items[:limit]

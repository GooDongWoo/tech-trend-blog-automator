import json
from typing import List, Optional
from pydantic import BaseModel, Field

from config import settings
from src.profiler.interest_profiler import UserProfile
from src.collector.base import TrendItem


class CuratedTopic(BaseModel):
    rank: int = Field(description="1~5 순위")
    title: str = Field(description="주제 제목")
    url: str = Field(description="원문 링크")
    source: str = Field(description="출처 (GeekNews, GitHub 등)")
    one_line_summary: str = Field(description="기술 팩트 중심의 1줄 요약")
    relevance_reason: str = Field(description="사용자 최근 관심사/지식 깊이와의 연결고리 및 추천 이유")
    suggested_angle: str = Field(description="유쾌하고 재밌는 블로그 글로 풀어나갈 추천 접근 각도")


class TrendMatcher:
    """Matches collected trends with UserProfile and selects the Top 5 topics."""

    def __init__(self):
        pass

    def _call_llm(self, prompt: str) -> str:
        if settings.gemini_api_key:
            from google import genai
            client = genai.Client(api_key=settings.gemini_api_key)
            models_to_try = [settings.gemini_model, "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]
            models_to_try = list(dict.fromkeys(models_to_try))
            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt
                    )
                    if response.text:
                        return response.text
                except Exception as e:
                    print(f"[TrendMatcher] Gemini {model_name} failed: {e}. Trying next fallback...")



        if settings.openai_api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=settings.openai_api_key)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                print(f"[TrendMatcher] OpenAI API call failed: {e}")

        # Fallback if no LLM key
        print("[TrendMatcher] No LLM API key. Using heuristic selection.")
        return ""

    def curate_top_5(self, profile: UserProfile, items: List[TrendItem]) -> List[CuratedTopic]:
        """Select and format Top 5 topics matching user's profile."""
        if not items:
            return []

        # Prepare candidates text for LLM
        candidates_text = []
        for i, it in enumerate(items[:40]):
            candidates_text.append(f"[{i+1}] [{it.source}] {it.title}\nURL: {it.url}\nSummary: {it.summary[:150]}\nScore: {it.score}\n")
        candidates_str = "\n".join(candidates_text)

        prompt = f"""
다음은 사용자의 관심사 및 지식 깊이 프로필이다:
- 핵심 관심사: {profile.core_interests}
- 보유 지식 수준: {profile.knowledge_depth}
- 절대 추천 금지 (기초/입문): {profile.avoid_topics}
- 타겟 도메인: {profile.target_domains}

아래 수집된 원시 트렌드 목록 중에서, 사용자의 관심사 및 지식 수준에 가장 잘 맞고 흥미로운 **상위 5개 주제**를 엄선하라.
기초적인 튜토리얼이나 뻔한 홍보성 글은 제외하고, 기술적 깊이가 있거나 실무에서 자극을 줄 수 있는 주제를 선정하라.

[후보 트렌드 목록]
{candidates_str}

[출력 형식: 반드시 아래 JSON 배열 형식으로만 응답하라. ```json ... ``` 코드블록 사용]
```json
[
  {{
    "rank": 1,
    "title": "주제 제목 (한글 번역 및 정돈)",
    "url": "원문 URL",
    "source": "출처",
    "one_line_summary": "핵심 내용 1줄 요약",
    "relevance_reason": "사용자의 최근 관심사와 어떤 점에서 밀접한지, 왜 흥미로울지 설명",
    "suggested_angle": "블로그 포스트로 작성할 때 잡으면 좋을 유쾌하고 맛깔난 접근 각도"
  }}
]
```
"""
        raw_output = self._call_llm(prompt)
        curated: List[CuratedTopic] = []

        try:
            cleaned = raw_output.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            data = json.loads(cleaned)
            for d in data[:5]:
                curated.append(CuratedTopic(**d))
        except Exception as e:
            print(f"[TrendMatcher] LLM parsing failed or no LLM key ({e}). Using heuristic fallback.")
            # Heuristic selection from items
            for idx, it in enumerate(items[:5], 1):
                curated.append(CuratedTopic(
                    rank=idx,
                    title=it.title,
                    url=it.url,
                    source=it.source,
                    one_line_summary=it.summary[:100] or it.title,
                    relevance_reason=f"최신 기술 트렌드 ({it.source}) 화제 항목",
                    suggested_angle="실제 써보고 뜯어보는 실무 개발자 관점의 팩트 폭격 리뷰"
                ))

        return curated

    @staticmethod
    def format_telegram_card(topics: List[CuratedTopic]) -> str:
        """Format curated topics into a friendly, structured Telegram briefing message."""
        lines = [
            "🔥 **[오늘의 기술 트렌드 추천 5선]** 🔥",
            "동우님의 최근 Obsidian 관심사와 기술 깊이를 바탕으로 엄선한 오늘자 트렌드입니다!\n"
        ]
        for t in topics:
            lines.append(
                f"**{t.rank}️⃣ {t.title}** ({t.source})\n"
                f"📝 {t.one_line_summary}\n"
                f"💡 **추천 이유**: {t.relevance_reason}\n"
                f"🎯 **블로그 각도**: {t.suggested_angle}\n"
                f"🔗 [원문 보기]({t.url})\n"
            )
        lines.append("👇 아래 버튼을 눌러 오늘 블로그로 작성할 주제 **1개**를 선택해주세요!")
        return "\n".join(lines)

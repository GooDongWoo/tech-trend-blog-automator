import json
from typing import List, Optional
from pydantic import BaseModel, Field

from config import settings
from src.llm.client import ModelClient
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
        return ModelClient().generate("curate", prompt)

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
다음은 사용자의 관심사 프로필이다. 노트와 관심사는 사용 경험의 증거가 아니다.
knowledge_depth가 unknown이면 구현 경험이나 전문성을 추정하지 마라.
사용자 공개 경험 기록: {profile.user_context.published_experience}
명시적으로 첨부된 실행 기록 수: {len(profile.user_context.experience_refs)}
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
    "suggested_angle": "원문으로 검증할 수 있는 한 문장의 질문. 사용해 보았다는 경험을 만들지 마라."
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
                    suggested_angle=f"{it.title}의 작동 원리와 대안을 비교하면 어떤 조건에서 채택할 수 있는가?"
                ))

        return curated

    @staticmethod
    def format_telegram_card(topics: List[CuratedTopic]) -> str:
        """Format curated topics into a friendly, structured Telegram briefing message."""
        lines = [
            "🔥 **[오늘의 기술 트렌드 추천 5선]** 🔥",
            "확인된 관심 기록과 수집된 기술 자료를 바탕으로 검토할 주제를 추천합니다.\n"
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

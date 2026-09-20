from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class TrendItem(BaseModel):
    title: str = Field(description="트렌드 항목 제목")
    url: str = Field(description="원문 링크 URL")
    source: str = Field(description="수집 출처 (GeekNews, GitHub, HackerNews, Reddit 등)")
    summary: str = Field(default="", description="기사/레포에 대한 간략한 요약 또는 설명")
    tags: List[str] = Field(default_factory=list, description="관련 태그/언어/카테고리")
    score: int = Field(default=0, description="인기도 점수 (Star수, Upvote수 등)")
    author: Optional[str] = Field(default=None, description="작성자 또는 리포지토리 소유자")
    published_at: Optional[str] = Field(default=None, description="게시 일시")


class BaseCollector(ABC):
    """Abstract base class for all trend collectors."""

    name: str = "base"

    @abstractmethod
    async def collect(self, limit: int = 15) -> List[TrendItem]:
        """Collect trend items asynchronously."""
        pass

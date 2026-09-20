import datetime
import re
from pathlib import Path
from typing import Dict, Any, Optional

from config import settings
from src.curator.matcher import CuratedTopic
from .deep_researcher import DeepResearcher
from .meme_manager import MemeManager


class BlogWriter:
    """Generates humorous, witty, anti-AI developer blog posts in Jekyll format."""

    def __init__(self, blog_repo_path: Optional[Path] = None):
        self.blog_repo_path = blog_repo_path or settings.blog_repo_path
        self.posts_dir = self.blog_repo_path / "_posts"
        self.posts_dir.mkdir(parents=True, exist_ok=True)
        self.researcher = DeepResearcher()
        self.meme_mgr = MemeManager(self.blog_repo_path)

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
                    print(f"[BlogWriter] Gemini {model_name} failed: {e}. Trying next fallback...")



        if settings.openai_api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=settings.openai_api_key)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                print(f"[BlogWriter] OpenAI API call failed: {e}")

        # Fallback template if no LLM key
        print("[BlogWriter] No LLM API key. Using fallback template.")
        return ""

    def _slugify(self, title: str) -> str:
        """Create a clean url-friendly slug."""
        slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
        slug = re.sub(r"[\s_-]+", "-", slug)
        return slug[:50] or "tech-trend-post"

    async def generate_post(self, topic: CuratedTopic) -> Dict[str, Any]:
        """Deep research topic and generate a Jekyll blog post draft."""
        # 1. Deep research
        research_data = await self.researcher.research(topic)

        # 2. Pick memes
        meme1 = self.meme_mgr.get_random_meme()
        meme2 = self.meme_mgr.get_random_meme()
        meme_md1 = self.meme_mgr.format_meme_markdown(meme1)
        meme_md2 = self.meme_mgr.format_meme_markdown(meme2)

        today_str = datetime.date.today().strftime("%Y-%m-%d")
        now_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S +0900")

        prompt = f"""
너는 위트 있고 글 잘 쓰기로 소문난 시니어 개발자 블로거다.
최근 핫한 기술 트렌드인 [{topic.title}]에 대한 심층 분석 블로그 글을 작성하라.

[핵심 글쓰기 규칙 - 절대 엄수!]
1. **문체**: 무조건 **평어체**(`~했다`, `~다`, `~인 셈이다`, `~잖아?`)를 쓴다. 존댓말 경어체는 일체 쓰지 않는다.
2. **탈(脫) AI (Anti-AI) 스타일**:
   - AI 특유의 뻔한 서두("최근 인공지능 기술의 발전으로...", "이번 글에서는 ~를 살펴보겠습니다") 절대 금지!
   - 첫 문장은 현실적인 개발자 시선, 황당했던 경험, 혹은 강렬한 훅(Hook)으로 바로 본론으로 돌진한다.
     (예: "어느 날 평화롭게 깃허브를 떠돌다가 충격적인 레포를 봤다.", "또 새로운 도구가 나왔다. 솔직히 처음엔 '또 뭔 신기술이야' 싶었다.")
   - AI 특유의 상투적 결론("앞으로의 귀추가 주목된다", "우리의 삶을 혁신할 것이다") 절대 금지!
     (대신: "그래서 내 프로젝트에 쓸 거냐고? 솔직히 말하면...", "한 줄 총평: ~다.")
3. **유쾌함과 팩트의 조화**:
   - 실무 개발자가 겪는 현실적 고민, 자조적 유머, 찰진 비유를 곁들인다.
   - 하지만 기술적 알맹이(작동 원리, 아키텍처, 벤치마크, 기존 기술과의 명확한 차이점)는 매우 날카롭고 깊이 있게 설명한다.
4. **시각적 요소 & 짤방 배치**:
   - 글 중간에 `[MEME_1]`과 `[MEME_2]` 플레이스홀더를 각각 1개씩 적절한 문맥에 배치하라.
   - 아키텍처나 구조 설명 시 ````mermaid ... ```` 다이어그램을 적절히 포함하라.
5. **Jekyll Frontmatter**:
   - 문서 맨 위에 아래 Frontmatter를 반드시 포함하라:
---
layout: post
title: "위트 있고 직관적인 한국어 제목"
date: {now_time_str}
categories: [Tech, AI]
tags: [트렌드, 개발, 오픈소스]
---

[참고 자료]
- 주제: {topic.title}
- 출처: {topic.source} ({topic.url})
- 추천 각도: {topic.suggested_angle}
- 수집된 원문 데이터:
{research_data['raw_content'][:3000]}
"""

        generated_raw = self._call_llm(prompt)

        # If LLM didn't return, fallback post
        if not generated_raw or "layout: post" not in generated_raw:
            post_title = f"{topic.title} - 실무 개발자가 뜯어본 솔직 후기"
            post_body = f"""---
layout: post
title: "{post_title}"
date: {now_time_str}
categories: [Tech, Trend]
tags: [개발, 오픈소스, 기술트렌드]
---

오늘도 평화롭게 깃허브를 서핑하다가 눈에 띄는 녀석을 발견했다. 바로 **{topic.title}**이다.

[MEME_1]

## 1. 도대체 뭐 하는 녀석인가?

한 줄로 요약하면 {topic.one_line_summary}인 셈이다.

솔직히 처음엔 '또 새로운 프레임워크야?' 싶었는데, 내용을 까보니 꽤나 흥미로운 구석이 있다.

```mermaid
graph TD
    A[기존 방식의 비효율] --> B[새로운 접근법: {topic.title}]
    B --> C[개발 생산성 향상]
```

## 2. 왜 주목받고 있을까?

- **출처**: [{topic.source}]({topic.url})
- **주요 포인트**: {topic.suggested_angle}

[MEME_2]

## 3. 그래서 내 프로젝트에 쓸만한가?

결론부터 말하자면, 확실히 가려운 곳을 긁어주는 면이 있다. 
다만 늘 그렇듯 은총알은 없으니 내 시스템의 제약 조건을 잘 따져보고 도입을 고민해볼 만하다.

- **원문 링크**: [{topic.url}]({topic.url})
"""
            generated_raw = post_body

        # Replace meme placeholders
        final_content = generated_raw.replace("[MEME_1]", meme_md1).replace("[MEME_2]", meme_md2)

        # Extract title from frontmatter
        title_match = re.search(r'title:\s*"([^"]+)"', final_content) or re.search(r"title:\s*([^\n]+)", final_content)
        title = title_match.group(1).strip() if title_match else topic.title

        # Determine file slug and path
        slug = self._slugify(topic.title)
        filename = f"{today_str}-{slug}.md"
        target_path = self.posts_dir / filename

        # Write post file
        target_path.write_text(final_content, encoding="utf-8")

        return {
            "title": title,
            "filename": filename,
            "file_path": str(target_path),
            "relative_path": f"_posts/{filename}",
            "slug": slug,
            "content": final_content,
            "topic": topic
        }

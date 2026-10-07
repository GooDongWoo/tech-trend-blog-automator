import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.curator.matcher import CuratedTopic
from src.editorial.pipeline import EditorialPipeline

topics = [
    CuratedTopic(
        rank=2,
        title="PageIndex: Vectorless, Reasoning-based RAG 문서 인덱스",
        source="GitHub Trending",
        url="https://github.com/VectifyAI/PageIndex",
        one_line_summary="전통적인 벡터 임베딩 방식에서 벗어나 추론 기반의 문서 인덱싱과 탐색을 지향하는 새로운 형태의 RAG 접근법.",
        relevance_reason="Qdrant 기반 RAG 및 증분 인덱싱 파이프라인 구축, 그리고 형태소 분석 기반 하이브리드 검색을 파고드는 사용자가 벡터 서치의 한계를 극복하는 대안적 아키텍처를 탐색하기에 완벽한 주제입니다.",
        suggested_angle="매번 '벡터를 몇 차원으로 쪼갤 것인가'로 고민하던 RAG 장인들에게 '아예 벡터를 쓰지 말라'고 외치는 발칙한 혁신을 해부해 봅니다."
    ),
    CuratedTopic(
        rank=3,
        title="브라우저 실시간 통신 설계: 폴링·SSE·WebSocket과 상태 복구",
        source="GeekNews",
        url="https://blog.wonkooklee.com/docs/api-and-interfaces/browser-realtime/",
        one_line_summary="단순한 WebSocket 예찬론을 넘어, 채팅·알림·협업 커서 등 상황별 요구사항에 따른 폴링·SSE·WebSocket의 트레이드오프와 상태 복구 설계 가이드.",
        relevance_reason="웹 서버 구조, 네트워크 통신 프로토콜 및 시스템 아키텍처에 관심이 많고, 특히 MCP의 stdio/SSE 전송 방식과 프로토콜 진화 과정을 이해하는 사용자에게 실무적인 통찰을 줍니다.",
        suggested_angle="무조건 WebSocket부터 박고 시작하던 개발 습관을 팩폭하며, SSE와 폴링이 살아남는 이유를 우아한 네트워크 관점에서 짚어봅니다."
    ),
    CuratedTopic(
        rank=4,
        title="OpenID Foundation: 에이전트 AI를 위한 신원 관리 (Identity Management for Agentic AI)",
        source="Hacker News",
        url="https://openid.net/wp-content/uploads/2025/10/Identity-Management-for-Agentic-AI.pdf",
        one_line_summary="자율 에이전트(Agentic AI) 생태계에서 사용자, 서비스, 에이전트 간의 안전한 인증 및 인가를 다루는 표준 아키텍처 백서.",
        relevance_reason="MCP 프로토콜 버전 개정 배경 중 하나인 인증/인가 및 권한 통제 이슈와 직결되어 있으며, 에이전트 아키텍처의 보안 계층을 고민하는 사용자에게 필수적인 레퍼런스입니다.",
        suggested_angle="'내 에이전트가 나인 척 API를 호출하면 어쩌지?' 에이전트 시대의 디지털 신분증과 보안 프로토콜의 미래를 가볍게 털어봅니다."
    ),
    CuratedTopic(
        rank=5,
        title="DietrichGebert/ponytail: 게으른 시니어 개발자처럼 생각하는 AI 에이전트",
        source="GitHub Trending",
        url="https://github.com/DietrichGebert/ponytail",
        one_line_summary="가장 게으른 시니어 개발자처럼 생각하는 AI 에이전트 도구. '가장 좋은 코드는 애초에 작성하지 않은 코드다'라는 철학 구현.",
        relevance_reason="데스크톱 환경에서의 AI 에이전트 자동화 및 도구 연동에 관심이 있는 사용자가, 실용적이고 위트 있는 에이전트 프롬프트/워크플로우 설계 아이디어를 얻기 좋습니다.",
        suggested_angle="일은 안 하고 가성비 좋게 잔머리 굴리는 '초고수 시니어'를 벤치마킹한 AI 에이전트의 뻔뻔한 매력을 파헤쳐봅니다."
    )
]

async def main(*, output_root=None, writer=None, selected_topics=None):
    pipeline = EditorialPipeline(output_root, writer=writer)
    artifacts = []
    for topic in topics if selected_topics is None else selected_topics:
        artifact = await pipeline.generate(pipeline.register_topic(topic))
        artifacts.append(artifact)
        print(f"{artifact.status.value}: {topic.title}")
        print(f"Full draft: {artifact.content_path}")
        print(f"Review report: {artifact.content_path.parent / 'review.md'}")
    return artifacts

if __name__ == "__main__":
    asyncio.run(main())

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

from src.profiler.interest_profiler import InterestProfiler
from src.collector.orchestrator import TrendOrchestrator
from src.curator.matcher import TrendMatcher

async def main():
    profiler = InterestProfiler()
    profile = profiler.build_profile()

    collector = TrendOrchestrator()
    items = await collector.collect_all(limit_per_source=8)

    matcher = TrendMatcher()
    curated = matcher.curate_top_5(profile, items)

    print("\n=== TOP 5 CURATED TOPICS ===")
    for t in curated:
        print(f"[{t.rank}] {t.title}")
        print(f"    Source: {t.source} | URL: {t.url}")
        print(f"    Summary: {t.one_line_summary}")
        print(f"    Reason: {t.relevance_reason}")
        print(f"    Angle: {t.suggested_angle}\n")

if __name__ == "__main__":
    asyncio.run(main())

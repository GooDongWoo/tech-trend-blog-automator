import argparse
import asyncio
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


from config import settings
from src.profiler.interest_profiler import InterestProfiler
from src.collector.orchestrator import TrendOrchestrator
from src.curator.matcher import TrendMatcher
from src.writer.blog_writer import BlogWriter
from src.bot.telegram_bot import TrendBotApp


async def test_pipeline():
    """Run an end-to-end dry-run test without Telegram or Git push."""
    print("=" * 60)
    print("🚀 [Dry-Run] End-to-End Pipeline Test Starting...")
    print("=" * 60)

    # 1. Interest Profiler
    print("\n[1/4] Scanning Obsidian Vault & Profiling Interests...")
    profiler = InterestProfiler()
    profile = profiler.build_profile(days=7)
    print(f" - Core Interests: {profile.core_interests}")
    print(f" - Avoid Topics: {profile.avoid_topics}")
    print(f" - Target Domains: {profile.target_domains}")

    # 2. Collect Trends
    print("\n[2/4] Collecting Multi-Source Trends...")
    collector = TrendOrchestrator()
    items = await collector.collect_all(limit_per_source=4)
    print(f" - Successfully collected {len(items)} items from all sources.")

    # 3. Match & Curate Top 5
    print("\n[3/4] Curating Top 5 Topics via Matcher...")
    matcher = TrendMatcher()
    curated = matcher.curate_top_5(profile, items)
    print(f" - Curated {len(curated)} topics.")
    for t in curated:
        print(f"   [{t.rank}] {t.title} ({t.source})")
        print(f"       Summary: {t.one_line_summary[:80]}...")
        print(f"       Angle: {t.suggested_angle[:80]}...")

    # 4. Generate Blog Post for #1
    if curated:
        top1 = curated[0]
        print(f"\n[4/4] Generating Witty Anti-AI Blog Post Draft for Rank 1: '{top1.title}'...")
        writer = BlogWriter()
        draft = await writer.generate_post(top1)
        print(f" - Draft saved: {draft['relative_path']}")
        print(f" - Title: {draft['title']}")
        print(" - Content Preview (first 300 chars):")
        print("-" * 40)
        print(draft["content"][:300])
        print("-" * 40)

    print("\n✅ Dry-Run Pipeline Test Completed Successfully!")


def main():
    parser = argparse.ArgumentParser(description="Tech Trend Curation & Blog Automator")
    parser.add_argument("command", choices=["bot", "test-pipeline", "send-briefing"], default="bot", nargs="?",
                        help="Run telegram bot, execute pipeline dry-run, or send briefing immediately to Telegram")

    args = parser.parse_args()

    if args.command == "test-pipeline":
        asyncio.run(test_pipeline())
    elif args.command == "send-briefing":
        bot_app = TrendBotApp()
        asyncio.run(bot_app.trigger_briefing())
    elif args.command == "bot":
        bot_app = TrendBotApp()
        bot_app.run()



if __name__ == "__main__":
    main()

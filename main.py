import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

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
from src.editorial.pipeline import EditorialPipeline
from src.bot.telegram_bot import TrendBotApp


async def test_pipeline(*, output_root=None, topics=None, writer=None, user_context=None):
    """Read external sources and retain review artifacts in a temporary local root.

    Collection/research may use configured external APIs; publication and Vault
    writes are absent. Inject topics/writer to run entirely offline.
    """
    root = Path(output_root) if output_root else Path(tempfile.mkdtemp(prefix="blog-review-"))
    pipeline = EditorialPipeline(root, writer=writer)
    print(f"Dry-run review output: {pipeline.store.root}")
    if topics is None:
        profile = InterestProfiler().build_profile(days=7)
        if user_context is None:
            user_context = profile.user_context
        items = await TrendOrchestrator().collect_all(limit_per_source=4)
        topics = TrendMatcher().curate_top_5(profile, items)[:1]
    artifacts = []
    for topic in topics:
        artifact = await pipeline.generate(pipeline.register_topic(topic, user_context=user_context))
        artifacts.append(artifact)
        print(f"{artifact.status.value}: {artifact.content_path}")
        print(f"Review report: {artifact.content_path.parent / 'review.md'}")
    if not artifacts:
        print("NEEDS_RESEARCH: no selected topics")
    return artifacts


def main():
    parser = argparse.ArgumentParser(description="Tech Trend Curation & Blog Automator")
    parser.add_argument("command", choices=["bot", "test-pipeline", "send-briefing"], default="bot", nargs="?",
                        help="Run telegram bot, execute pipeline dry-run, or send briefing immediately to Telegram")

    parser.add_argument("--output-root", type=Path, help="Local review output directory; defaults to a retained temporary directory")
    args = parser.parse_args()

    if args.command == "test-pipeline":
        asyncio.run(test_pipeline(output_root=args.output_root))
    elif args.command == "send-briefing":
        bot_app = TrendBotApp()
        asyncio.run(bot_app.trigger_briefing())
    elif args.command == "bot":
        bot_app = TrendBotApp()
        bot_app.run()



if __name__ == "__main__":
    main()

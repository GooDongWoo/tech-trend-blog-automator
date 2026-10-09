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


def main(argv=None, *, service=None):
    import json
    from src.workflow.service import WorkflowService
    from src.workflow.models import DraftArgumentParser
    parser = DraftArgumentParser(description="Durable evidence-led editorial workflow")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("bot", "test-pipeline"):
        sub = commands.add_parser(name)
        sub.add_argument("--output-root", type=Path)
    for name in ("request", "briefing", "send-briefing", "status", "resume", "select", "review", "revise", "approve", "trial", "publish", "review-legacy"):
        sub = commands.add_parser(name)
        sub.add_argument("--workflow-root", type=Path)
        if name in ("request", "briefing", "send-briefing"):
            sub.add_argument("--mode", choices=("shadow", "reviewed_trial", "production"), default="shadow")
            sub.add_argument("--days", type=int, default=14)
            sub.add_argument("--topic-count", type=int, default=5)
            sub.add_argument("--intent", default="")
        elif name != "review-legacy":
            sub.add_argument("run_id")
        if name in ("revise", "approve", "trial", "publish", "review-legacy"):
            sub.add_argument("draft_id")
            sub.add_argument("content_sha256", help="full SHA-256 from the delivered current review")
        if name in ("request", "select", "review", "revise", "approve", "trial", "publish", "review-legacy"):
            sub.add_argument("--reviewer", required=True, help="stable operator identity, e.g. cli:dongwoo")
        if name == "revise":
            sub.add_argument("--instruction", default="")
            sub.add_argument("--section-id")
        if name == "select":
            sub.add_argument("--rank", type=int, required=True)
        if name == "publish":
            sub.add_argument("--reconcile", action="store_true")
        if name == "review-legacy":
            sub.add_argument("--review-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command in ("bot", "test-pipeline"):
        if args.command == "test-pipeline":
            asyncio.run(test_pipeline(output_root=args.output_root))
        else:
            TrendBotApp().run()
        return 0
    workflow = service or WorkflowService(args.workflow_root)
    def invoke(operation):
        from contextlib import redirect_stdout
        import inspect
        with redirect_stdout(sys.stderr):
            value = operation()
            return asyncio.run(value) if inspect.isawaitable(value) else value
    try:
        if args.command == "request":
            result = invoke(lambda: workflow.request(mode=args.mode, days=args.days, topic_count=args.topic_count, intent=args.intent, reviewer=args.reviewer))
        elif args.command in ("briefing", "send-briefing"):
            result = invoke(lambda: workflow.create_briefing(mode=args.mode, days=args.days, topic_count=args.topic_count, intent=args.intent or 'Telegram topic briefing'))
        elif args.command == "status":
            result = invoke(lambda: workflow.status(args.run_id))
        elif args.command == "resume":
            result = invoke(lambda: workflow.resume(args.run_id))
        elif args.command == "select":
            result = invoke(lambda: workflow.select(args.run_id, args.rank, reviewer=args.reviewer))
        elif args.command == "review":
            result = invoke(lambda: workflow.deliver(args.run_id, reviewer=args.reviewer))
            print(json.dumps({"success": True, "review": result}, ensure_ascii=False))
            return 0
        elif args.command == "review-legacy":
            from src.editorial.store import DraftStore
            result = invoke(lambda: workflow.review_legacy(DraftStore(args.review_root), args.draft_id, args.content_sha256, reviewer=args.reviewer))
            print(json.dumps({"success": True, "review": result}, ensure_ascii=False))
            return 0
        elif args.command == "revise":
            result = invoke(lambda: workflow.revise(args.run_id, args.draft_id, args.content_sha256, reviewer=args.reviewer, instruction=args.instruction, section_id=args.section_id))
        elif args.command in ("approve", "trial"):
            result = invoke(lambda: getattr(workflow, args.command)(args.run_id, args.draft_id, args.content_sha256, reviewer=args.reviewer))
        else:
            result = invoke(lambda: workflow.publish(args.run_id, args.draft_id, args.content_sha256, reviewer=args.reviewer, reconcile=args.reconcile))
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["success"] and result["sync_status"] == "SYNCED" else 1
        print(json.dumps({"success": True, "run": result.model_dump(mode="json")}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"success": False, "status": "BLOCKED", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

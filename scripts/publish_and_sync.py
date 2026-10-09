"""Explicit approved-draft publication command; importing performs no writes."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.editorial.store import DraftStore
from src.publisher.git_publisher import GitPublisher
from src.publisher.obsidian_sync import ObsidianSync


def main(argv=None):
    from src.workflow.models import DraftArgumentParser
    parser = DraftArgumentParser(description="Publish one already approved, reviewed revision")
    parser.add_argument("draft_id")
    parser.add_argument("content_sha256", help="full approved SHA-256 from the review")
    parser.add_argument("--review-root", type=Path, default=Path(__file__).resolve().parents[1] / "temp" / "review")
    parser.add_argument("--workflow-root", type=Path)
    parser.add_argument("--reviewed-trial", action="store_true", help="explicit one-post trial for an already delivered/approved revision")
    parser.add_argument("--reviewer", help="identity from the retained delivered approval")
    parser.add_argument("--reconcile", action="store_true", help="confirm an uncertain push using the remote SHA; never push again")
    args = parser.parse_args(argv)
    if args.reviewed_trial:
        from src.workflow.service import WorkflowService
        try:
            WorkflowService(args.workflow_root).import_trial(DraftStore(args.review_root), args.draft_id, args.content_sha256, reviewer=args.reviewer)
        except (ValueError, OSError) as error:
            print(json.dumps({"success": False, "status": "BLOCKED", "error": str(error)}, ensure_ascii=False))
            return 1
    result = GitPublisher().publish(DraftStore(args.review_root), args.draft_id, args.content_sha256,
                                    sync=ObsidianSync(), reconcile=args.reconcile)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] and result["sync_status"] == "SYNCED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

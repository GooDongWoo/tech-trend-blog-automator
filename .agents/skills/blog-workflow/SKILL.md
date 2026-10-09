---
name: blog-workflow
description: Run this repository's technical blog workflow from natural-language requests for Vault-based Telegram topics, selected-topic drafts, revisions, reviewed publication, and failed-run continuation.
---

Use `main.py` from this repository with its configured Python environment. Read
[the command guide](../../../docs/natural-language-workflow.md) for supported
signatures. Commands return JSON; retain and return the `run.id`, status and
actionable failure reason. Use the same `--workflow-root` on every command.

- “Vault 보고 블로그 주제 텔레그램으로 보내줘”: `briefing --mode shadow --days 14 --topic-count 5 --intent "<user request>"`. This creates and resumes a durable run and sends to the configured Telegram reviewer. `request --reviewer ...` is an optional local inspection route, not the default briefing.
- “2번 주제로 작성해줘”: `select <run_id> --rank 2 --reviewer <run.reviewer>` using the saved candidates. New topics require a new run.
- “계속해줘 / 실패한 작업 이어서”: inspect `status <run_id>`, then `resume <run_id>`. Reuse the saved run and frozen inputs; report unresolved blockers. If the run is ambiguous, resolve its identity before taking action.
- “이 부분을 고쳐줘”: `revise <run_id> <current_draft_id> <current_sha256> --reviewer <run.reviewer> --instruction "<requested edit>"`, adding `--section-id <id>` for a named section. Deliver the new full review; revision invalidates prior delivery/approval.
- “검토 / 승인 / 이 한 편 발행”: `review`, `approve`, then explicitly scoped `trial` and `publish` as described in the guide. The user must see the entire current draft, report and media and explicitly approve its exact ID/hash. Do not approve an unseen revision or treat the original drafting request as publication authorization. A shadow run needs explicit one-post trial intent; never change global shadow/cutover settings.

`--intent` records routing/audit context; it is not a freeform drafting goal.
Preserve the user's requested publication scope and existing authorization.
Production retains its quality/cutover gates. `resume` can retry a previously
reserved publication target, including uncertain push reconciliation or sync
only; approval alone never starts publication. Telegram delivery may repeat if
acceptance precedes the local checkpoint. Never patch providers, invent alternate
external workflows, write directly to the blog/Vault, or run Vault indexing.

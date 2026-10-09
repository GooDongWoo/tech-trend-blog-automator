# Tech Trend Blog Automator

Collect and curate technical topics, then create Korean developer drafts with an inspectable source packet, editorial brief, claim map and validation report. The runtime policy is `src/editorial/policy.md`; `AGENTS.md` is maintenance guidance.

**Telegram runs in shadow mode by default.** It delivers complete draft/report attachments, source links, exact hashes and revision controls. Publication requires exact reviewed approval and either an explicitly scoped one-post trial or the production quality/cutover gates. No production cutover has been performed.

Current implementation, verification boundaries and pending quality work: [refactor status](docs/refactor-status.md). See the [current architecture](docs/architecture.md) and [change log](docs/changelog.md) for the flow and changes.

## Draft and publication workflow

1. Collect topics from GeekNews, GitHub, Hacker News, Reddit, Hugging Face and arXiv, then curate using available user context. Vault notes describe interests and constraints; they do not prove firsthand experience.
2. Fetch bounded source snapshots and retain their hashes and source locations. Missing evidence produces `NEEDS_RESEARCH`.
3. Build a source-grounded brief, generate a mapped draft and validate it. At most two targeted revisions are allowed. Failures produce `NEEDS_REVISION`; no success-shaped fallback article is written.
4. Select optional inspected, permitted media. The bundled historical GIFs are currently excluded because provenance/permission remains unknown.
5. Persist a local review bundle and deliver the complete draft and report in Telegram. Approval binds the reviewed content and bundle hashes, reviewer and revision.
6. After an independently authorized cutover, an approved draft can be published through a separate button. Git push confirmation precedes Vault sync. Vault indexing is never triggered.

The historical `docs/architecture.json`, `docs/architecture.html` and PNG exports describe the pre-refactor route. The JSON metadata and HTML viewer are marked as historical; the PNGs remain preserved records. Use [the current architecture](docs/architecture.md) for the implemented flow and [the design](docs/superpowers/specs/2026-10-07-evidence-led-blog-pipeline-design.md) for the original requirements.

## Installation and configuration

Python 3.11+, Git, a Telegram token and a configured model provider are required for online operation. Offline tests and evaluation use local fixtures without credentials.

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt -r requirements-dev.txt

Copy `.env.example` to `.env` and configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `BLOG_REPO_PATH`, `BLOG_BASE_URL` and `OBSIDIAN_VAULT_PATH`. Group chats also require `TELEGRAM_REVIEWER_USER_ID`. Set `SCHEDULE_TIME` for the daily briefing.

Retain these defaults while evaluating:

    EDITORIAL_SHADOW_MODE=true
    EDITORIAL_CUTOVER_AUTHORIZED=false
    EDITORIAL_QUALITY_GATE_REPORT=

Disabling shadow mode alone does not enable publication. Telegram checks a locally saved evaluation report with real, hash-bound human reviews across multiple topic types and a separate explicit cutover authorization. A passing report is evidence for the operator's decision; it never authorizes that decision by itself. The bundled synthetic evaluation can never pass this gate.

The shared CLI/Telegram authorization requires the current delivered draft ID/hash and reviewer. Shadow runs cannot publish. Ordinary evaluation must use temporary paths. After the user sees and explicitly approves the full review, a separately authorized one-post trial can elevate that exact run/revision through `trial`. This does not authorize production cutover or waive research, validation, manifest/hash or reviewer checks.

For that separately authorized trial only:

    python main.py trial RUN DRAFT SHA --reviewer REVIEWER
    python main.py publish RUN DRAFT SHA --reviewer REVIEWER

An explicitly scoped trial can write the configured blog and Vault while global shadow remains enabled. Confirmed Git push precedes Vault sync; indexing is never triggered. Existing legacy reviews need explicit `review-legacy` delivery before scoped workflow authorization; see the [command guide](docs/natural-language-workflow.md).

## Usage

    python main.py bot
    python main.py send-briefing
    python main.py test-pipeline

`/now` and `/trend` request a briefing. Selecting a topic sends a complete local review bundle. Review approval records the exact draft; it performs no Git or Vault write. Regeneration supersedes the old approval button.

For natural-language requests use the discoverable repository [blog-workflow skill](.agents/skills/blog-workflow/SKILL.md). “Vault 보고 주제 텔레그램으로 보내줘” maps to `briefing --mode shadow`; “계속해줘” resumes the saved `run_id`. The [workflow guide](docs/natural-language-workflow.md) covers selection, directed section revision, user-visible full review, exact approval, trial/publication and durable failure recovery. `--intent` records routing context, not an unrestricted writing prompt.

The shared configurable `OPENAI_MODEL` default is `gpt-4o-mini` (the writer formerly used `gpt-4o`); `LLM_FALLBACK_MODELS` explicitly names any fallbacks. This states configuration, not live model availability.

`test-pipeline` performs online collection/model work unless dependencies are explicitly injected; its draft output defaults to a temporary review directory. It is not the offline test command.

## Offline evaluation and tests

    python -m pytest -q
    python -m scripts.evaluate_drafts --output temp/evaluation-example

The output directory must be new/empty and outside configured blog/Vault paths. Results include `report.json`, a private variant key, and an anonymous `blind/` packet with draft/source files and a pending human score sheet. Share only the blind directory before scoring. Agents are not human raters.

**All eight historical baseline topics lack their frozen original source inputs, original model responses, prompts and budgets.** Stagehand, ai-memory, GAVEL, cua, ponytail, OpenID Foundation, PageIndex and browser communication are marked unreproducible for all four variants. No same-source historical ablation or article-quality uplift is claimed. Current pages must not be substituted for historical inputs.

The bundled synthetic queue fixture replays four stage labels: old one-shot output, packet output, packet plus validator, and full drafting/validator/targeted-repair flow. It is a hand-authored diagnostic, with no model call. It tests harness behavior only; one-shot/packet generations are not reexecuted, and full replay omits media. Real captures require declared matching model/budget, source/output/prompt hashes, capture metadata, and exact prompt matching for full replay.

The model-only report records unsupported-claim spans, first-person spans without logs, mapped-source coverage and issue codes. Human scores, meme fit and cost remain **not measured**. Durable `workflow_receipt` summaries separately expose captured SDK tokens/call time when available and operator interventions; missing or synthetic measurements stay null. Offline replay time measures local Python work only. Source mappings are not factual verification; a first-person span without a log is not proof that a real author fabricated an experience.

Release remains blocked until real blinded human review supplies zero critical defects and mean technical depth and decision clarity of at least 4/5 for every included topic type, with required-type coverage enforced separately. IDs must be unique and reviews bind valid SHA-256 hashes; unbound or duplicate records fail the gate. See [evaluation evidence, rubric and recovery](docs/editorial-evaluation.md). The temporary Git/fake-push/temporary-Vault rehearsal in `tests/test_publish_workflow.py` exercises publication mechanics only.

## Recovery and rollback

Keep shadow mode enabled when research, validation or human review is incomplete. Correct the evidence/brief or regenerate a new review revision; never edit an approved bundle in place. A failed push does not sync the Vault. An uncertain push requires explicit remote-SHA reconciliation, and a failed Vault sync can be retried without another push.

There is no implemented switch back to the old one-shot Telegram route. Operational rollback means stopping the bot and restoring an explicitly reviewed prior application revision after a separate operator decision. Old-route output must be labeled unverified; rollback does not waive review or publication approval.

## Privacy

`.env` and local review artifacts are ignored. Source text is untrusted input; model calls use the versioned runtime policy, public source snapshots and permitted user context. Local Vault contents must not be promoted to public experience claims.

# Natural-language Blog Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Run and resume the approved Vault-to-Telegram blog workflow through structured commands invoked from natural-language requests.

**Architecture:** Extend the existing JSON draft store with durable workflow runs and a common publication authorization boundary. Keep providers, primary research and independent grounding as bounded adapters behind the existing pipeline. Use a repository skill for natural-language intent mapping.

**Tech Stack:** Existing Python, Pydantic, httpx, Google/OpenAI SDKs, python-telegram-bot and Git; no new queue/database framework.

**Spec:** docs/superpowers/specs/2026-10-09-natural-language-workflow-design.md

## Global Constraints

- No live blog/Vault/Telegram writes during implementation tests. Offline fixtures and injected adapters only; never run Vault indexing.
- Missing/contradicted evidence remains NEEDS_RESEARCH or NEEDS_REVISION. No outage prose, invented personal experience, automatic human ratings or claimed real quality uplift.
- Preserve complete raw source snapshots, URL/hash/location provenance, versioned runtime policy, exact approval hashes, manifest integrity, publisher receipts and duplicate-push recovery.
- Default request: Vault interests, five Telegram candidates and user selection. An unseen draft is never approved by a request to create/publish a post.
- Reuse existing stores and SDKs; do not add a generic queue or database framework. Each task writes an implementation report and an atomic commit; no push/merge.
- Worktree: C:/Users/dongwoo/.codex/worktrees/evidence-blog-pipeline/tech-trend-blog-automator. Python: .venv/Scripts/python.exe.
- Run focused meaningful tests first. A final full suite verifies integration. Record unmeasured API/model quality separately from offline correctness.

### Task 1: Bounded shared model calls

**Files:** Create src/llm/client.py, src/llm/__init__.py, tests/test_llm_client.py. Modify config.py, .env.example, src/profiler/interest_profiler.py, src/curator/matcher.py and src/writer/blog_writer.py.

**Interfaces:** Export `ModelClient.generate(stage: str, prompt: str, *, max_output_tokens: int = 4096, artifact_dir: Path | None = None) -> str` and `ModelCallError`. Constructor accepts injectable transport and clock/sleep for offline retry tests. Existing `_call_llm` hooks delegate here so existing fake tests remain usable. Config adds positive request/stage timeouts, bounded attempts and explicitly configured fallback model list. No hardcoded outdated fallback chain.

- [x] Add a failing test for transient retry then success, deadline exhaustion, nonretryable 404, redacted captures and output limits using an injected transport.
```python
def test_nonretryable_error_does_not_iterate_fallbacks(fake_transport):
    fake_transport.fail(code=404)
    with pytest.raises(ModelCallError):
        ModelClient(transport=fake_transport).generate('draft', 'input')
    assert fake_transport.calls == 1
```
- [x] Observe the targeted failure, implement the adapter with SDK retries disabled and remaining-time request limits, and remove duplicated provider loops.
- [x] Validate profiler/matcher/writer compatibility and safe degraded diagnostics; report commands/results and commit the task files.

### Task 2: Primary research and located experiment evidence

**Files:** Modify src/writer/deep_researcher.py, src/research/fetch.py, src/research/extract.py, src/research/packet.py, src/editorial/models.py, src/editorial/brief.py and the researcher construction in src/writer/blog_writer.py. Create tests/test_primary_research.py and a focused minimal first-trial fixture with provenance notes. Existing research/brief tests remain relevant.

**Interfaces:** DeepResearcher retains `async research(topic) -> dict` and its artifact_dir. Optional injected model follows the Task 1 generate contract through a narrow adapter. Source links and explicit extraction/experiment metadata are additive contract fields. Export deterministic located-excerpt/metric verification helpers for Task 3. The result retains status/packet/reasons and reports source-following gaps. Preserve existing packet constructors and source fixtures.

- [x] Write failing offline tests: HF landing page follows its real paper link; no invented repository links; bounded cycles/fetch count; PDF experimental setup/table result excludes unrelated appendix prompts; two studies cannot share setup; reported success-rate compound results are split with located context.
```python
async def test_abstract_only_source_follows_primary_document(fake_fetcher):
    result = await DeepResearcher(fetcher=fake_fetcher).research(selected_topic())
    assert result['status'] == 'RESEARCH_READY'
    assert any(s.kind == 'pdf' for s in result['packet'].sources)
    assert fake_fetcher.calls == expected_inspected_links()
```
- [x] Implement primary traversal, faithful extraction normalization and bounded literal evidence selection. Model extraction proposals require exact located source spans and numeric/context verification; unresolved proposals block.
- [x] Populate paper study fields from actual reported experiment evidence without false 'not reported' warnings caused solely by heading spelling. Retain genuinely missing fields and explicit uncertainty.
- [x] Validate research/brief/integration tests and commit task files with an implementation report.

### Task 3: Natural Korean prose and independent grounding

**Files:** Modify src/editorial/policy.md, src/editorial/models.py, src/editorial/draft.py, src/editorial/validate.py and src/writer/blog_writer.py. Create src/editorial/grounding.py and tests/test_korean_grounding.py; update version-dependent existing drafting fixtures/tests deliberately.

**Interfaces:** Independent grounding judgments live outside DraftPayload and bind sentence/content/evidence hashes and verdict supported/contradicted/unknown. `validate_draft` remains usable for literal offline fixtures; Korean paraphrases require a separately generated bound judgment, never a model's embedded self-attestation. `write_draft` accepts an optional independent reviewer and continues returning DraftText/report. Persist reviewer captures through the actual model adapter and result bundle.

- [x] Observe failures for faithful Korean paraphrase, contradicted translation, fabricated number, fabricated experience, stale review binding and model-supplied verdict. Add a repair test whose full draft exceeds revision context budget but whose implicated section fits.
```python
def test_changed_sentence_invalidates_semantic_support(bound_review):
    draft = faithful_korean_draft().model_copy(update={'sections': changed_sections()})
    assert 'stale_grounding_review' in issue_codes(validate_with(draft, bound_review))
```
- [x] Version the runtime policy and implement conservative structured semantic review plus deterministic numeric/experience/source-span guards. Natural inference wording and varied nonassertive headings must not require English token overlap or a repeated '추론:' prefix. Unknown or contradicted review blocks.
- [x] Construct/protect exact sentence spans in code; use section-scoped revision evidence and preserve unaffected bytes. Revalidate any readability/wit edit before optional permitted media; no mandatory GIF or scene explanation.
- [x] Add safe reported-result arithmetic only when operands, units, comparison conditions and provenance are verified; label derived differences separately from original measurements, or block unavailable comparisons.
- [x] Run drafting/claim/context/pipeline/evaluation regressions, report verification boundaries and commit task files.

### Task 4: Durable workflow, CLI, Telegram and publication authorization

**Files:** Create src/workflow/models.py, src/workflow/store.py, src/workflow/service.py, src/workflow/publication.py and tests/test_workflow_runs.py, tests/test_workflow_publication.py. Modify main.py, src/bot/telegram_bot.py, scripts/publish_and_sync.py and src/publisher/git_publisher.py only where needed to enforce shared authorization. Reuse existing DraftStore/publisher primitives.

**Interfaces:** Structured CLI request/status/resume/select/revise/approve/publish operations delegate to WorkflowService. Request(mode='shadow'|'reviewed_trial'|'production', days=14, topic_count=5) creates a stored run; approve requires draft_id and full content_sha256 and reviewer identity. Stored one-post trial permission is run/draft/hash-bound. Telegram and CLI call this same service; old unscoped live CLI publication must not evade the mode policy. Explicit legacy operator trial flag may create a scoped run for a previously reviewed artifact, retaining identity/hash checks.

- [x] Write failing injected tests for restart after each stage, duplicate selection/revision/approval/delivery/publication, stale hash, unauthorized reviewer, trial limited to one draft, shadow denial, production quality/cutover denial, failed push reconciliation and sync-only retry.
```python
async def test_resume_does_not_repeat_completed_collection(service, adapters):
    run = await service.request(mode='shadow')
    await service.resume(run.id)
    assert adapters.collect.calls == 1
```
- [x] Implement atomic checkpoints and a process-safe lock with crash recovery; do not hold asynchronous bot polling on synchronous provider work. Full draft/report delivery precedes approval. Approvals bind the current delivered revision; changing a draft invalidates the previous approval.
- [x] Centralize publication policy before every live entry point while preserving exact Git push/receipt safeguards and independent sync recovery. Avoid duplicate pushes and falsely successful remote state.
- [x] Record redacted stage events, model usage references, time and operator intervention. Resume frozen completed inputs rather than silently recollecting different sources. Allow explicit new runs for refresh.
- [x] Run focused workflow and existing publication/context tests, write the task report and commit.

### Task 5: Natural-language skill, failure replay and documentation

**Files:** Create .agents/skills/blog-workflow/SKILL.md, docs/natural-language-workflow.md and tests/test_workflow_replay.py. Modify AGENTS.md, README.md, docs/architecture.md, docs/changelog.md, docs/refactor-status.md and scripts/evaluate_drafts.py only where needed for honest run metrics/replay.

**Interfaces:** Skill translates supported natural-language intent into the Task 4 CLI, returns/resumes run_id and preserves user's publication scope. Evaluation captures real run receipts and interventions separately from model-only output; existing blind human review/report gates remain.

- [x] Add an offline end-to-end restart/replay test using frozen minimal first-trial inputs. Verify artifact identity, final review delivery, approval, one publication and independent sync retry without network/live writes.
```python
def test_operator_edits_are_not_counted_as_automatic_quality(replayed_run):
    assert replayed_run['operator_interventions'] > 0
    assert replayed_run['human_scores'] is None
```
- [x] Document requests for Vault briefing, selected-topic drafting, exact revision approval/publication and failed-run resume; no invented commands or temporary provider patches. Record the first trial's actual Git push and SYNCED receipt and its manual interventions.
- [x] Keep comparison/human ratings pending unless genuine captures exist. Expose time, usage, unsupported claims and intervention counts without claimed quality uplift. Complete docs and focused verification, commit and deliver a reviewable branch.

## Final gate

- [x] Review each task for spec compliance and code quality; record resolutions in the plan-scoped ledger.
- [x] Run full offline suite including temporary Git publication/recovery tests, inspect exit code and count.
- [x] Run a structured CLI help/status/request/resume rehearsal with injected/local-only adapters, review changed files and check secrets/whitespace.
- [x] Report implementation, verification, branch and live/human-quality boundaries. No live publication, merge or push is part of this implementation request.

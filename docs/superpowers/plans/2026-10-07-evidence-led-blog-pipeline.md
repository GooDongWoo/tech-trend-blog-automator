# Evidence-Led Tech Blog Pipeline Implementation Plan

> **For agentic workers:** Implement one logical unit at a time, with a review gate after each unit. Do not publish or write to the Obsidian Vault during implementation. The `subagent-driven-development` skill is optional only when the user explicitly requests delegation; inline execution is valid.

**Goal:** Produce Korean technical blog drafts whose mechanisms, quantitative claims, decisions, and optional humor are grounded in inspectable evidence.

**Architecture:** Replace the one-shot `DeepResearcher -> BlogWriter` path with typed source, evidence, brief, draft, and validation artifacts. Keep the current collectors, topic cards, Jekyll output, Telegram review, Git publishing, and Vault sync, but connect publishing to a validated immutable draft ID rather than a process-wide last draft.

**Tech Stack:** Python 3.13-compatible code, Pydantic 2, existing `httpx`/BeautifulSoup, existing Gemini/OpenAI adapters, pytest for offline tests. Choose a PDF text extractor only after checking the repository's actual sample PDFs and package support at implementation time.

**Spec:** [근거 중심 기술 블로그 자동화 설계](../specs/2026-10-07-evidence-led-blog-pipeline-design.md)

## Global Constraints

- All tests use local fixtures, fake LLM responses, temporary blog repositories, and temporary Vault directories; tests never call live APIs, push, or index the Vault.
- `AGENTS.md` guides Codex maintenance; the running Python pipeline must load its own versioned editorial policy. Do not assume `AGENTS.md` is present in LLM calls.
- A failed fetch, LLM response, or validation returns a visible blocked status. Never write the current fallback article template as a successful draft.
- Direct experience is allowed only from explicitly supplied, inspectable run logs. Vault notes and a model's prose are not such proof.
- The user approves a specific reviewed draft before any Git or Vault write. No automatic Vault incremental indexing.
- Existing published posts are read-only evaluation examples unless a separate edit is requested.

## File map

| Responsibility | Files |
| --- | --- |
| Editorial policy and maintainer rules | `src/editorial/policy.md`, `AGENTS.md` |
| Typed evidence and draft contracts | `src/editorial/models.py` |
| Source extraction and claim preparation | `src/research/fetch.py`, `src/research/extract.py`, `src/research/packet.py` |
| Personal context with provenance | `src/profiler/interest_profiler.py`, `src/profiler/rag_checker.py`, `src/profiler/daily_scanner.py` |
| Brief, drafting, validation | `src/editorial/brief.py`, `src/editorial/draft.py`, `src/editorial/validate.py` |
| Optional, accurate meme selection | `src/editorial/media.py`, `assets/memes/catalog.json` |
| Durable draft state and workflow | `src/editorial/store.py`, `src/editorial/pipeline.py` |
| Existing entry points and publication | `src/writer/blog_writer.py`, `src/bot/telegram_bot.py`, `src/publisher/git_publisher.py`, `src/publisher/obsidian_sync.py`, `main.py` |
| Evaluation | `tests/fixtures/`, `tests/`, `scripts/evaluate_drafts.py`, `docs/editorial-evaluation.md` |

The exact split can be reduced if two files remain small and change together. Do not copy the Gemini/OpenAI retry loop into new modules: extract one injected LLM client when Task 2 first needs it.

## Dependency order

`Unit 0` → `Unit 1` → `Unit 2` → `Unit 3` → `Unit 4` → `Unit 5` → `Unit 6` → `Unit 7` → `Unit 8`. Unit 0 establishes a baseline; each later unit has its own independently testable gate. A failing gate blocks dependent units. After Units 5 and 6, end-to-end generation can already create a reviewable local draft without publication.

### Unit 0: Freeze baseline and create an offline test harness

**Files:** Add `pytest` to the dev dependency group or a dedicated `requirements-dev.txt`; create `tests/fixtures/`, `tests/test_current_pipeline.py`, `docs/editorial-evaluation.md`.

- [ ] Record 8–12 existing blog posts as a read-only baseline manifest with path, topic type, and evaluation date. Include PageIndex, Stagehand, and the OpenID Foundation article. Do not silently modify these posts.
- [ ] Build tiny HTML, README, PDF, LLM-response, and local GIF fixtures. The fixtures must contain known claim locations and one unsupported quantitative claim.
- [ ] Write tests that characterize current failure modes: PDF content is missing, empty LLM output creates a template, two memes are inserted without topic matching, and publish can be attempted after a 500-character preview. Mark these as expected current behavior until their owning unit changes them.
- [ ] Define a review sheet for technical depth, claim provenance, decision clarity, naturalness, and meme fit. Measure baseline values before changing prompts.

**Gate:** `python -m pytest tests/test_current_pipeline.py -q` passes offline and a baseline review sheet contains values rather than assumed scores. The test command must not create files in the real blog repository.

### Unit 1: Define evidence and state contracts

**Files:** Create `src/editorial/models.py`, `tests/test_editorial_models.py`; update `src/curator/matcher.py` only if needed to carry stable topic identity.

**Interfaces:** `SourceRecord(url, fetched_at, title, kind, sha256, text, locations, error)`; `EvidenceClaim(text, kind, source_refs, metric_context, status)`; `ResearchPacket(topic_id, question, sources, claims, gaps)`; `UserContext(goals, constraints, interests, experience_refs)`; `EditorialBrief(topic_id, post_kind, thesis, comparison, decision_criteria, reversal_conditions)`; `DraftArtifact(id, topic_id, content_path, content_sha256, evidence_path, report_path, status, media_paths)`.

- [ ] Add Pydantic validation for source references, claim kinds, measurements, and legal state transitions. A measurement without baseline, unit, or source/run record is invalid.
- [ ] Add serialization round-trip tests and a transition test: `NEEDS_RESEARCH` cannot become `APPROVED`; `REVIEW_READY` can become `APPROVED` only after its content hash is checked.
- [ ] Introduce stable identifiers based on canonical topic URL plus source snapshot, avoiding a title-derived ID.

**Gate:** `python -m pytest tests/test_editorial_models.py -q` passes and the models reject a numerical claim with no provenance. No current entry point behavior changes yet.

### Unit 2: Extract primary sources without discarding provenance

**Files:** Create `src/research/fetch.py`, `src/research/extract.py`, `src/research/packet.py`, `tests/test_research.py`; retire direct scraping inside `src/writer/deep_researcher.py` after consumers move.

**Interfaces:** `fetch_source(url: str) -> SourceRecord`; `extract_sections(source: SourceRecord) -> list[Section]`; `build_packet(topic: CuratedTopic, sources: list[SourceRecord]) -> ResearchPacket`.

- [ ] Preserve full extracted text in a local source snapshot; apply a relevance-based context budget to the LLM prompt instead of `raw_content[:3000]` and `lines[:120]`.
- [ ] Fetch a GitHub README, ordinary HTML, and PDF as distinct formats. For PDF, extract page text with page references; if no reliable text exists, return `NEEDS_RESEARCH` with the reason `unreadable_pdf`.
- [ ] Capture original URL, final URL, retrieval time, content hash, and section/page references. Prefer primary documents and attach secondary reporting only as context.
- [ ] Treat HTTP failures, blocked pages, and partial extraction as failures with diagnostics; never substitute `topic.one_line_summary` for source text.
- [ ] Add fixture tests for all three formats, redirect, missing PDF text, and a source that contains conflicting claims.

**Gate:** `python -m pytest tests/test_research.py -q` passes offline. A packet for the PDF fixture contains a verifiable page reference; a blank PDF produces `NEEDS_RESEARCH`.

### Unit 3: Make the editorial decision before drafting

**Files:** Create `src/editorial/brief.py`, `src/editorial/policy.md`, `tests/test_brief.py`, `tests/test_user_context.py`; update `src/curator/matcher.py`, `src/profiler/interest_profiler.py`, and `src/profiler/rag_checker.py`.

**Interfaces:** `build_brief(packet: ResearchPacket, user_context: UserContext) -> EditorialBrief | ResearchBlocked`.

- [ ] Define four post kinds: paper/benchmark, library/tool, protocol/standard, and design comparison. Choose the kind from source material, not from a generic fixed heading template.
- [ ] Require a one-sentence question, thesis, comparison alternative, key mechanism, adoption constraints, and reversal condition. For paper posts, capture study dataset, baseline, metrics, ablation, and limitations only when the paper actually provides them.
- [ ] Distinguish user interests from user experience. A Vault note may inform the comparison criteria, but first-person claims require an explicitly attached run log or user-authored statement marked for publication.
- [ ] Build `UserContext` from traceable user statements and Vault note references. When the daemon is unavailable, label a local search as a local search; do not report it as RAG evidence. Remove the static fallback claiming specific expertise and return an explicit `unknown` depth where evidence is absent.
- [ ] Refuse a brief whose proposed thesis depends on an unsupported claim. Return a short list of missing facts instead of prose.
- [ ] Write tests for the four post kinds, an insufficient-evidence packet, daemon-unavailable context, and a profile with no proof of hands-on experience.

**Gate:** `python -m pytest tests/test_brief.py tests/test_user_context.py -q` passes. A protocol post can pass without an ablation table; a benchmark post cannot repeat a bare percentage without its context; missing profile data never becomes an invented first-person story.

### Unit 4: Draft from the brief, then account for every claim

**Files:** Create `src/editorial/draft.py`, `src/editorial/validate.py`, `tests/test_drafting.py`, `tests/test_claim_validation.py`; simplify `src/writer/blog_writer.py` into an adapter for the new pipeline.

**Interfaces:** `write_draft(brief: EditorialBrief, packet: ResearchPacket, llm: LLMClient) -> DraftText`; `validate_draft(text: DraftText, packet: ResearchPacket, brief: EditorialBrief) -> ValidationReport`.

- [ ] Version the runtime editorial policy and inject it into the drafting and revision prompts. Add an `AGENTS.md` section instructing maintainers to update the runtime policy and tests when editorial requirements change.
- [ ] Ask the LLM for a claim map alongside prose: each core technical sentence points to evidence IDs or is marked as an inference. Generated citations must resolve to packet references.
- [ ] Require the body to explain one concrete mechanism, a meaningful alternative, and the conditional decision. Do not force arbitrary headings, a Mermaid diagram, a code block, a number, or a meme.
- [ ] Reject invented personal experience, unverifiable numerical comparisons, placeholder citations, and confident claims absent from the packet. Revise only the implicated section, with at most a small fixed retry count; then remain `NEEDS_REVISION`.
- [ ] Remove the current success-shaped fallback template. LLM outages leave the source packet and failure reason intact.
- [ ] Add fixture tests for unsupported claims, correct attribution to an original author, valid first-person run log, failed LLM response, and accurate Jekyll frontmatter.

**Gate:** `python -m pytest tests/test_drafting.py tests/test_claim_validation.py -q` passes. The PageIndex fixture cannot produce “직접 써보니” without supplied run evidence.

### Unit 5: Make humor optional and verify media content

**Files:** Create `assets/memes/catalog.json`, `src/editorial/media.py`, `tests/test_media.py`; remove random selection and duplicate caption formatting from `src/writer/meme_manager.py` after migration.

**Interfaces:** `choose_media(brief: EditorialBrief, draft: DraftText, catalog: MemeCatalog) -> MediaChoice | None`; `render_media(choice: MediaChoice) -> str`.

- [ ] Inspect existing local GIFs and record only verifiable visible content, source/use notes, suitable context, and factual alt text. If an asset cannot be confidently described or used, exclude it from the catalog.
- [ ] Select 0 or 1 relevant meme after the argument is written. Use an explicit `None` path as the normal outcome. Keep the same deterministic fixture input stable across test runs.
- [ ] Add a validator that rejects duplicate captions, “짤 설명” prose, invented quotes/scenes, missing local assets, and excessive media count.
- [ ] Prefer a real diagram, code fragment, or result table when it carries the technical point better than a GIF.

**Gate:** `python -m pytest tests/test_media.py -q` passes. An unrelated protocol article gets no meme, and a chosen GIF has accurate alt text with no explanatory caption.

### Unit 6: Persist and review a complete draft artifact

**Files:** Create `src/editorial/store.py`, `src/editorial/pipeline.py`, `tests/test_pipeline.py`; modify `src/bot/telegram_bot.py`, `main.py`, and `scripts/generate_selected.py` to call the pipeline.

**Interfaces:** `generate(topic_id: str) -> DraftArtifact`; `get_draft(draft_id: str) -> DraftArtifact`; `approve(draft_id: str, expected_sha256: str) -> DraftArtifact`.

- [ ] Store each run's source snapshots, packet, brief, Markdown, validation report, and status under a stable local draft ID. Preserve failed artifacts for diagnosis without placing them in Jekyll `_posts`.
- [ ] Show the Telegram reviewer the full local draft path, provenance summary, unresolved issues, and one-click source links. A 500-character teaser alone is insufficient for approval.
- [ ] Bind callback data to draft ID and content hash, not `last_draft`. A retry creates a new revision; an older approval button cannot publish the newer file.
- [ ] Make `test-pipeline` a read-only dry run for external systems: explicitly select a temporary output root or require a flag to write into the actual blog repo.
- [ ] Test two simultaneous drafts, bot restart, retry, stale approval, and blocked validation using fake bot and local storage.

**Gate:** `python -m pytest tests/test_pipeline.py -q` passes. Generating a draft never changes the real blog repo or Vault, and stale approval is rejected.

### Unit 7: Make publication an explicit, recoverable transition

**Files:** Modify `src/publisher/git_publisher.py`, `src/publisher/obsidian_sync.py`, `src/bot/telegram_bot.py`; add `tests/test_publish_workflow.py`. Retire or rewrite `scripts/publish_and_sync.py` so it cannot bypass validation.

- [ ] Validate `APPROVED` status, content hash, destination repository, and allowed paths immediately before copying the article and referenced assets to Jekyll.
- [ ] Stage only the article and assets belonging to that draft. Check the Git result and return the commit SHA or a specific error. Never report success after a failed push.
- [ ] Run Obsidian sync only after push succeeds. If Vault sync fails, keep `PUBLISHED` plus a distinct `SYNC_FAILED` note and allow idempotent retry without another push.
- [ ] Review changes in a temporary Git repo and temporary Vault. Assert that no unrelated staged files enter the commit and that a duplicate approval creates no second post.
- [ ] Remove hard-coded historical topics and direct push behavior from `scripts/publish_and_sync.py` or turn it into a validated draft-ID command.

**Gate:** `python -m pytest tests/test_publish_workflow.py -q` passes with fake Git failures and temp repositories. Real Git push and Vault writes remain unexercised until a separately authorized end-to-end rehearsal.

### Unit 8: Evaluate, ablate, and roll out in shadow mode

**Files:** Create `scripts/evaluate_drafts.py`, update `docs/editorial-evaluation.md`, `README.md`, and selected offline fixtures; add `tests/test_evaluation.py`.

- [ ] Generate new drafts for the baseline topics with frozen source snapshots. Compare current and new outputs under the same declared model, input sources, and budget; note any runs that cannot be reproduced.
- [ ] Run a small pipeline ablation on the same topics: current one-shot flow; evidence packet only; evidence packet plus validator; full flow. Track claim errors, false first-person, source coverage, human scores, token/time cost, and meme fit. The goal is diagnosis, not statistical significance.
- [ ] Have a human review blind to variant labels. Use the rubric in the spec. Do not treat a model grader as the sole judge of style or truth.
- [ ] Enable shadow mode for the Telegram route: show the new draft and report without allowing its publication until the quality gate passes on multiple topic types.
- [ ] Accept rollout only when critical defects are 0 in the evaluation set and mean technical depth and decision clarity are each at least 4/5. If one type underperforms, keep it blocked and improve its source/brief path.
- [ ] Document operational recovery, cost per draft, and how to revert the bot to the old route without labeling old-route output as verified.

**Gate:** `python -m pytest -q` passes offline; evaluation report lists raw counts and per-topic judgments; one reviewed article has been rehearsed in a temporary blog repo. A production cutover is a separate user decision after reviewing this evidence.

## Review checkpoints

After every unit, inspect the diff, run that unit's gate, and record what was proved and what remains unverified. Avoid a single giant merge. Units 0–4 establish editorial truthfulness; Units 5–7 establish presentation and safe operation; Unit 8 decides whether the new pipeline is actually better. If the old and new flows tie on quality, prefer the cheaper and simpler one.

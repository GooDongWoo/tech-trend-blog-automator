# Editorial baseline — 2026-10-07

This baseline was recorded before changing prompts or production behavior. The eight
published Markdown files in `tests/fixtures/baseline-manifest.json` were read only.
The manifest records absolute and repository-relative paths, topic types, evaluation
date, SHA-256 hashes, and reproducible text counts. Do not edit those posts as part
of this migration. The test suite never needs access to that real repository.

## Method and review sheet

Reviewer: Codex editorial inspection of the complete local Markdown on 2026-10-07.
These are actual reviewer ratings, not automated truth scores or a blinded human
study. No primary-source retrieval, experience logs, link availability checks, or
inspection of the original GIF scenes was performed. A claim lacking an inspectable
source is **unverified**, not established false. Scene explanations described below
are unsupported by the review material; the original media content remains unverified.

Use integer scores from 1 to 5, with the following anchors. Intermediate scores
represent partial fulfillment of the neighboring anchors.

| Dimension | 1 | 3 | 5 |
| --- | --- | --- | --- |
| Technical depth | Slogans without mechanism | Mechanism and limitations, some implementation detail | Traceable API/code or protocol flow, comparison conditions, failure cases |
| Claim provenance | No inspectable source links or measurement record | Links to source with incomplete claim locations/conditions | Every material claim maps to source location, log, or marked inference |
| Decision clarity | Blanket adoption advice | Conditional use cases and tradeoffs | Alternatives, operational constraints, and evidence that could reverse the choice |
| Naturalness | Forced persona, repetitive hook/conclusion | Readable with occasional stock phrases | Direct prose; voice serves the technical explanation |
| Meme fit | Invented scene/dialogue or unexplained insertion | Relevant context but generic caption/alt | Verified scene, accessible alt, deliberate placement (including appropriate omission) |

For each review record: draft ID/hash, source packet ID, model/budget, reviewer/date,
five scores, supporting passage/section, unverified core claims, untraceable core
numbers, experience passages without logs, unsupported scene explanations, broken
media/links, cost, elapsed time, and unresolved questions. Use `not measured` rather
than zero when evidence is unavailable. Distinguish hypothetical examples, version
numbers, and illustrative arithmetic from measured performance claims.

## Recorded baseline scores

Keys below identify exact paths and frozen hashes in the manifest.

| Post key | Type | Depth | Provenance | Decision | Naturalness | Meme fit | Evidence behind rating |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Stagehand | library/tool | 3 | 1 | 2 | 2 | 2 | `observe/act/extract` examples and limitations add detail; “2배/80%” lacks a source link and conditions; final “그냥 이거 써라” overrides qualifications; opening invoice narrative has no supplied log; generic two captions. |
| ai-memory | library/tool | 3 | 1 | 3 | 2 | 1 | Markdown/SQLite/hooks architecture and privacy discussion; zero links for Rust/version and integration claims; heavy multi-tool users are a choice boundary; two conflicting unsupported scene descriptions for the same GIF path. |
| GAVEL | paper/benchmark | 2 | 1 | 3 | 2 | 1 | Graph/planner diagram but no paper location, experiment table, measured baseline, or ablation; graph construction cost/domain boundaries help the decision; two scene explanations have no supporting media record. |
| cua | library/tool | 3 | 1 | 3 | 2 | 1 | Driver/fleet/Lume flow and isolation tradeoff; platform/S1 claims have no linked source; local trial versus production is explicit; unverified “This is fine” scene accompanies a different named asset. |
| Browser communication | design comparison | 3 | 1 | 4 | 2 | 1 | Polling/SSE/WebSocket and recovery options are explained; three selection cases make the decision clearer; “90%의 서비스” has no source; novice PR story has no supplied log; unsupported media dialogue. |
| ponytail | library/tool | 2 | 1 | 2 | 2 | 2 | Persona/flow diagram rather than implementation path; “54%/20%/27%/100%” lacks conditions/source; FastAPI+React trial has no supplied run log; broad praise and generic captions. |
| OpenID Foundation | protocol/standard | 3 | 2 | 3 | 2 | 1 | Delegation sequence, scopes, DPoP discussion and one whitepaper link; no page citations or implementation status; incomplete-protocol caveat helps; “99%” is unsupported rhetorical quantity; unsupported admin-key/spreadsheet scenes. |
| PageIndex | library/tool | 2 | 1 | 3 | 2 | 1 | Tree search diagram and cost/latency tradeoff, but no linked API/code evidence; FAQ versus long-document distinction; SDK trial/migration claims lack logs; illustrative 0.1s/2s is not a benchmark; unsupported dialogue/road scene. |
| **Mean (n=8)** | | **2.625** | **1.125** | **2.875** | **2.0** | **1.25** | Small, subjective convenience sample; no significance claim. |

Unit 5 subsequently inspected sampled frames of the eight GIFs bundled in this
automator. Observations, exact sample indices and byte hashes are recorded in
`assets/memes/catalog.json`. Bundled filenames are not scene evidence: for example,
`github-star.gif` shows a man with “TEACH ME.” subtitles and `legacy-dumpster.gif`
shows an office man placing a finger at his lips. All eight are excluded because
original source and usage permission remain unknown. This inspection does not
establish that the published blog's copies match the bundled byte hashes; baseline
scene descriptions therefore remain unsupported/unverified, not proven invented.

## Directly observed counts and boundaries

The manifest's text counters recorded **16 Markdown images** (two per article),
**1 external Markdown source link** (OpenID), and **12 explicit scene-explanation
markers** across six articles. These counts describe syntax, not verified media
truth or completeness of every possible link format. All eight articles contain
technical claims needing source comparison; their count is **not measured** here.

At least four articles contain clear experience narratives without supplied logs:
Stagehand (invoice/agent build), browser communication (junior PR), ponytail
(FastAPI+React trial), PageIndex (SDK trial and migration). Count of proven fake
experiences: **not measured**; absence of logs does not establish fabrication.
Source-untraceable quantitative passage groups explicitly flagged during review:
Stagehand (speed/token savings), ponytail (benchmark percentages), browser
communication (90% of services), OpenID (99% probability). This is a lower-bound
passage inventory, **not** an exhaustive numeric claim count. The PageIndex example
arithmetic and hypothetical latency comparison are examples, not measured results.

Broken links/media, primary-source factual correctness, runtime, API cost, and
token usage: **not measured**. The baseline does not certify a publishable draft.

## Offline fixtures and characterization gate

`tests/fixtures/claims.json` maps known claims to HTML sections, README lines, and
PDF page 1. The LLM fixture deliberately adds the unsupported “73% faster” claim.
`local.gif` is a synthetic one-pixel GIF with no depicted action or dialogue.
The PDF is a one-page uncompressed text PDF, intentionally tiny for later extraction
tests. No published article body is copied into the fixture set.

Install `python -m pip install -r requirements-dev.txt`, then run:

```text
python -m pytest tests/test_current_pipeline.py -q
python -m pytest -q
```

The `current_behavior` marker identifies defects whose assertions should be replaced
by their owning units: PDF body omitted (2), success-shaped fallback after empty LLM
(4), two context-free memes and unchecked numeric claim (5/4), and publish enabled
after a 500-character preview (6). They pass now because they describe observed
behavior; they must not become requirements for the new pipeline. The approval
test also records sync/success messaging despite a mocked failed push.

Tests block application socket connections, HTTP requests, and subprocess launches;
Windows asyncio's synchronous internal socketpair creation is the sole socket exception.
Output paths and keys
are patched per test. Researchers use explicit local fake responses; writers use
fake LLM text; Telegram callbacks use mocks and a temporary blog. Neither Git nor
Vault integrations are instantiated or executed by the approval test.

## Evaluation after refactor

Generate candidate drafts from the same topic/source fixtures and fixed model/budget
for current, evidence-packet, packet+validation, and full-flow variants. Shuffle
anonymous draft labels before independent human review and retain individual scores.
Report depth/decision averages and failure counts alongside cost/time; do not claim
statistical significance from this small sample. Release requires zero confirmed
fake experience, untraceable core numbers, unsupported scene descriptions, and
broken links, plus depth and decision averages at least 4/5. Unchecked items remain
unverified until reviewed. The current sample does not meet that gate.

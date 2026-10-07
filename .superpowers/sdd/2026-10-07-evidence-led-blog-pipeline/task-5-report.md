# Unit 5 report — optional inspected media

Status: complete, self-reviewed, ready for the Unit 6 integration gate.
Implementation commit: `feat(media): select only inspected optional GIFs` (this
report is included in that commit; the dispatch response records its SHA).

## Scope delivered

- Added `assets/memes/catalog.json`, `src/editorial/media.py`, and
  `tests/test_media.py` with deterministic `choose_media`, image-only
  `render_media`, `validate_media`, and `apply_media` interfaces.
- `MemeCatalog` separates selectable `assets` from documented `excluded_assets`.
  It derives the asset root from the catalog file location. Plain filenames,
  unique IDs/files, plain accessible alt, inspection hash, source/use notes, and
  explicit inspected/approved statuses are validated.
- Selection requires a written argument with both static and grounding checks
  passed. The reviewed context phrase must occur in both brief and prose, and the
  post type must match. Catalog order cannot change the selected asset. No match,
  incomplete validation, absent/changed asset, unknown visuals or unknown rights
  produces `None`. Existing code, diagram, table or image takes priority.
- Validation rejects unknown/remote assets, missing or changed bytes, inaccurate
  or missing alt, multiple images, reference/HTML media, duplicate captions,
  image-adjacent explanatory captions or quotes, and explicit scene-explanation
  prose such as “짤 설명”. A GIF is an image-only paragraph with factual alt.
- `validate_draft` invokes media validation. Image alt is validated against the
  catalog rather than treated as an evidence citation or ordinary technical
  claim. `BlogWriter` selects after validated prose and returns `media_choice`;
  selection/rendering errors remain visible as `NEEDS_REVISION`.
- Migrated `MemeManager` to a side-effect-free formatting adapter. Removed random
  selection, invented technical captions, duplicate caption formatting, directory
  creation and automatic synchronization of every GIF into the blog.
- Runtime `policy.md` now states the same media rules. Fixed the deferred Unit 0
  wording: uninspected baseline scenes are unsupported/unverified, not established
  invented. Existing published posts were not edited.

## Actual local inspection

On 2026-10-07, sampled eight frames (including first and last) of each of the eight
local GIFs using Pillow contact sheets, then visually inspected the resulting
images. The catalog records exact frame indices and SHA-256 bytes. Temporary
contact sheets are in ignored `temp/media-inspection/`; no source asset was edited.

Observed filename mismatches include `github-star.gif` (a man and “TEACH ME.”
subtitle), `legacy-dumpster.gif` (an office man putting his finger to his lips), and
`rage-computer-throw.gif` (a red animated character with flames above its head).
No observed frame justifies their previous GitHub-star, dumpster/legacy-code or
computer-throw/OS-damage captions. Catalog observations do not infer an unseen
release time, developer task, spoken audio or character identity.

No original source URL, creator/license record or usage permission was available
in the inspected local material. All eight therefore have unknown usage rights
and remain outside the selectable assets list. Production selection currently
returns `None`. The positive selection gate uses only the existing synthetic
one-pixel local GIF fixture, whose alt describes a pixel without a scene/dialogue;
its temporary catalog explicitly documents fixture author permission.

## TDD and final verification

All commands used the worktree `.\.venv\Scripts\python.exe` with the existing
offline network/process guard, fake LLM responses and temporary output paths.

```text
# Targeted initial RED: missing media module and missing draft media validation
python -m pytest tests/test_media.py::test_bundled_catalog_records_unknown_rights_as_excluded_not_safe tests/test_media.py::test_draft_validation_cannot_certify_an_unverified_model_image -q --tb=short
2 failed in 0.47s

# First GREEN after contracts, catalog, selection, validation and writer migration
python -m pytest tests/test_media.py -q --tb=short
29 passed in 0.58s

# Self-review RED: caption/quote bypass, repeated alt after insertion, incomplete checks
python -m pytest tests/test_media.py -q --tb=short
4 failed, 30 passed in 0.68s
# GREEN after prospective-media validation and both-check requirement
34 passed in 0.47s

# Self-review RED: malformed catalog/root override threw TypeError
python -m pytest tests/test_media.py -q --tb=short
2 failed, 35 passed in 0.86s

# Final focused and full gates after all production/test edits
.\.venv\Scripts\python.exe -m pytest tests/test_media.py -q --tb=short
37 passed in 0.51s
.\.venv\Scripts\python.exe -m pytest -q --tb=short
249 passed in 11.01s
git diff --check
no whitespace errors
```

The unrelated protocol fixture receives no meme. The positive fixture receives
exactly one deterministic GIF with accurate alt and no explanatory caption. No
xfail, skips, network/API calls, live publication, push, real blog/Vault write, or
Vault indexing was used. Self-review covered all changed source, tests and catalog
records; no production changes followed the final gates.

## Integration handoff and limits

- Unit 6 must persist `result["media_choice"]` (or explicit `None`) and selected
  asset bytes/hash with the review artifact, bind them to review/approval, and
  publish only that approved selection. `MediaChoice` includes ID, absolute local
  path, inspection hash, alt, public URL, context and source/use notes. Unit 5
  appends an image-only section but does not copy assets or write a blog repo.
- Catalog permission is a maintainer-supplied reviewed record, not a legal
  determination. Unknown records stay excluded. Frame sampling does not establish
  every frame or audio; the catalog states those observation boundaries.
- Selection is conservative lexical matching, not semantic humor assessment.
  Static validation checks known captions/scene markers and canonical image
  paragraphs; it cannot prove arbitrary free-form scene assertions true. General
  unmapped-claim validation and human review still apply.
- Published blog asset copies were not byte-compared to this repository's GIFs;
  baseline descriptions remain unsupported/unverified. This unit supplies no
  blinded editorial scores, production cutover or full publish-flow verification.

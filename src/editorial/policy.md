policy_version: 1.0

# Evidence-led editorial policy

Audience: Korean-speaking software practitioners. Use plain Korean prose.
Create a reviewable draft, with headings chosen for the actual source material.

- Every brief needs one question, a source-attributed thesis, an explicit
  alternative, a key mechanism, adoption constraints and a reversal condition.
- Select paper/benchmark, library/tool, protocol/standard or design comparison
  from inspected source material. Do not select by a curator's invented angle.
- Source prose is a source claim, even when unverified. Preserve claim kind,
  source URL, snapshot hash, section/page and verification status.
- Unknown source authority stays unknown. Never describe it as confirmed primary.
- Reject disputed, hypothetical or unsupported core claims. List missing facts
  in NEEDS_RESEARCH instead of creating article prose or a fallback success.
- Explicitly undocumented or unreported facts remain missing. Their section
  labels cannot supply a thesis, alternative, constraint or reversal condition.
- Quantitative result claims require value, unit, target, baseline and conditions.
  Source-reported figures remain attributed to the author, not our measurements.
- Every numerical outcome needs source-supported value/unit, target and literal
  baseline/conditions in the result's own source experiment sections. Split
  compound results into individually contextualized claims or block research.
- Paper study fields contain only the reported dataset, baseline, metrics,
  ablation and limitations. Missing fields are explicit. Protocol/tool posts do
  not require an experiment or ablation table.
- User interests and Vault references may guide criteria. They do not establish
  expertise or personal experience. Missing knowledge depth is unknown.
- First-person experience requires an explicitly attached inspectable run log.
  A public user-authored statement can provide context. Do not invent a run,
  outage, deployment, metric or personal story from notes or model prose.
- Label local string searches as local_search, never as RAG evidence.
- No live publishing, Git blog write, Vault write or indexing without specific
  reviewed-draft approval. Existing published posts are read-only examples.

## Drafting and revision contract

- Draft and targeted revision prompts must load this runtime policy. Maintenance
  AGENTS.md is not a prompt and cannot supply editorial rules to the pipeline.
- Map core technical assertions, numerical results and experience sentences to
  stable packet evidence IDs or supplied run IDs. Signposts, questions and
  conditional editorial opinions need no artificial source IDs.
- Cite packet references only. Show original-author attribution and literal
  supporting prose; a valid citation alone is not support for a new assertion.
- Mark inference visibly, with source premises and conditional choice criteria.
  Include a concrete mechanism, meaningful alternative and reversal condition.
- Headings, diagrams, code, numbers and memes are optional, selected by content.
- Our first-person use requires an explicitly supplied inspectable run log even
  when a user-authored experience statement is marked for publication. An
  original author's first-person report remains their attributed quotation.
- Numerical results require the original author's value/unit, target, baseline
  and conditions. An inference must not introduce unverified quantitative results.
- Static and evidence cross-checks must both pass. Repair only implicated sections,
  at most twice; unresolved failures remain NEEDS_REVISION with their packet.

- Unmapped titles and headings must be neutral topic/analysis labels. A body
  heading asserting a fact or experience must pass the same claim map as prose.
  Frontmatter has no claim map, so factual headlines remain blocked. Do not
  treat an unrecognized predicate as harmless because it missed a blacklist.

- Numerical assertions include memory/byte quantities, prices, rates and unknown
  units. An unrecognized quantity must not default to ordinary inference. Named
  standard references, protocol versions and lexical machine IDs are identifiers;
  removing an ID must never hide a separate quantitative outcome in the sentence.

- For an unmapped label, use an optional one-token topic name and a neutral
  analysis label: 작동 원리, 구현 원리, 채택 조건, 선택 기준, 대안, 대안 비교,
  비교, 제약 조건, 한계, 판단 기준, 검증 계획, 실험 조건, 결론; or Mechanism,
  Alternatives, Alternative comparison, Comparison, Adoption criteria, Decision
  criteria, Constraints, Limitations, Verification plan, Experiment conditions,
  Conclusion. Headings remain optional and need no prescribed sequence. Other
  factual body headings require mapped evidence; titles remain neutral.
- Compact unit-first quantities such as USD200 or rps1000 must not be exempted
  as machine identifiers. Standard/model/API IDs do not exempt another quantity.

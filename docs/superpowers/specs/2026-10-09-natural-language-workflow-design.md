# Natural-language blog workflow

The user approved this design in conversation on 2026-10-09 and asked for implementation. This extends the existing evidence-led pipeline using its draft store and publisher.

## Outcome

Natural-language requests in Codex map to documented structured CLI operations. The default flow remains Vault interests -> five Telegram candidates -> selected topic -> primary-source research -> Korean draft and validation -> exact revision approval -> publication and Vault sync. `continue` resumes a stored run. Creating a run never constitutes approval of an unseen draft.

## Requirements

1. A durable run stores request intent, publication mode, stage checkpoints, topic/draft identity, frozen input references, review delivery, approval and publication receipts. JSON atomic replacement and a process-safe run lock reuse the existing local-store approach. No queue framework or new database dependency. Interrupted/failed stages resume; completed publication reconciles remote state or retries sync without duplicate push.
2. A shared model adapter provides bounded request and stage budgets, limited retry of transient failures, explicitly configured fallbacks, output budgets and redacted capture metadata. Model calls must not block Telegram polling. Failed providers never produce article prose.
3. Research follows inspected HF/arXiv primary links and the paper's official repository, with bounded fetch count. Complete raw snapshots stay intact. Located excerpts and table values retain page/section, header/unit, baseline and conditions. Numeric outcomes split into independently contextualized claims; context cannot migrate across unrelated studies. Missing facts remain visible. A model may propose located excerpts but deterministic checks reject invented text, numbers and references.
4. Public prose uses natural Korean attribution and citations; internal grounding retains original spans, claim kind and uncertainty. Independent structured grounding review is bound to the exact sentence and evidence snapshot hashes. It is not human review or truth proof. Unsupported, contradicted, stale or unknown claims block. Deterministic numeric/experience/manifest checks remain. No model-supplied self-attestation can certify its own output. Fix prompts carry only implicated sections and required premises, preserving other section bytes. Write/readability editing precedes media and is revalidated.
5. Publication modes are `shadow`, `reviewed_trial`, `production`. Shadow cannot publish. A reviewed trial authorizes one stored run and one specifically approved draft/hash; it does not change global settings. Production retains the current real-quality-report and cutover gate. CLI and Telegram use the same authorization service. Existing publisher safeguards, exact refspec, push reconciliation and independent sync retry remain. Vault indexing is never invoked.
6. A repository skill translates natural-language requests into supported CLI operations; it does not invent shell workflows or patch providers during a run. Request/status/resume/select/revise/approve/publish commands output structured state and actionable failure reasons. Original Vault snippets are private interest evidence, never personal experience.
7. Evaluation records stage time/model usage, failure/recovery events and operator intervention separately. Real paired comparisons require frozen matching sources/model/budgets. Human score sheets remain pending until a human supplies ratings. No fake ablation, fabricated measurement or automatic human rating.

## Acceptance

Offline injected end-to-end runs cover persistent selection, research, review delivery, identity/hash-bound approval, one trial publication and sync retry; restart and duplicate actions are tested. Regression fixtures reproduce the first UniSkill trial's abstract-only research, scoped PDF/table context, Korean paraphrase, JSON span mismatch, revision budget exhaustion, provider outage and shadow/trial ambiguity. Actual API/model quality and GitHub Pages deployment are separate live verification boundaries. No live blog/Vault/Telegram writes occur during implementation testing.

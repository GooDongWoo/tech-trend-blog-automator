# Maintenance instructions

## Editorial policy and drafting

The running pipeline reads `src/editorial/policy.md` through `load_policy()`.
This file is maintainer guidance and must never stand in for runtime prompt rules.
When editorial requirements change, update the versioned runtime policy and the
focused drafting/claim-validation tests together. Keep draft and revision prompts
on that loaded policy; preserve packet references, claim kinds and uncertainty.

Keep failures visible as NEEDS_RESEARCH or NEEDS_REVISION. Never replace an outage
with article prose. Direct experience requires an explicitly supplied inspectable
run log; Vault interests or model prose cannot provide it. Author-reported results
must retain attribution and their reported comparison conditions.

Use offline fixtures and temporary paths for verification. Drafting writes only
local review artifacts. Blog/Vault writes and publication require approval of a
specific reviewed draft; never trigger Vault incremental indexing automatically.

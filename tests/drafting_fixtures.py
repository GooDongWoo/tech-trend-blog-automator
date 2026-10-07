"""Local, hand-checked drafting inputs; no fetch or model calls."""
import copy
import hashlib

import pytest

from src.editorial.brief import build_brief
from src.editorial.models import EvidenceClaim, ResearchPacket, SourceRecord, SourceRef, UserContext


@pytest.fixture
def drafting_input():
    bodies = (
        "Persist the request before acknowledgement.",
        "Compare with the in-memory queue.",
        "Requires durable storage; unsuitable when writes are unavailable.",
    )
    text = "\f".join(f"# {heading}\n{body}" for heading, body in zip(
        ("API mechanism", "Baseline alternatives", "Deployment constraints"), bodies))
    source = SourceRecord(url="https://example.invalid/pageindex", title="PageIndex tool library",
        kind="markdown", sha256=hashlib.sha256(text.encode()).hexdigest(), text=text,
        locations=("section:0", "section:1", "section:2"), fetched_at="2026-10-07T00:00:00Z", role="primary")
    claims = tuple(EvidenceClaim(text=body, kind="source_claim", source_refs=(SourceRef(
        url=source.url, sha256=source.sha256, location=source.locations[i]),)) for i, body in enumerate(bodies))
    packet = ResearchPacket(topic_id="pageindex-fixture", question="When should the persistent queue be adopted?",
        sources=(source,), claims=claims)
    return packet, build_brief(packet, UserContext())


def response():
    url = "https://example.invalid/pageindex"
    def fact(sentence, eid, role):
        return {"sentence": sentence, "kind": "source_claim", "evidence_ids": [eid], "role": role}
    mechanism = f'원문은 “Persist the request before acknowledgement.”라고 설명한다. [E1]({url})'
    alternative = f'원문은 “Compare with the in-memory queue.”라고 설명한다. [E2]({url})'
    constraint = f'원문은 “Requires durable storage; unsuitable when writes are unavailable.”라고 설명한다. [E3]({url})'
    decision = "추론: durable storage 조건이라면 in-memory queue 대안과 비교해 채택을 고려한다. writes are unavailable 조건에서는 선택을 바꾼다."
    return copy.deepcopy({"frontmatter": '---\nlayout: post\ntitle: "PageIndex 채택 조건"\ndate: "2026-10-07 09:00:00 +0900"\ncategories: [Tech]\ntags: [queue]\n---',
        "sections": [
            {"id": "flow", "text": "어떤 조건에서 선택할까?\n\n" + mechanism, "claims": [fact(mechanism, "E1", "mechanism")]},
            {"id": "compare", "text": alternative, "claims": [fact(alternative, "E2", "alternative")]},
            {"id": "conditions", "text": constraint, "claims": [fact(constraint, "E3", "constraint")]},
            {"id": "decision", "text": decision, "claims": [{"sentence": decision, "kind": "inference", "evidence_ids": ["E2", "E3"], "role": "decision"}]},
        ]})

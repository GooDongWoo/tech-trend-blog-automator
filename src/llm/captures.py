"""Select one physical capture per UUID call, preferring persisted snapshots."""
from pathlib import Path
import re


def canonical_capture_paths(refs):
    selected = {}
    for ref in sorted({Path(ref).resolve() for ref in refs}, key=str):
        match = re.fullmatch(r'model-call-([a-fA-F0-9]{32})\.json', ref.name)
        # Unknown legacy names have no proven shared identity: retain each path.
        identity = ('call', match[1].lower()) if match else ('path', str(ref))
        working = ref.parent.name == 'model-calls' and ref.parent.parent.name == 'working'
        prior = selected.get(identity)
        if prior is None or (prior.parent.name == 'model-calls' and prior.parent.parent.name == 'working' and not working):
            selected[identity] = ref
    return sorted(selected.values(), key=str)

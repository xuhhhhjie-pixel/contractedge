"""Behaviour requirements (obligations): sources, versions and the host-side state snapshot.

The state lives on the host in a directory that is not mounted into the method container, so the
model cannot edit it through its shell. One process updates it in order; writes are atomic.

An obligation has two independent dimensions:
- grounding: GROUNDED (a checked public quote supports it), UNRESOLVED (no usable support, or a
  declared conflict), RETRACTED (withdrawn with public counter-evidence);
- outcomes per scenario: PASS_AT_SNAPSHOT / FAIL_AT_SNAPSHOT bound to a source tree and the
  obligation version. Only evidence from an executed check writes outcomes (behavior_feedback).
The model proposes condition, expected, kind and source_refs; ids, versions and outcomes are
assigned here. A valid source reference shows the quote exists, not that the reading is right.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import time
from pathlib import Path

SCHEMA = 'contractedge.behavior_state.v1'
KINDS = ('target', 'preserve')
GROUNDING = ('GROUNDED', 'UNRESOLVED', 'RETRACTED')
OUTCOMES = ('UNKNOWN', 'PASS_AT_SNAPSHOT', 'FAIL_AT_SNAPSHOT')
# Method view: the editable source and the read-only public inputs the task text declares
# ("Public inputs are mounted read-only at /inputs"). Nothing else the container can see.
ALLOWED_ROOTS = ('/testbed', '/inputs')
DENIED = ('/testbed/.git',)
MIN_QUOTE, MAX_QUOTE, MAX_TEXT = 8, 400, 400


class StateError(Exception):
    """The snapshot is unreadable; the mechanism stops updating state instead of starting empty."""


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text)).strip()


def _key(kind: str, condition: str, expected: str) -> str:
    return json.dumps([kind, _norm(condition).lower(), _norm(expected).lower()])


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', 'surrogateescape')).hexdigest()


def empty() -> dict:
    return {'schema': SCHEMA, 'obligations': {}, 'checks': {}, 'evidence': [], 'next_actions': [],
            'reported': [], 'reminded': [], 'packages': {}, 'test_files': [], 'counters': {}}


class Store:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.path = self.dir / 'behavior-state.json'
        self.events = self.dir / 'behavior-events.jsonl'

    def load(self) -> dict:
        if not self.path.exists():
            return empty()
        try:
            value = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f'behavior state unreadable: {exc}') from exc
        if not isinstance(value, dict) or value.get('schema') != SCHEMA:
            raise StateError('behavior state has an unexpected schema')
        return value

    def save(self, snapshot: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False, sort_keys=True))
        os.replace(tmp, self.path)

    def log(self, event: str, **value) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.events.open('a') as stream:
            stream.write(json.dumps({'event': event, 'at': time.time(), **value}, ensure_ascii=False,
                                    default=str, sort_keys=True) + '\n')


# ---------------------------------------------------------------- sources
class Sources:
    """Allowed public material: the task text and files under the allowed roots.

    `read_file(abs_path)` returns the file text or None; the host reads through the container.
    """

    def __init__(self, task_text: str, read_file):
        self.task = task_text or ''
        self.read_file = read_file
        self.cache: dict[str, str | None] = {}

    @staticmethod
    def resolve(path: str) -> str | None:
        if not isinstance(path, str) or not path.strip() or '\0' in path:
            return None
        raw = path.strip()
        full = posixpath.normpath(raw if raw.startswith('/') else posixpath.join('/testbed', raw))
        if not any(full == root or full.startswith(root + '/') for root in ALLOWED_ROOTS):
            return None
        if any(full == d or full.startswith(d + '/') for d in DENIED):
            return None
        return full

    def text(self, full: str) -> str | None:
        if full not in self.cache:
            self.cache[full] = self.read_file(full)
        return self.cache[full]

    def check(self, ref) -> tuple[dict | None, str]:
        """Return (verified reference, '') or (None, reason)."""
        if not isinstance(ref, dict):
            return None, 'reference is not an object'
        quote = _norm(ref.get('quote', ''))
        if not MIN_QUOTE <= len(quote) <= MAX_QUOTE:
            return None, f'quote must be {MIN_QUOTE}-{MAX_QUOTE} characters'
        source = ref.get('source')
        if source == 'task':
            body, where = self.task, {'source': 'task'}
        elif source == 'file':
            full = self.resolve(ref.get('path', ''))
            if full is None:
                return None, f"path {ref.get('path')!r} is outside the allowed public sources"
            body = self.text(full)
            if body is None:
                return None, f'{full} cannot be read'
            where = {'source': 'file', 'path': full}
        else:
            return None, "source must be 'task' or 'file'"
        flat = _norm(body)
        at = flat.find(quote)
        if at < 0:
            return None, 'quote not found in the source'
        line = None
        if source == 'file':
            head = quote[:30]
            line = next((n for n, text in enumerate(body.splitlines(), 1) if head in _norm(text)), None)
        return {**where, 'quote': quote, 'content_sha256': sha(body), 'line': line}, ''


# ---------------------------------------------------------------- proposals
def _verified(refs, sources: Sources) -> tuple[list[dict], list[str]]:
    good, why = [], []
    for ref in refs if isinstance(refs, list) else []:
        value, reason = sources.check(ref)
        if value:
            good.append(value)
        else:
            why.append(reason)
    if not isinstance(refs, list) or not refs:
        why.append('no source_refs')
    return good, why


def _fields(item: dict) -> tuple[dict | None, str]:
    kind = item.get('kind')
    condition, expected = _norm(item.get('condition', '')), _norm(item.get('expected', ''))
    if kind not in KINDS:
        return None, f'kind must be one of {KINDS}'
    if not condition or not expected:
        return None, 'condition and expected are required'
    if len(condition) > MAX_TEXT or len(expected) > MAX_TEXT:
        return None, f'condition/expected longer than {MAX_TEXT} characters'
    return {'kind': kind, 'condition': condition, 'expected': expected}, ''


def apply_proposals(snapshot: dict, proposals, sources: Sources, origin: str) -> dict:
    """Apply proposed adds, revisions and retractions; return the RequirementDelta.

    An empty delta only means nothing new was accepted, not that the task is fully covered.
    """
    delta = {'added': [], 'revised': [], 'retracted': [], 'unresolved': [], 'duplicates': [], 'rejected': []}
    obligations = snapshot['obligations']
    keys = {_key(o['kind'], o['condition'], o['expected']): oid for oid, o in obligations.items()}
    for item in proposals if isinstance(proposals, list) else []:
        if not isinstance(item, dict):
            delta['rejected'].append({'item': repr(item)[:200], 'reason': 'not an object'})
            continue
        op = item.get('op', 'add')
        if op == 'add':
            fields, why = _fields(item)
            if fields is None:
                delta['rejected'].append({'item': item, 'reason': why})
                continue
            key = _key(fields['kind'], fields['condition'], fields['expected'])
            if key in keys:
                delta['duplicates'].append(keys[key])
                continue
            refs, reasons = _verified(item.get('source_refs'), sources)
            conflict = _norm(item.get('conflict', ''))
            grounding = 'GROUNDED' if refs and not conflict else 'UNRESOLVED'
            oid = f'O{1 + max((int(k[1:]) for k in obligations), default=0)}'
            obligations[oid] = {'id': oid, 'version': 1, **fields, 'source_refs': refs, 'grounding': grounding,
                                'grounding_notes': reasons + ([f'conflict: {conflict}'] if conflict else []),
                                'outcomes': {}, 'check_ids': [], 'origin': origin, 'history': []}
            keys[key] = oid
            delta['added' if grounding == 'GROUNDED' else 'unresolved'].append(oid)
        elif op in ('revise', 'retract'):
            current = obligations.get(item.get('id'))
            if current is None:
                delta['rejected'].append({'item': item, 'reason': f"unknown obligation {item.get('id')!r}"})
                continue
            refs, reasons = _verified(item.get('source_refs'), sources)
            if not refs:
                # Revising or withdrawing a requirement needs new public support; failing checks are not support.
                delta['rejected'].append({'item': item, 'reason': '; '.join(reasons) or 'no verified source'})
                continue
            if op == 'retract':
                current['history'].append({'version': current['version'], 'grounding': current['grounding'],
                                           'reason': _norm(item.get('reason', ''))})
                current['grounding'] = 'RETRACTED'
                current['source_refs'] = current['source_refs'] + refs
                delta['retracted'].append(current['id'])
                continue
            fields, why = _fields({**{k: current[k] for k in ('kind', 'condition', 'expected')},
                                   **{k: item[k] for k in ('kind', 'condition', 'expected') if k in item}})
            if fields is None:
                delta['rejected'].append({'item': item, 'reason': why})
                continue
            same = _key(**fields) == _key(current['kind'], current['condition'], current['expected'])
            current['history'].append({'version': current['version'], 'condition': current['condition'],
                                       'expected': current['expected'], 'kind': current['kind']})
            current.update(fields, source_refs=refs, grounding='GROUNDED', grounding_notes=[])
            if not same:
                # A new meaning: old checks and outcomes no longer speak for it.
                current['version'] += 1
                current['outcomes'] = {}
                current['check_ids'] = []
            delta['revised'].append(current['id'])
        else:
            delta['rejected'].append({'item': item, 'reason': f'unknown op {op!r}'})
    return delta


def missing_against(snapshot: dict, reference_quotes: list[str]) -> list[str]:
    """Development review: reference requirement quotes no grounded obligation cites.

    The run itself never claims coverage; this compares an extraction with a human-made list.
    """
    cited = [ref['quote'].lower() for o in active(snapshot) for ref in o['source_refs']]
    return [q for q in reference_quotes
            if not any(_norm(q).lower() in c or c in _norm(q).lower() for c in cited)]


def active(snapshot: dict) -> list[dict]:
    return [o for o in snapshot['obligations'].values() if o['grounding'] == 'GROUNDED']


def outcome(obligation: dict, scenario: str, tree: str | None) -> str:
    """Current verification status of one scenario; anything not bound to `tree` is UNKNOWN."""
    row = obligation['outcomes'].get(scenario)
    if not row or row.get('tree') != tree or row.get('version') != obligation['version']:
        return 'UNKNOWN'
    return row['status']

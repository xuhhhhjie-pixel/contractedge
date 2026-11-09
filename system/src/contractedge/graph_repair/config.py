"""Mechanism switches for contractedge-graph and the named ablation variants.

Every switch off plus the original Live prompt is the live-equivalent
configuration: same DefaultAgent behaviour, only the read-only tool mount differs.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace

SWITCHES = ('graph', 'linkers', 'closure', 'probes', 'checkpoint', 'attribution', 'stop_rule')


@dataclass(frozen=True)
class Variant:
    name: str
    prompt: str = 'graph'          # 'graph' (three-phase guide) or 'live' (original livesweagent.yaml)
    graph: bool = True
    linkers: bool = True
    closure: bool = True
    probes: bool = True
    checkpoint: bool = True
    attribution: bool = True
    stop_rule: bool = True
    wall_seconds: int = 1800
    force_fraction: float = 0.85
    probe_time_fraction: float = 0.20
    probe_call_fraction: float = 0.15
    probe_timeout_seconds: int = 300
    max_full_tests: int = 2

    @property
    def any_mechanism(self) -> bool:
        return any(getattr(self, name) for name in SWITCHES)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, value: dict) -> 'Variant':
        known = {f.name for f in fields(cls)}
        unknown = set(value) - known
        if unknown:
            raise ValueError(f'Unknown variant fields: {sorted(unknown)}')
        return cls(**value)


FULL = Variant('full')
VARIANTS = {
    'full': FULL,                                                      # group A
    'live-equivalent': Variant('live-equivalent', prompt='live', **{s: False for s in SWITCHES}),
    'no-probes': replace(FULL, name='no-probes', probes=False),        # group C
    'no-closure': replace(FULL, name='no-closure', closure=False),     # group D
    'no-stop-rule': replace(FULL, name='no-stop-rule', stop_rule=False),  # group E
    # group F: Live prompt, only the submit-time rule check and forced submission.
    'submit-only': Variant('submit-only', prompt='live', graph=False, linkers=False, closure=False,
                           probes=False, checkpoint=True, attribution=False, stop_rule=True),
}


def variant(name: str) -> Variant:
    try:
        return VARIANTS[name]
    except KeyError:
        raise ValueError(f'Unknown variant {name!r}; known: {sorted(VARIANTS)}') from None


def dumps(value: Variant) -> str:
    return json.dumps(value.to_json(), sort_keys=True)

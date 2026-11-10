"""Mechanism switches for contractedge-graph and the named ablation variants.

Every switch off plus the original Live prompt is the live-equivalent
configuration: same DefaultAgent behaviour, only the read-only tool mount differs.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace

SWITCHES = ('graph', 'linkers', 'closure', 'probes', 'checkpoint', 'attribution', 'stop_rule')
BEHAVIOR_SWITCHES = ('behavior_requirements', 'behavior_feedback', 'behavior_impact')


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
    # Behaviour requirements and checks (v1.2 method). Provisional limits, to be frozen before comparisons.
    behavior_requirements: bool = False
    behavior_feedback: bool = False
    behavior_impact: bool = False
    behavior_max_proposals: int = 3
    behavior_proposal_timeout: int = 30
    behavior_checks_per_tick: int = 1
    behavior_submit_checks: int = 2
    behavior_check_timeout: int = 120
    behavior_max_check_runs: int = 12
    behavior_reserve_seconds: int = 90

    def __post_init__(self):
        if self.behavior_impact:
            raise ValueError('behavior_impact is not implemented in this version')
        if self.behavior_feedback and not self.behavior_requirements:
            raise ValueError('behavior_feedback needs behavior_requirements')
        if self.behavior_requirements:
            if not self.checkpoint:
                raise ValueError('behaviour requirements need checkpoint (results are bound to source trees)')
            clash = [name for name in ('graph', 'linkers', 'closure', 'probes') if getattr(self, name)]
            if clash:
                raise ValueError(f'behaviour requirements replace the graph completion rules; turn off {clash}')

    @property
    def any_mechanism(self) -> bool:
        return any(getattr(self, name) for name in SWITCHES + BEHAVIOR_SWITCHES)

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
# v1.2 comparison: submit-only is the shared reliable base; these add the behaviour method on top of it.
VARIANTS['behavior'] = replace(VARIANTS['submit-only'], name='behavior', behavior_requirements=True,
                               behavior_feedback=True)
VARIANTS['behavior-requirements-only'] = replace(VARIANTS['submit-only'], name='behavior-requirements-only',
                                                 behavior_requirements=True)


def variant(name: str) -> Variant:
    try:
        return VARIANTS[name]
    except KeyError:
        raise ValueError(f'Unknown variant {name!r}; known: {sorted(VARIANTS)}') from None


def dumps(value: Variant) -> str:
    return json.dumps(value.to_json(), sort_keys=True)

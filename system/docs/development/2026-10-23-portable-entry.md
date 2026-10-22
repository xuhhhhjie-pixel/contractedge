# Portable development entry verification (2026-10-23)

The self-contained test entry now defaults CONTRACTEDGE_TOPOMEN_SOURCE to this
checkout's vendor/topomen. An explicit environment override remains authoritative.
The original publication's developer-specific fallback caused three failures on a
different account. Runtime method source and the original 0.6.27 freeze are unchanged.

Verified in a new worktree and independent Python 3.10 virtual environment:

- Install requirements-dev.lock, then pip install --no-deps -e .; CLI --help exits 0.
- Without a TopoMen override, python scripts/test_portable.py reports 628 passed,
  17 subtests passed and one failure in test_temporal_response_bounds. The Docker
  daemon rejects --cpus=2 before probe execution because CFS quota is unavailable.
- With CONTRACTEDGE_TOPOMEN_SOURCE=$PWD/vendor/topomen, python -m pytest -q
  tests/test_topomen_agent.py reports 9 passed using fixture providers.
- Entry invocation inspection confirms the default and explicit override behavior.
- DOCKER_API_VERSION=1.43 is task-local for the current Docker 24 daemon; no daemon
  restart, global cgroup change, test exclusion or limit removal was performed.

The portable entry explicitly excludes six historical/native modules; the native
module above was run separately. These scopes are not a full 662+22 reproduction
or evidence of model repair effectiveness. PyYAML 6 in the development lock differs
from the historical PyYAML 5.4.1 profile. formal_ready remains false. No real model
HTTP request, dataset download or scoring was performed.

The original DELIVERY_MANIFEST.json is preserved. README.md and test_portable.py
are intentional differences from that published manifest; this note is new.

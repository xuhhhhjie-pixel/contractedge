"""Explicit self-contained development suite; historical replays are not counted."""
from pathlib import Path
import os, subprocess, sys
root=Path(__file__).resolve().parents[1]
excluded=['test_ready_support_consistency.py','test_probe_profiles_v2.py','test_compact_render_inventory.py','test_diagnosis_error_aggregation.py','test_structured_observation_adapters.py','test_topomen_agent.py']
print('Historical fixture / native integration modules NOT RUN:', ', '.join(excluded),flush=True)
env={**os.environ,'PYTHONPATH':os.pathsep.join([str(root/'src'),str(root),os.environ.get('PYTHONPATH','')])}
env.setdefault('CONTRACTEDGE_TOPOMEN_SOURCE', str(root/'vendor'/'topomen'))
raise SystemExit(subprocess.call([sys.executable,'-m','pytest','-q','tests',*[f'--ignore=tests/{p}' for p in excluded]],cwd=root,env=env))

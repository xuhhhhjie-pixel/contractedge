#!/bin/bash
# Unattended chain: finish M2 smoke -> config comparison -> M4 panel (full variant) -> summaries ->
# linker recall on the panel -> zero-cost preparation of the first M5 cases while disk allows.
# Paid M5 groups are NOT started here. Progress: logs/overnight.log; result: OVERNIGHT_DONE.json.
set -u
E=~/contractedge-eval-graph-repair-20261109
SRC=~/contractedge-collab-20261023/worktrees/graph-repair-20261109/system/src
PY=/tmp/contractedge-deepseek-preflight-20261019/venv312/bin/python
H=$SRC/contractedge/graph_repair/harness
cd $E || exit 1
export PATH=$E/bin:$PATH
log() { echo "$(date -Is) $*" >> $E/logs/overnight.log; }
free_gib() { df -BG --output=avail $E | tail -1 | tr -dc 0-9; }

log "start; free $(free_gib)G"
# 1. Let zero-model preparations started earlier finish, then set aside any incomplete case tree.
while pgrep -f "[p]repare_case150.py" >/dev/null; do sleep 30; done
$PY - <<'EOF' >> $E/logs/overnight.log 2>&1
import hashlib, json, os, shutil, time
from pathlib import Path
E = Path.home() / 'contractedge-eval-graph-repair-20261109'
for case in sorted((E / 'cases').iterdir()):
    if (case / 'QUALIFIED.json').exists():
        continue
    dest = E / 'cases-incomplete' / f'{case.name}-{int(time.time())}'
    dest.parent.mkdir(exist_ok=True)
    short = 'cep5-' + hashlib.sha256(str(case).encode()).hexdigest()[:10]
    for d in Path('/run/user', str(os.getuid()), 'cep5-public').glob(short + '-*'):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    shutil.move(str(case), dest)
    job = E / 'jobs' / ('prepare-' + case.name)
    if job.exists():
        shutil.move(str(job), str(dest) + '.job')
    print('set aside incomplete case', case.name, '->', dest)
EOF

# 2. Smoke (M2): restart the daemon with the current scheduler code.
pkill -f "[g]raph_eval.py daemon smoke"; sleep 3
rm -f $E/PAUSE
log "smoke daemon start"
$PY graph_eval.py daemon smoke >> logs/daemon-smoke.log 2>&1
log "smoke daemon exit $?"
for d in cases/*/active-case/attempts-smoke-Z1/contractedge-graph; do
  c=$(echo $d | cut -d/ -f2); b=cases/$c/active-case/attempts-smoke-B1/live-swe-agent
  [ -f $b/native-config.yaml ] && $PY $H/compare_config.py $b $d > analysis/smoke-compare-$c.json 2>&1
  log "compare $c rc=$?"
done
PYTHONPATH=$SRC $PY $H/summarize.py $E smoke analysis/summary-smoke.json > /dev/null 2>> logs/overnight.log

# 3. Panel (M4): full variant on the 12-case development panel.
[ -f runs-panel.json ] || $PY graph_eval.py runs panel >> logs/overnight.log 2>&1
log "panel daemon start; free $(free_gib)G"
$PY graph_eval.py daemon panel >> logs/daemon-panel.log 2>&1
log "panel daemon exit $?"
PYTHONPATH=$SRC $PY $H/summarize.py $E panel analysis/summary-panel.json > /dev/null 2>> logs/overnight.log
PYTHONPATH=$SRC $PY $H/eval_linkers.py ~/contractedge-eval-front-half-20261106/gold-draft $E analysis/linker-recall-panel.json >> logs/overnight.log 2>&1
log "summaries written"

# 4. Zero-cost preparation of the first M5 cases (A/B list order) while disk allows.
CASES=$($PY -c "import json;print(' '.join(json.load(open('freeze/EVALUATION_LISTS.json'))['evaluation_without_holdout']))")
n=0
for c in $CASES; do
  [ -f cases/$c/QUALIFIED.json ] && continue
  [ $n -ge 10 ] && break
  if [ "$(free_gib)" -lt 55 ]; then log "stop m5 preparation: free $(free_gib)G"; break; fi
  [ -e cases/$c ] && continue
  log "prepare $c"; PYTHONPATH=$E/controller:$E PYTHONDONTWRITEBYTECODE=1 $PY prepare_case150.py $c > logs/prepare-$c.log 2>&1
  log "prepare $c rc=$?"; n=$((n+1))
done
$PY - <<EOF
import json, datetime, shutil
from pathlib import Path
E = Path('$E')
json.dump({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'smoke': json.loads((E/'status/STATUS-smoke.json').read_text()),
           'panel': json.loads((E/'status/STATUS-panel.json').read_text()) if (E/'status/STATUS-panel.json').exists() else None,
           'qualified_cases': sorted(p.parent.name for p in (E/'cases').glob('*/QUALIFIED.json')),
           'free_gib': shutil.disk_usage(E).free // 2**30}, open(E/'OVERNIGHT_DONE.json', 'w'), indent=1)
EOF
log "done; free $(free_gib)G"

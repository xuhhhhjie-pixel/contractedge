#!/bin/bash
# After panel2: summaries, release panel-only cases, swap in the final ce-tools, freeze and run M5.
E=~/contractedge-eval-graph-repair-20261109; cd $E; export PATH=$E/bin:$PATH
SRC=~/contractedge-collab-20261023/worktrees/graph-repair-20261109/system/src
H=$SRC/contractedge/graph_repair/harness
PY=/tmp/contractedge-deepseek-preflight-20261019/venv312/bin/python
log() { echo "$(date -Is) $*" >> logs/to-m5.log; }
until grep -q '"state": "COMPLETED"' status/STATUS-panel2.json; do sleep 30; done
log "panel2 completed"
PYTHONPATH=$SRC $PY $H/summarize.py $E panel2 analysis/summary-panel2.json > /dev/null 2>> logs/to-m5.log
PYTHONPATH=$SRC $PY $H/summarize.py $E panel analysis/summary-panel.json > /dev/null 2>> logs/to-m5.log
PYTHONPATH=$SRC $PY $H/eval_linkers.py ~/contractedge-eval-front-half-20261106/gold-draft $E analysis/linker-recall-panel.json >> logs/to-m5.log 2>&1
log "panel summaries written"
pkill -f "[g]raph_eval.py daemon panel" ; pkill -f "[g]raph_eval.py daemon smoke"; sleep 3
cp $H/graph_eval.py graph_eval.py
KEEP=$($PY -c "import json;d=json.load(open('freeze/EVALUATION_LISTS.json'));print(' '.join(d['evaluation_without_holdout']+d['holdout']))")
for c in $($PY -c "import json;print(' '.join(json.load(open('freeze/EVALUATION_LISTS.json'))['panel']))"); do
  case " $KEEP " in *" $c "*) continue;; esac
  $PY graph_eval.py release $c >> logs/to-m5.log 2>&1 && log "released $c"
done
log "free $(df -BG --output=avail $E | tail -1 | tr -dc 0-9)G"
./swap_tools.sh >> logs/to-m5.log 2>&1 || { log "swap failed"; exit 1; }
[ -f runs-m5.json ] || $PY graph_eval.py runs m5 >> logs/to-m5.log 2>&1
log "m5 start"
$PY graph_eval.py daemon m5 >> logs/daemon-m5.log 2>&1
log "m5 exit $?"

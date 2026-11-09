#!/bin/bash
# Replace ce-tools only while no method run is active (the directory is bind-mounted into runs).
# The old package is kept as ce-tools-<ts>; tools-versions.jsonl records every swap.
set -eu
E=~/contractedge-eval-graph-repair-20261109
SRC=~/contractedge-collab-20261023/worktrees/graph-repair-20261109/system/src
PY=/tmp/contractedge-deepseek-preflight-20261019/venv312/bin/python
cd $E
busy() { for j in jobs/generate-*; do [ -f $j/STARTED.json ] && [ ! -f $j/DONE.json ] && return 0; done; return 1; }
while busy || pgrep -f "[p]repare_case150.py" >/dev/null; do sleep 20; done
old=ce-tools-$(date +%Y%m%d%H%M%S)
before=$(sha256sum ce-tools/MANIFEST.json | cut -d' ' -f1)
mv ce-tools $old
$PY $SRC/contractedge/graph_repair/harness/package_tools.py $SRC ce-tools
after=$(sha256sum ce-tools/MANIFEST.json | cut -d' ' -f1)
cmp -s $old/VARIANTS.json ce-tools/VARIANTS.json || { echo "variant table changed; refuse"; rm -rf ce-tools; mv $old ce-tools; exit 1; }
echo "{\"utc\": \"$(date -Is)\", \"previous\": \"$old\", \"previous_manifest_sha256\": \"$before\", \"manifest_sha256\": \"$after\"}" >> tools-versions.jsonl
cp $SRC/contractedge/graph_repair/harness/graph_eval.py graph_eval.py
echo "swapped $before -> $after (no phase resumed; start daemons explicitly)"

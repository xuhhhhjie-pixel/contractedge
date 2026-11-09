"""Agent configuration: livesweagent.yaml unchanged, with the three-phase guide when enabled.

Only the "Recommended Workflow" section of the instance template is replaced; the system
template, rules, formatting help, observation and error templates stay Live's.
"""
from __future__ import annotations

import copy

import yaml

START, END = '## Recommended Workflow', '## Important Rules'


def guide(v) -> str:
    parts = ['## Recommended Workflow (contract-graph guided)', '',
             'This is a cross-artifact repair: one configuration intent passes through several layers (declarations such as '
             'values keys, CRD fields, flags or env vars; templates or code that consume them; generated resources; runtime '
             'consumers). A fix is complete only when every consumer that should honour the intent does, and existing '
             'behaviour that should not change still holds. Do not wait for certainty before editing; iterate.', '']
    step = 1
    if v.graph or v.linkers:
        parts.append(f'{step}. Locate and map (keep it short). Use normal shell tools plus:')
        if v.graph or v.linkers:
            parts.append('   - `ce-search --text X [--scope DIR]` / `ce-search --symbol Name`, `ce-read HANDLE` or '
                         '`ce-read --path P --start N --end M`, `ce-follow HANDLE --selector pkg.Func` (Go imports)')
        if v.linkers:
            parts.append('   - `ce-links SEED` lists candidate cross-artifact edges (Helm values<->templates, Kustomize, Go '
                         'struct tags, CRD fields, env vars/flags, dependency APIs). SEED is a key, field, ENV name, '
                         '`path` or `path:line`.')
        if v.graph:
            parts.append('   - Record what matters: `ce-graph add-node --role CONFIG_DECLARATION|PRODUCTION_CONSUMER|'
                         'GENERATED_STATE|RUNTIME_CONSUMER|OBSERVED_STATE --path P --span START-END --anchor A`, '
                         '`ce-graph add-edge --type PROPAGATION|TRANSFORMATION|BINDING|SYNCHRONIZATION|RECONCILIATION '
                         '--from N1 --to N2 --status VIOLATED|EXPECTED|UNKNOWN --note "..."`, and behaviour that must '
                         'not change: `ce-graph preserve --edge E2 --scenario "..."`. `ce-graph show` prints the graph.')
        step += 1
    if v.closure or v.probes:
        parts.append(f'{step}. Closure and probes (cheap, once):')
        if v.closure:
            parts.append('   - `ce-closure E1` lists other consumers of the same declaration; decide each one with '
                         '`ce-graph decide --of E1 --candidate N3 --decision NEEDS_FIX|NO_FIX --reason "..."`.')
        if v.probes:
            parts.append('   - Write small probes for violated edges and preserved behaviour. `ce-probe gen` shows what is '
                         'missing. Prefer render/static checks: `ce-probe add --edge E1 --kind violate --cmd "helm template '
                         '/work/chart --set a.b=x | grep -q ..."` (runs through ce-run in the published runtime; add '
                         '`--local` for a check in /testbed). Then run `ce-probe calibrate` once: it runs new probes on the '
                         'original version (a violate probe must fail there, a preserve probe must pass) and drops the '
                         'others. Probe time is capped; do not spend long here.')
        step += 1
    loop = f'{step}. Edit and verify loop: edit the source, then run `ce-public`'
    loop += ' and `ce-probe run`.' if v.probes else '.'
    if v.graph and v.linkers:
        loop += (' After your first edit of a file, the executor links it to the declaration it consumes and lists other '
                 'consumers of the same declaration in a [ce-auto] note' +
                 ('; decide each listed consumer (NEEDS_FIX and edit it, or NO_FIX with a reason).' if v.closure else '.'))
    if v.attribution:
        loop += ' Failures are mapped back to graph nodes and edges automatically.'
    if v.checkpoint:
        loop += (' Every change is checkpointed automatically; `ce-ckpt list` shows them and `ce-rollback N|best` '
                 'restores one.')
    parts.append(loop)
    step += 1
    if v.stop_rule:
        parts.append(f'{step}. Submit with `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` (alone). Submitting runs the stop '
                     'rule automatically (`ce-submit --check` runs it earlier): the public check passes'
                     + (', calibrated probes pass' if v.probes else '') + (', closure candidates are decided' if v.closure else '')
                     + ' and the changed files follow the submission rules. Unmet items are reported back so you can '
                     'continue; near the end of the time budget the best candidate is submitted automatically.')
    else:
        parts.append(f'{step}. When the public check and your probes pass, submit with '
                     '`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` (alone). `ce-submit --check` is available as a '
                     'self-check.')
    parts += ['', 'The ce-* commands are ready to use as described (each has --help); there is no need to read their '
                  'implementation or their state files. Tool state lives in /ce-state. Never leave helper scripts, notes '
                  'or test files under /testbed: every untracked file there is exported in the submitted patch.', '']
    return '\n'.join(parts)


def agent_config(live_yaml: str, variant) -> dict:
    config = yaml.safe_load(live_yaml)
    cfg = copy.deepcopy(config['agent'])
    if variant.prompt == 'graph':
        template = cfg['instance_template']
        start, end = template.index(START), template.index(END)
        cfg['instance_template'] = template[:start] + guide(variant) + template[end:]
    elif variant.prompt != 'live':
        raise ValueError(f'unknown prompt {variant.prompt!r}')
    return {'config': config, 'agent': cfg}

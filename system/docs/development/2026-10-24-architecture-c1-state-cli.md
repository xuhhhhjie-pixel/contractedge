# C1 state transitions and persistent CLI run recording

This development stage is stacked on PR #10, base commit
`022d1827eeaf0628800a85315810d2600a6f0663`. The current snapshot and its
validation JSON define the exact validated version. `formal_ready=false`.

## State and decisions

StageResult classifies existing front, edit, observation, infrastructure,
budget and unknown-response results. RepairState and pure transitions route
the actual runner and repair loop; they reference existing artifacts and calls
rather than maintaining another semantic plan or global budget.
Committed decisions and completed side effects have separate records.
Policy quotas retain the original global call, token, round, time and money caps.

Source changes during diagnosis stop planning against a stale snapshot.
The two final state projections preserve a primary provider/engine exception
when recording itself fails. State persistence is not proof of a completed edit,
public check or candidate promotion.

## Standard CLI recording

`contractedge run --output TRACE.json` retains its output-file interface.
`TRACE.json.artifacts/` holds the owned immutable source, input identities,
ProviderJournal and the existing BudgetedProvider's actual dispatch facts.
Output/claim reuse and identity conflicts are rejected before dispatch.
No recording adapter creates a replacement money allowance or retries an
unknown request. Explicit live budgets use the previously authorized money
ledger; no key value is stored in recording artifacts.

Final request quotes and semantic-delivery witnesses use that same budget
instance. Format corrections remain separately counted actual calls.
Source/config/input identities are checked at final publication; recording
failure cannot leave a successful READY artifact as the authoritative result.
The unrecorded programmatic entry remains an explicit legacy compatibility path.

## Verification and limits

The stage fixture panel includes state transitions, primary-error preservation,
CLI claim/identity/publication counterexamples, and the inherited A2 delivery
and isolated public tests. Five fixed Full repair fixtures compare actual
requests, call/reservation totals, routes, feedback, selected snapshot/patch
and terminal status; CLI front comparisons cover format-correction variants.
Exact commands and results are in the same-version validation report.

The portable development subset retains six historical/native exclusions;
vendored native integration and opt-in actual isolated tests are reported
separately. No test count is a research outcome.

This stage does not implement arbitrary candidate crash recovery. C2/D1 and D2
remain separate integration units. Seven historical worker front paths and
offline tape replay do not gain this CLI journal merely because the standard
run command does. No formal150, hidden evaluation or real model HTTP is part
of this stage's regression validation.

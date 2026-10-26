# Existing plan identity boundary clarification

Source052 delivered concrete subscription context through diagnosis/re-diagnosis/planning, then failed before execution. Its plan declared preserve_constraints=[] but preservation_impact used outcomeO1. The exact draft was supplied to correction, but only a generic completeness error identified the problem; the model repeated the same row.

Use the existing schema and validator within the approved complete-feedback work package. When the diagnosis allows no preserve entries, constrain impact to empty as well. In every other case, the model still defines preservation and its IDs. Feedback explicitly distinguishes plan preserveIDs from outcomeIDs, lists expected/missing/unexpected/duplicate identities, and states empty-set meaning. Never generate/remove semantic entries automatically; never relax validation.

Alternatives: more retries leaves ambiguous feedback; automatic deletion hides intent; an extra agent/module is unnecessary. Choose explicit schema plus existing-validator set differences. Arbitrary cross-array identity equality remains validator-owned. Initial diagnosis, planning correction and re-diagnosis paths must use the same generic rule.

Existing context, Top12,12calls/2rounds, one extension unchanged; all old failures retained. Source052 plan still suggested an asynchronous start reorder, whose behavior has not been verified. A formatting improvement cannot prove a correct lifecycle strategy. Exact historical replay, renamed counterexamples, full engineering and isolation qualifications precede any real invocation. No hidden checks, GT/Fixed, old successful patches or evaluator details enter method input.

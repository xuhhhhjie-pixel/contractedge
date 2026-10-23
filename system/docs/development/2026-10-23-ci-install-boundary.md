# Python 3.10 installer boundary (2026-10-23)

The initial push and PR workflows passed native, public examples, Python3.12 and actual Docker/portable jobs. Python3.10 failed during editable installation before tests. pip26.2.1 with the global src/system PYTHONPATH discovered the newly generated package metadata and resolved the original PyYAML==5.4.1 dependency against the explicit development PyYAML6.0.3 environment, despite --no-deps.

An independent Python3.10.12/pip26.2.1/PyYAML6.0.3 venv reproduced that exact failure. Repeating the same editable command with env -u PYTHONPATH succeeded. Every workflow pip command now clears only that installation variable; runtime source selection remains explicit. Original pyproject dependencies and requirements-dev.lock are unchanged.

The ci1 freeze binds the changed installer workflow and current entry documents. Its runtime source/configuration/fixed panel match the prior freeze exactly; existing offline test evidence remains valid and was not recounted as a new run. The original freeze is preserved. Actual rerun results must be read from PR #7; the current snapshot records only what was known at freeze time.

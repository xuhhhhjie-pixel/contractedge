# R5 public task review packet v2

All entries are drafts, not approvals and not MethodInput. Approve only when the public source states the task faithfully, the task does not reveal the repair strategy, and the source matches the specific benchmark case.
Response format: `APPROVE: case IDs` or `REVISE: case ID — replacement text`. For any mismatch, use `REJECT: case ID — reason`.

## RW-ALERTMANAGER-5040

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** HIGH · **Method eligible:** False

**Draft task:** An AlertmanagerConfig that declares an IPv6 SMTP smarthost must remain valid after configuration is serialized and reloaded, so Alertmanager can become ready and use the configured address.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [HostPort.String() breaks IPv6 addresses - should use net.JoinHostPort()](https://github.com/prometheus/alertmanager/issues/5039) · SHA-256 `bd488ebbf06e3f6a21bd6a71dd33e446b2fb3363649df042c3c6ce457f071a31` · evidence `issue-section:Expected Behavior`

## RW-ALLOY-6818

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** HIGH · **Method eligible:** False

**Draft task:** When labels for an already-running Docker container target change, subsequent log entries should use the updated labels without requiring an Alloy restart.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [loki.source.docker: label changes silently ignored until restart](https://github.com/grafana/alloy/issues/6714) · SHA-256 `d8bab5757d5311310666d73d97b3d12f2db161b7ccdbd85e3d29d3d5dcf604cf` · evidence `issue-section:What's wrong?, issue-section:Steps to reproduce`

## RW-COREDNS-7562

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** When a Corefile update triggers a reload as Kubernetes is terminating a Pod, CoreDNS should finish a clean shutdown within the Pod grace period without deadlocking.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [plugin/reload deadlocks with SIGTERM](https://github.com/coredns/coredns/issues/7314) · SHA-256 `472b59f68557c87d513d3ecd68dea355b945240e25be856d1680b3eab5027ab1` · evidence `issue-section:What happened, issue-section:What you expected to happen`

## RW-DAPR-10220

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** The timeout for Dapr binding OPTIONS discovery requests should be configurable.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [Make the timeout for bindings OPTIONS request configurable](https://github.com/dapr/dapr/issues/10185) · SHA-256 `7899f60952ae5165a16c6acdc1eb13d5a7a676119e0a40532c771c37342bfdb1` · evidence `issue-section:Expected Behavior, issue-section:Steps to Reproduce the Problem`

## RW-DAPR-9173

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** Dapr 1.16 should start successfully on Kubernetes clusters configured with a custom DNS suffix other than .cluster.local.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [Dapr 1.16 fails on clusters using custom DNS suffix (non-.cluster.local)](https://github.com/dapr/dapr/issues/9172) · SHA-256 `63ef433183da6213df2fd686f7749e6b9d45c24bdc99c8b38a18862507538699` · evidence `issue-section:Expected Behavior, issue-section:Steps to Reproduce the Problem`

## RW-FLUENT-BIT-10720

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** After a hot reload, Fluent Bit should resume input and output processing and continue responding to SIGHUP and SIGTERM.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [🐛 Endless loop on reload: Fluent Bit stops log processing and handling of SIGHUP/SIGTERM](https://github.com/fluent/fluent-bit/issues/10670) · SHA-256 `a13dd9e63046e569cbff4bfd32626806ea39ada442cc304a1b9fa880ec62d351` · evidence `issue-section:Expected behavior, issue-section:Observe results`

## RW-FLUENT-BIT-12495

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** MEDIUM · **Method eligible:** False

**Draft task:** A Fluent Bit YAML configuration should load an existing include when it is given as a relative path from the process working directory or as a Windows absolute path.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [config_format: yaml relative includes no longer resolve against the working directory (regression in 2.1.9)](https://github.com/fluent/fluent-bit/issues/12488) · SHA-256 `1a281ed6873bdb02f040d9ed648fa21715e4963ef5e3c81ab6e439264bfca59d` · evidence `issue-section:Describe the bug`

**Primary source:** [config_format: yaml `includes` with Windows absolute paths are resolved as relative](https://github.com/fluent/fluent-bit/issues/12489) · SHA-256 `1e5ad2f5793290a4a34ba4d5e8eedf7d9df111719a42cc58c972b292b4bf77e9` · evidence `issue-section:Describe the bug`

## RW-KUBE-STATE-METRICS-2918

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** When the Custom Resource State configuration file exists, setting the continue-without-config-file option should not prevent that configuration from loading or its metrics from being generated.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [--continue-without-custom-resource-state-config-file prevents CRS metrics from loading when config file exists](https://github.com/kubernetes/kube-state-metrics/issues/2917) · SHA-256 `c628241527816a5c7a822c3f2927a5805220c92cea95ffa5bebf6e10f2adaaa1` · evidence `issue-section:What you expected to happen, issue-section:How to reproduce it`

## RW-KUBELET-133072

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** For a Pod with restartPolicy Never and a restartable init sidecar whose startup probe fails once and later succeeds, kubelet should allow the regular container to start.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [main container never starts with restartPolicy: Never and sidecar container has a startupProbe and fails the first attempt](https://github.com/kubernetes/kubernetes/issues/132826) · SHA-256 `4b8098a9361950296d276b727069f3522e75de772798e7f91cbc4874f913e9d2` · evidence `issue-section:What happened, issue-section:expected behavior`

## RW-KUBELET-139275

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** After a node recovers, Pods on another node using a subPath mount from a GlusterFS ReadWriteMany volume should start when the requested host path exists, rather than remain stuck with a mount error.

**Review note:** Derived from public user-reported behavior/expectation sections; no PR body, patch, private GT, or verifier text was used to write the task.

**Primary source:** [After master0 node recovered, pods on master2 with glusterfs ReadWriteMany PVC fail to mount: "no such file or directory" (path exists on host)](https://github.com/kubernetes/kubernetes/issues/139123) · SHA-256 `ba80b812926e5db74319ea06b1227e101a7aec4d9387022540ac209498b74ca9` · evidence `issue-section:What happened`

## RW-KUBELET-126806

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** HIGH · **Method eligible:** False

**Draft task:** Allow a Pod to mount an OCI image or artifact as a volume so containers can read and share its packaged files without rebuilding their application images. The volume should follow the image reference and pull behavior declared by the workload.

**Review note:** Drafted only from the public enhancement description and user-story sections of Kubernetes Enhancement issue #4639. The issue states the broad ImageVolume goal; it does not independently establish the case-specific VolumeAttributes behavior, so human case/source fidelity review is required before any export.

**Primary source:** [VolumeSource: OCI Artifact and/or Image](https://github.com/kubernetes/enhancements/issues/4639) · SHA-256 `2c829b64356b59d361e4c7c2c7170fc2745c740cf016804d922ab70aecbd4fd2` · evidence `issue-section:Enhancement Description, issue-section:User Stories`

## Additional issue-origin drafts (2026-10-05)

These four cases now have concise task drafts derived from linked public issue reports. They remain **DRAFT_PENDING_HUMAN_REVIEW** and **not eligible for MethodInput**; the linked PR descriptions and code excerpts were excluded. The Kyverno task includes three issue-level behavioral claims and must be checked against the case verifier before approval.

| Case | Public issue | Current draft scope | Status |
|---|---|---|---|
| `RW-ISTIO-2944` | [istio/istio#3515](https://github.com/istio/istio/issues/3515) | Reload changed injector configuration for subsequent injections without manual pod deletion | Pending human source/task review |
| `RW-KYVERNO-17310` | [kyverno/kyverno#17292](https://github.com/kyverno/kyverno/issues/17292) | IPv6-addressed egress block behavior, proxy behavior, and denial-message information exposure | Pending scope-to-verifier review |
| `RW-OTEL-COLLECTOR-10795` | [opentelemetry-collector#10787](https://github.com/open-telemetry/opentelemetry-collector/issues/10787) | Ignore variable-like text in comments in included configuration | Pending human source/task review |
| `RW-OTEL-COLLECTOR-15868` | [opentelemetry-collector#15867](https://github.com/open-telemetry/opentelemetry-collector/issues/15867) | Keep escaped URI literal while expanding a later valid URI | Pending human source/task review |

The source index now identifies 14/30 cases with issue evidence (17 issue sources), 14/30 with declaration/observation fixtures, and 4 cases with neither. These counts indicate source availability only; they are not case approval or public-input qualification.

### Additional draft: `RW-OTEL-OPERATOR-2171`

**Status:** DRAFT_PENDING_HUMAN_REVIEW · **Leakage risk:** LOW · **Method eligible:** False

**Draft task:** When a scrape configuration changes only in a relabeling regex, the TargetAllocator should treat it as changed and return the updated configuration to consumers rather than return the stale prior configuration.

**Sources:** [Issue #1926](https://github.com/open-telemetry/opentelemetry-operator/issues/1926) and [issue #1358](https://github.com/open-telemetry/opentelemetry-operator/issues/1358). The linked PR says it addresses both; a reviewer must confirm these reports match this case's archive and verifier. PR implementation details and code excerpts were excluded.

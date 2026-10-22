# R5 public task text review — 10 issue-backed drafts

**SUPERSEDED. Do not review or approve this packet.** It was replaced by `public-task-review-v2.md`, which includes digest-bound provenance for 11 drafts, removes three unsupported task claims, and adds an explicit R5 case/source fidelity note. No entry from this v1 packet was exported to MethodInput.

Review scope: requirement text only. These are candidate MethodInput prompts derived from captured public GitHub issue bodies, not approved release contents. Task statements intentionally omit PR bodies, patches, private GT, verifier code, and repair-family labels. Verify whether each sentence faithfully states the issue’s required user-visible behavior and does not disclose or presume a repair strategy.

Reply with `APPROVE: <case IDs>` and `REVISE: <case ID> — <replacement text>`; approval applies only to the listed sentence and cited source. No case is exported until its task text and public provenance pass human review.

## RW-ALERTMANAGER-5040

> An AlertmanagerConfig that declares an IPv6 SMTP smarthost must remain valid after configuration is serialized and reloaded, so Alertmanager can become ready and use the configured address.

- Public source: https://github.com/prometheus/alertmanager/issues/5039
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:Expected Behavior

Human decision: pending

## RW-ALLOY-6818

> When labels for an already-running Docker container target change, subsequent log entries should use the updated labels without requiring an Alloy restart.

- Public source: https://github.com/grafana/alloy/issues/6714
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:What's wrong?, issue-section:Steps to reproduce

Human decision: pending

## RW-COREDNS-7562

> When a Corefile update triggers a reload as Kubernetes is terminating a Pod, CoreDNS should finish a clean shutdown within the Pod grace period without deadlocking.

- Public source: https://github.com/coredns/coredns/issues/7314
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:What happened, issue-section:What you expected to happen

Human decision: pending

## RW-DAPR-10220

> The timeout for Dapr binding OPTIONS discovery requests should be configurable.

- Public source: https://github.com/dapr/dapr/issues/10185
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:Expected Behavior, issue-section:Steps to Reproduce the Problem

Human decision: pending

## RW-DAPR-9173

> Dapr 1.16 should start successfully on Kubernetes clusters configured with a custom DNS suffix other than .cluster.local.

- Public source: https://github.com/dapr/dapr/issues/9172
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:Expected Behavior, issue-section:Steps to Reproduce the Problem

Human decision: pending

## RW-FLUENT-BIT-10720

> After a hot reload, Fluent Bit should resume input and output processing and continue responding to SIGHUP and SIGTERM.

- Public source: https://github.com/fluent/fluent-bit/issues/10670
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:Expected behavior, issue-section:Observe results

Human decision: pending

## RW-FLUENT-BIT-12495

> A Fluent Bit YAML configuration should load an existing include when it is given as a relative path from the process working directory or as a Windows absolute path.

- Public source: https://github.com/fluent/fluent-bit/issues/12488
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:Describe the bug
- Public source: https://github.com/fluent/fluent-bit/issues/12489
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:Describe the bug

Human decision: pending

## RW-KUBE-STATE-METRICS-2918

> When the Custom Resource State configuration file exists, setting the continue-without-config-file option should not prevent that configuration from loading or its metrics from being generated.

- Public source: https://github.com/kubernetes/kube-state-metrics/issues/2917
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:What you expected to happen, issue-section:How to reproduce it

Human decision: pending

## RW-KUBELET-133072

> For a Pod with restartPolicy Never and a restartable init sidecar whose startup probe fails once and later succeeds, kubelet should allow the regular container to start.

- Public source: https://github.com/kubernetes/kubernetes/issues/132826
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:What happened, issue-section:expected behavior

Human decision: pending

## RW-KUBELET-139275

> After a node recovers, Pods on another node using a subPath mount from a GlusterFS ReadWriteMany volume should start when the requested host path exists, rather than remain stuck with a mount error.

- Public source: https://github.com/kubernetes/kubernetes/issues/139123
- Captured issue-body SHA-256: ``
- Evidence refs: issue-section:What happened

Human decision: pending

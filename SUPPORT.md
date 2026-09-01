# Support

K8s Telemetry MCP Server is free and open source under the Apache License 2.0. Support is
community best-effort — there is no paid support tier for this project and no SLA.

## How to Get Help

| Channel | Use for |
|---------|---------|
| [GitHub Issues](https://github.com/kubeopsai/k8s-telemetry-mcp/issues) | Bugs, feature requests, questions |
| [GitHub Discussions](https://github.com/kubeopsai/k8s-telemetry-mcp/discussions) | Setup help, usage patterns |
| support@kubeopsai.net | Security reports (please do not file these publicly) |

## What to Expect

This is maintained by a very small team. Realistically:

- Issues are usually acknowledged within a few business days
- Security reports are prioritised over everything else
- Clear reproductions get fixed far faster than vague ones
- Feature requests are read and considered, but not promised

Business hours are Monday–Friday, roughly 09:00–18:00 CET.

Earlier versions of this file listed paid support tiers with response-time SLAs. Those
applied to a commercial version of this server that no longer exists. There is no paid
support offering for this project.

## Reporting a Bug

Please include:

1. **Version** — from the startup log:
   ```bash
   kubectl logs -n monitoring -l app.kubernetes.io/name=k8s-telemetry-mcp | head -5
   ```
2. **Which tool failed**, and the prompt or arguments that triggered it
3. **The full error** returned by the tool
4. **Your backend** — Loki/Prometheus/Tempo, Datadog, or CloudWatch
5. **Your assistant** — Amazon Q, Claude Desktop, Kiro, Cursor, other
6. **Kubernetes version** and how the cluster was provisioned (EKS, GKE, kind, …)

## Commercial Product

If you need this investigation to run automatically on alerts rather than on demand,
[KubeOpsAI Agent](https://kubeopsai.net) is the commercial product and comes with paid
support. It is a separate offering; nothing here depends on it.

Full details: https://github.com/kubeopsai/k8s-telemetry-mcp/wiki/Support

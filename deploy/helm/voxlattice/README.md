# VoxLattice Helm chart

Deploys the CUDA-accelerated voice-isolation gRPC server. The verified model
checkpoint ships inside the image, so the chart creates no volumes and performs
no downloads.

Prerequisites, install instructions, the TLS model, and troubleshooting are
documented in [`docs/kubernetes.md`](../../../docs/kubernetes.md).

```bash
helm upgrade --install voxlattice deploy/helm/voxlattice \
  --namespace voxlattice --create-namespace \
  --set auth.existingSecret=voxlattice-api-token
```

## Values

| Key | Default | Purpose |
|---|---|---|
| `image.repository` | `ghcr.io/mayflower/voxlattice` | Server image |
| `image.tag` | `edge` | Rolling main build; pin a release or `sha-` tag |
| `replicaCount` | `1` | Replicas, each loading its own model instance |
| `deploymentStrategy.type` | `Recreate` | Avoids waiting for a GPU the outgoing pod holds |
| `auth.existingSecret` | `""` | Secret holding the bearer token |
| `auth.secretKey` | `api-token` | Key within that Secret |
| `auth.token` | `""` | Inline token; evaluation only |
| `tls.mode` | `certManager` | `certManager`, `existingSecret`, or `insecure` |
| `tls.certManager.issuerRef` | `{}` | Existing CA issuer; empty creates a namespace-local CA |
| `tls.clientCA.enabled` | `false` | Require and verify client certificates |
| `server.*` | see `values.yaml` | Ports and limits from `docs/configuration.md` |
| `gpu.resourceCount` | `1` | GPU units per pod |
| `gpu.runtimeClassName` | `""` | Set to `nvidia` where a RuntimeClass is required |
| `gpu.mps.enabled` | `false` | Mount the MPS control directory |
| `gpu.mps.pinnedDeviceMemoryLimit` | `""` | VRAM cap, for example `0=8G` |
| `podSecurityContext` | non-root, UID/GID 65532 | Override (e.g. `runAsUser: 0`) where NVIDIA MPS requires matching the server's UID |
| `service.headless.enabled` | `false` | Pod addresses for client-side load balancing |
| `ingressRoute.enabled` | `false` | Publish gRPC through a Traefik IngressRoute |
| `serviceMonitor.enabled` | `false` | Register `/metrics` with the Prometheus Operator |
| `networkPolicy.enabled` | `false` | Default-deny ingress with explicit peers |
| `startupProbe.failureThreshold` | `72` | Six minutes for model verification and CUDA warm-up |
| `terminationGracePeriodSeconds` | `""` | Defaults to `server.gracefulShutdownS` plus headroom |

`values-data-muc.yaml` is the overlay for the data-muc cluster.

Validate changes with `make helm-check` from the repository root.

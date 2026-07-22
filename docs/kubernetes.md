# Kubernetes deployment

The chart in [`deploy/helm/voxlattice`](../deploy/helm/voxlattice) deploys the
inference server on a cluster with NVIDIA GPUs. It is the Kubernetes
counterpart to the Compose setup in [Operations](operations.md); the server
contract, environment variables, and limits are identical.

The verified checkpoint is copied into the image at build time, so no volume,
init container, or download step is needed at runtime. A pod needs a GPU, a
bearer token, and — unless explicitly disabled — a TLS key pair.

## Prerequisites

- A node with an NVIDIA GPU and the [device plugin](https://github.com/NVIDIA/k8s-device-plugin)
  advertising `nvidia.com/gpu`
- [cert-manager](https://cert-manager.io/) when `tls.mode` is `certManager`
- Traefik with the `traefik.io/v1alpha1` CRDs when `ingressRoute.enabled` is set
- Prometheus Operator when `serviceMonitor.enabled` is set
- Helm 3.8 or newer

Confirm that the cluster can schedule GPU work before deploying:

```bash
kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\\.com/gpu
```

## Bearer token

The server refuses to start without a token of at least 16 characters. Create
the Secret before the first install and reference it with `auth.existingSecret`,
so that the token never passes through a values file:

```bash
kubectl create namespace voxlattice
kubectl -n voxlattice create secret generic voxlattice-api-token \
  --from-literal=api-token="$(openssl rand -hex 32)"
```

`auth.token` renders the token into a chart-managed Secret instead. That stores
it in plain text wherever the values live, so use it only for local evaluation.

Rotate a token by replacing the Secret and restarting the deployment. Active
streams are drained rather than cut:

```bash
kubectl -n voxlattice rollout restart deployment/voxlattice
```

## Install

```bash
helm upgrade --install voxlattice deploy/helm/voxlattice \
  --namespace voxlattice --create-namespace \
  --set auth.existingSecret=voxlattice-api-token \
  --set image.tag=sha-<40-character commit>
```

Pin a release version or an immutable `sha-` tag. The chart default `edge` is
the rolling build of the main branch.

Startup performs model verification, CUDA warm-up, and a state-isolation self
test *before* the HTTP listener binds, so nothing answers `/healthz` for the
first few seconds to minutes. The chart therefore gates liveness and readiness
behind a startup probe that allows six minutes by default. Raise
`startupProbe.failureThreshold` on slow or heavily shared GPUs rather than
loosening the liveness probe.

## Transport security

`tls.mode` selects how the gRPC port is protected.

| Mode | Behaviour |
|---|---|
| `certManager` (default) | cert-manager issues the server certificate |
| `existingSecret` | mounts a `kubernetes.io/tls` Secret you manage |
| `insecure` | plaintext gRPC via `ALLOW_INSECURE_GRPC=true` |

Public ACME issuers cannot sign `.svc.cluster.local` names. With
`tls.mode=certManager` and no `tls.certManager.issuerRef`, the chart therefore
creates a namespace-local self-signed CA and issues the server certificate from
it, covering every in-cluster name a client may dial. Point
`tls.certManager.issuerRef` at an existing CA-type issuer to use a cluster-wide
internal PKI instead.

Clients must trust the CA. Export it with:

```bash
kubectl -n voxlattice get secret voxlattice-server-tls \
  -o jsonpath='{.data.ca\.crt}' | base64 -d > voxlattice-ca.crt
```

For mutual TLS, create a Secret holding the CA that signed your client
certificates and set `tls.clientCA.enabled=true` with
`tls.clientCA.existingSecret`. The server then requires and verifies a client
certificate. Clients still send the bearer token: TLS protects the connection,
the token authorizes access.

`tls.mode=insecure` sends audio and the bearer token unencrypted. Restrict it
with `networkPolicy.enabled` and never combine it with external exposure; the
chart rejects that combination.

## Client configuration

In-cluster clients dial the ClusterIP Service:

```python
processor = RemoteFastEnhancer(
    endpoint="dns:///voxlattice.voxlattice.svc.cluster.local:50051",
    api_key=os.environ["FASTENHANCER_API_TOKEN"],
    tls=True,
    root_certificates=pathlib.Path("/etc/voxlattice/ca.crt").read_bytes(),
)
```

Each processor owns one channel and therefore one TCP connection, so
connections from many tracks are spread across ready pods by kube-proxy. Do not
move a live track between endpoints: a gRPC stream carries per-stream model
state and must stay on the pod that started it.

Run the LiveKit agent in the cluster, next to the server. The real-time plugin
waits only `response_wait_ms` for each interval's enhanced samples before
emitting raw audio, and that window must cover the server's per-hop latency.
Size it from the `fastenhancer_hop_end_to_end_seconds` histogram — on a
GPU shared through MPS the hop runs several times the inference time, well past
the 12 ms default. See [LiveKit integration](livekit.md#tuning).

Enable `service.headless.enabled` only for clients that implement their own
load balancing. The bundled plugin uses the default `pick_first` policy, which
would pin every channel to the first resolved pod.

See [LiveKit integration](livekit.md) for the full processor lifecycle.

## GPU scheduling

`gpu.resourceCount` GPU units are requested per pod, and each replica loads its
own model instance. Set `gpu.runtimeClassName` on clusters that expose the
NVIDIA container runtime through a RuntimeClass.

Where the device plugin advertises several replicas per physical GPU through
NVIDIA MPS, set `gpu.mps.enabled=true`. The chart then mounts the MPS control
directory and points `CUDA_MPS_PIPE_DIRECTORY` at it. Also set
`gpu.mps.pinnedDeviceMemoryLimit` (for example `0=8G`) so that this workload
cannot exhaust the VRAM of everything else sharing the device.

MPS serves **one UID at a time**. The control daemon runs a single MPS server
owned by whichever user connected first, and a client running under a different
UID is queued until that server can be torn down — which never happens while
other workloads are using it. CUDA initialisation then blocks indefinitely: the
process does not fail, it hangs, and the startup probe eventually restarts it.

The image runs as UID 65532. Where the node's MPS server is owned by another
user, set `podSecurityContext.runAsUser` to match it. Confirm the owner from
the daemon before assuming:

```bash
kubectl -n <plugin-namespace> logs <mps-control-daemon-pod> --all-containers \
  | grep -E 'NEW CLIENT|Server .* has .* active'
```

`NEW CLIENT … from user <uid>: Server is not ready, push client to pending
list` is the signature of this mismatch. Leaving such a pod running is not
harmless: while it waits, the daemon repeatedly tries to shut down the server
the other GPU workloads depend on.

File permissions on the pipe directory are a separate and lesser concern; the
daemon creates the `control` socket world-accessible by default, so they are
rarely the cause.

MPS units are a finite, cluster-wide pool. Confirm a free unit exists before
raising `replicaCount` or switching to `RollingUpdate`, which needs one spare
unit for the surge replica:

```bash
kubectl get node <gpu-node> -o jsonpath='{.status.allocatable.nvidia\.com/gpu}{"\n"}'
```

`deploymentStrategy` defaults to `Recreate`, because with one dedicated GPU per
pod a rolling update would wait forever for a device the outgoing pod still
holds. Switch to `RollingUpdate` only where spare GPU capacity exists, such as
under MPS.

## External exposure

`ingressRoute.enabled` publishes the gRPC port through a Traefik IngressRoute.
Traefik terminates a publicly trusted certificate at the edge and opens a
separate TLS connection to the pod, negotiating HTTP/2 over ALPN. A
`ServersTransport` validates the backend certificate against the in-cluster
name it was issued for.

An IngressRoute does not honour the cert-manager ingress annotation, so the
chart requests the edge certificate explicitly through
`ingressRoute.certificate.issuerRef`.

The health and metrics port is never exposed externally.

## Observability

`serviceMonitor.enabled` registers the unauthenticated `/metrics` endpoint with
the Prometheus Operator. Metrics carry no audio, tokens, stream identifiers,
room names, or participant identities.

Watch `fastenhancer_active_streams` against `MAX_ACTIVE_STREAMS`, and
`fastenhancer_stream_rejections` for capacity pressure, before raising
concurrency. Larger queue bounds add latency and memory use without adding GPU
capacity; see [Benchmarking](benchmarking.md).

## Shutdown

On `SIGTERM` the server clears readiness, marks gRPC health as not serving, and
drains active RPCs for up to `server.gracefulShutdownS`.
`terminationGracePeriodSeconds` defaults to that value plus headroom for
scheduler teardown, so raising the drain window automatically widens the grace
period.

## Deploying through ArgoCD

[`values-data-muc.yaml`](../deploy/helm/voxlattice/values-data-muc.yaml) is the
overlay for the data-muc cluster: MPS-shared GPU, the `nvidia` RuntimeClass, an
IngressRoute on `voxlattice.data.mayflower.zone` with the
`letsencrypt-intern-dns` issuer, and a ServiceMonitor. It runs the pod as root
(`runAsUser: 0`) because that cluster's MPS server is root-owned; see the GPU
scheduling section above.

The matching `Application` lives in the cluster's GitOps repository at
`data-muc/platform-apps/voxlattice.yaml` and tracks `deploy/helm/voxlattice`
from this repository. The chart's own `values.yaml` is always the base, so the
Application only layers the overlay on top.

The bearer token is deliberately not part of the overlay. Create the
`voxlattice-api-token` Secret in the target namespace before the first sync,
either with the command above or through the cluster's SOPS workflow.

## Validation

`make helm-check` lints the chart and validates every manifest it renders
across the supported value combinations. CI runs the same target.

## Troubleshooting

### Pod never becomes ready

Check the logs for model hash, CUDA, or warm-up errors:

```bash
kubectl -n voxlattice logs deployment/voxlattice
```

A pod stuck in `Pending` usually means no node advertises a free
`nvidia.com/gpu` unit. Confirm with `kubectl describe pod`.

### Certificate stays in a pending state

```bash
kubectl -n voxlattice describe certificate voxlattice-server
```

With the chart-managed CA, the server certificate cannot be issued until the CA
certificate itself is ready. cert-manager retries automatically.

### Clients receive `UNAVAILABLE` with a certificate error

The client does not trust the issuing CA, or it dials a name the certificate
does not cover. Re-export `ca.crt` and dial one of the names in the
certificate's `dnsNames`.

### Clients receive `UNAUTHENTICATED`

The token in the Secret and the token used by the client differ. Confirm the
mounted file contains no trailing newline beyond what the server strips.

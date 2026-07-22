# Changelog

All notable changes to VoxLattice will be documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Open-source project identity, governance, contribution guidance, security
  policy, community standards, and GitHub collaboration templates.
- User-focused installation, LiveKit integration, configuration, TLS,
  operations, and benchmarking documentation.
- Source-installable package metadata and TLS/mTLS Docker Compose overlays.
- Kubernetes Helm chart (`deploy/helm/voxlattice`) with a deployment guide,
  cert-manager TLS, an optional Traefik gRPC ingress, a ServiceMonitor, and a
  NetworkPolicy, plus configurable pod and container security contexts.
- `tools/denoise_check.py` to measure a running server's silence-gap
  noise-floor reduction against a deterministic test clip.

### Fixed

- Published container image did not start: the virtualenv interpreter was left
  dangling and `/opt/model` lost its execute bit. The Dockerfile now builds
  against the base-image interpreter and creates the model directory explicitly.

## [0.1.0] - 2026-07-21

### Added

- CUDA-only FastEnhancer-B streaming server with bounded multi-stream batching.
- Versioned authenticated bidi-gRPC protocol with TLS and mTLS support.
- Track-local LiveKit `FrameProcessor` with aligned fail-open behavior.
- Reproducible model verification, container deployment, tests, benchmarks,
  CI, release candidates, SBOM generation, and security scanning.

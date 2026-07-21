# Security policy

## Supported versions

Until VoxLattice reaches 1.0, security fixes are provided for the latest
release on the `main` branch.

| Version | Supported |
|---|---|
| Latest 0.x release | Yes |
| Older 0.x releases | No |

## Reporting a vulnerability

Do not open a public issue, discussion, or pull request for a suspected
vulnerability.

Use GitHub's private vulnerability reporting feature from the repository's
Security tab. If private reporting is unavailable, contact a maintainer using
a private method listed on their GitHub profile and include "VoxLattice
security" in the subject. Do not send credentials, private keys, production
audio, or personal data.

Include, when possible:

- the affected version or commit;
- deployment assumptions and required privileges;
- reproduction steps or a minimal proof of concept;
- impact and whether exploitation crosses a trust boundary;
- any suggested remediation or disclosure constraints.

Maintainers will acknowledge a complete report as soon as practical, validate
it, coordinate a fix and release, and credit the reporter if desired. Public
disclosure should wait until a fix is available or a timeline has been agreed.

## Security boundaries

Plaintext gRPC is intended only for localhost or an isolated Compose network.
Distributed deployments should use TLS or mTLS and a unique bearer token. The
server processes untrusted protocol metadata and PCM lengths, but operators
remain responsible for host, NVIDIA runtime, certificate, secret, and network
security. See [docs/security.md](docs/security.md) for deployment details.

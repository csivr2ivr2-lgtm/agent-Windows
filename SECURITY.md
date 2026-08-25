# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Security → Report a vulnerability** flow. Do not open a public issue containing credentials, prompts, transcripts, audio, or exploit details. Include affected commit, impact, reproduction, and suggested mitigation when known.

## Supported version

Security fixes target the current `main` branch until a formal release policy is published.

## Security model

- Provider and relay credentials come only from environment variables or local `.env`; neither is committed or logged.
- Logs redact authorization headers and common secret assignments. Prompts, transcripts, and audio are not logged by default.
- Windows file tools resolve paths and permit access only below configured roots; arbitrary shell execution is not exposed.
- Network clients verify TLS using Python defaults. Relay mode requires HTTPS and rejects credential-bearing or ambiguous URLs.
- Relay uploads are authenticated, bounded, checksum-verified, MIME-validated, stored with generated names outside the web root, and never executed.
- `X-Forwarded-Proto` is trusted only when `RELAY_TRUST_PROXY=true`; the trusted proxy must overwrite client-supplied forwarding headers.

Treat the agent token as a secret separate from provider keys. Rotate it after suspected exposure. Deploy the Relay under a non-privileged account and keep its storage directory outside the document root with restrictive permissions.

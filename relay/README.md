# PHP Relay deployment

Requires PHP 8.1+, HTTPS, URL rewriting to `public/index.php`, and a writable storage directory outside the web root. Copy `.env.example` values into the host environment; the relay intentionally does not parse a web-readable `.env`.

The relay authenticates agents, rate-limits, creates/resumes sessions, streams chunks to disk, verifies SHA-256, detects duplicates/conflicts, reports status, and finalizes stored sessions. `StoredOnlyForwarder` is the safe default: it returns `transcript: null`, allowing a configured client to use direct STT. Replace it with a deployment-specific `ProviderForwarder` only after configuring provider keys on the server.

Production must set `RELAY_REQUIRE_HTTPS=true`. Point the web root at `relay/public`, never at the repository or storage directory. Keep `RELAY_TRUST_PROXY=false` unless a trusted reverse proxy terminates TLS and overwrites `X-Forwarded-Proto`.

Before deployment, run `php -l relay/public/index.php` and `php -l relay/src/ProviderForwarder.php`, then exercise authentication, limits, checksums, conflicts, resume, and rate limiting under the real PHP/web-server account. CI's development-server integration test does not replace deployment validation.

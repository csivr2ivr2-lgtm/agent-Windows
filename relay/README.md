# PHP Relay deployment

Requires PHP 8.1+, HTTPS, URL rewriting to `public/index.php`, and a writable storage directory outside the web root. Copy `.env.example` values into the host environment; the relay intentionally does not parse a web-readable `.env`.

The relay authenticates agents, rate-limits, creates/resumes sessions, streams chunks to disk, verifies SHA-256, detects duplicates/conflicts, reports status, and finalizes stored sessions. It can also proxy ElevenLabs through `POST /v1/tts/stream`, preserving HTTP chunked streaming so the Windows client can start `ffplay` before the full MP3 is generated. Configure `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, and `ELEVENLABS_MODEL` only in the server environment outside the web root. `StoredOnlyForwarder` remains the safe default for uploaded microphone audio: it returns `transcript: null`, allowing a configured client to use direct STT.

Production must set `RELAY_REQUIRE_HTTPS=true`. Point the web root at `relay/public`, never at the repository or storage directory. Keep `RELAY_TRUST_PROXY=false` unless a trusted reverse proxy terminates TLS and overwrites `X-Forwarded-Proto`.

Before deployment, run `php -l relay/public/index.php` and `php -l relay/src/ProviderForwarder.php`, then exercise authentication, limits, checksums, conflicts, resume, and rate limiting under the real PHP/web-server account. CI's development-server integration test does not replace deployment validation.

For a subdirectory deployment such as `https://example.com/ai`, set `RELAY_BASE_PATH=/ai`. `GET /v1/tts/health` reports whether server-side TTS credentials and PHP cURL are available without consuming ElevenLabs credits.

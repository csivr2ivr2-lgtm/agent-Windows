# AI Aharon Windows distribution

`AI-Aharon.iss` builds the public Windows installer. The release payload contains a private Python 3.11 runtime, the Agent Windows package, FFmpeg/FFplay, a small native launcher, service scripts, and the default configuration template.

## Persistent state

Upgrades replace program/runtime files but preserve `%ProgramData%\AgentWindowsAI\.env` and `%ProgramData%\AgentWindowsAI\data`. This keeps API keys, local configuration, service identity, SQLite memory, and user context between versions.

Uninstall removes the service and replaceable runtime/media binaries while intentionally preserving configuration and data so reinstalling does not silently destroy long-term memory.

## Built-in configuration

The desktop application exposes **Settings** for provider keys, model names, routing, STT/TTS, LiveKit, web integrations, relay and semantic-memory providers. On a first install with no LLM provider configured, the settings window opens automatically.

Real secrets are never placed in the repository or GitHub Release assets.

## Releases and updates

`.github/workflows/release.yml` can build an artifact manually or publish a GitHub Release when a `vX.Y.Z` tag is pushed. The release contains:

- `AI-Aharon-Setup-X.Y.Z.exe`
- `update.json`
- `checksums.sha256`

The desktop app checks the latest `update.json`, compares versions, downloads the installer over HTTPS, verifies its SHA-256 digest, and then launches the normal elevated installer. Existing settings and memory survive the upgrade.

For Authenticode signing, configure repository secrets `WINDOWS_CERT_PFX_B64` and `WINDOWS_CERT_PASSWORD`. Without them, the workflow still produces an installer but Windows may show an unknown-publisher warning.

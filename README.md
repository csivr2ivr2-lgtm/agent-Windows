# agent-Windows

A lightweight personal AI agent for Windows 11 with persistent memory, safe local tools, cloud LLM fallback, low-bandwidth voice, offline behavior, and an optional PHP relay. Target: i3-10110U, 8 GB RAM, integrated graphics. No Docker, GPU, database server, or model download is required.

## Quick start

Requires Windows 11 and Python 3.11+. FFmpeg with `libopus` is required only for voice.

```powershell
git clone https://github.com/csivr2ivr2-lgtm/agent-Windows.git
cd agent-Windows
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
notepad .env
.\.venv\Scripts\agent-windows.exe doctor
.\.venv\Scripts\agent-windows.exe chat
```

Setup creates a user virtual environment, installs the package, and copies `.env.example`. It does not require Administrator, change security settings, install system software, or download models.

## Commands

- `agent-windows chat` — interactive text agent.
- `agent-windows voice` — capture one utterance, transcribe, answer, synthesize, play.
- `agent-windows status` — configuration and health without secrets.
- `agent-windows doctor [--llmfit]` — diagnostics; llmfit runs only when requested.
- `agent-windows benchmark` — local payload/VAD/audio measurements; no API traffic.

Offline commands:

```text
/tool current_time
/tool system_info
/tool list_directory {"path":"."}
/tool read_text_file {"path":"README.md"}
/memory lightweight
```

Paths are restricted to the project and data directory. There is no arbitrary shell tool.

## Architecture

```text
Text -> optimizer -> SQLite memory -> LLM Router -> ProviderManager
                                |             -> Groq/Gemini/OpenRouter/local
                                +-> ToolRegistry -> safe Windows tools

Microphone -> EnergyVAD -> FFmpeg encoder -> network profile
           -> PHP relay chunks or direct STT -> Agent -> ElevenLabs TTS -> ffplay
```

Normal request latency/failures—not a speed test—select `GOOD`, `DEGRADED`, `POOR`, or `OFFLINE`. This changes timeout, retries, context/tool limits, Opus bitrate, chunk size, and VAD boundary. Benchmark Hebrew accuracy before production.

## Configuration and providers

Copy `.env.example` to `.env`. Missing key/model pairs are skipped without a request.

LLM order defaults to Groq, Gemini, OpenRouter, local. The local adapter accepts only a loopback OpenAI-compatible endpoint and never downloads a model. Without it, offline reasoning reports unavailable while memory/tools work.

STT order is AssemblyAI then Deepgram. AssemblyAI uses upload/submit/poll for bounded utterances. Deepgram uses Nova-3 with Hebrew `he`. TTS uses ElevenLabs because its current multilingual models include Hebrew and its Free plan includes TTS credits. Set `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`; otherwise answers remain text-only.

Never commit `.env`. Prompts, audio, transcripts, authorization headers, and keys are not logged by default.

## Voice

Windows capture uses FFmpeg DirectShow. List devices and set the exact name when `default` fails:

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

The pipeline captures 16 kHz mono PCM locally, removes silence, and prefers Ogg Opus only when `libopus`, transport, and STT all support it. MP3/PCM are negotiated fallbacks. Encoded media is not gzip-compressed.

Recordings are bounded and use temporary files rather than whole-recording RAM buffers. Offline encoded chunks enter a bounded checksum-verified spool. Recovery skips relay-acknowledged chunks and deletes completed sessions.

## PHP relay

See [relay/README.md](relay/README.md) and [the protocol](docs/low-bandwidth-audio.md). Deploy with PHP 8.1+, HTTPS, web root `relay/public`, and storage outside the web root.

The relay implements auth, health, rate limiting, session resume, streamed chunk storage, SHA-256, duplicate/conflict detection, status, and finish. Safe default `StoredOnlyForwarder` does not contact STT until a deployment-specific forwarder/key is configured. A null transcript permits direct fallback only when `AGENT_DIRECT_ALLOWED=true` and client STT credentials exist.

## Memory and offline mode

SQLite uses WAL, stays bounded to 5,000 timestamped/deduplicated records, retrieves without loading all rows, and supports deletion. OpenViking/Qdrant are optional.

Offline, local tools, memory, diagnostics and benchmarks work; audio queues; a local LLM runs only if configured on loopback. No model downloads automatically.

## Failure and bandwidth policy

- 401/403: configuration error, no retry, health records it.
- 429: no immediate retry; numeric `Retry-After` is honored.
- timeout/connection/5xx: bounded exponential retry, cooldown, fallback.
- malformed response: fail safely and continue.
- offline: cloud LLM calls are skipped.

Text requests normalize whitespace, deduplicate adjacent history, keep bounded recent context, and filter tool schemas by relevance. Compression is not used for small text or compressed audio.

## Security

- Relay can hold all provider secrets; production requires HTTPS and agent auth.
- Upload sizes, content types, IDs and checksums are validated.
- Server paths are generated/hashed; uploads stay outside web root and never execute.
- Local file tools enforce allowed roots and a 1 MB limit.
- No shell tool or automatic replay of non-idempotent tools.
- Central logging redacts token/key/authorization patterns.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests
```

Commercial APIs are mocked. With PHP CLI, a local relay integration test checks auth and invalid upload handling without external traffic.

## Optional components

LiveKit Agents is not installed: the FFmpeg/VAD path is smaller and completes the one-user MVP. LiveKit remains useful for future WebRTC rooms/telephony, not a parallel voice stack. llmfit is on-demand diagnostics. Needle, OpenViking, Hermes, OpenHuman, Unsloth and Soup are not required at runtime.

## Troubleshooting

- No LLM: configure a key+model or loopback model; run `doctor`.
- No microphone: install FFmpeg, list DirectShow devices, set `AGENT_MICROPHONE_DEVICE`.
- No speech output: configure ElevenLabs and put `ffplay` on PATH.
- Relay failure: verify HTTPS/token/rewrite/storage and `/v1/health`; direct fallback needs a client STT key.
- 429: wait for cooldown; limits are never evaded.

## Known limitations

- Cloud integrations are mock-tested until keys are supplied; setup/tests make no paid request.
- Direct AssemblyAI STT is VAD-bounded pre-recorded, not WebSocket streaming.
- PHP safely stores/finalizes audio; provider forwarding needs deployment-specific server configuration.
- Voice capture is Windows-only; other systems test initialization/failure behavior.
- Idle RAM varies by Python/Windows build and should be measured on the target PC.

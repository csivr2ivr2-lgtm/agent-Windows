# Real-time voice milestone

The default runtime remains lightweight and Windows-first. The local desktop microphone/playback path is authoritative; optional external projects are adapters, not mandatory runtime dependencies.

## Latency path

microphone/VAD -> STT -> provider manager streaming hook -> chunked TTS -> ffplay

Cancellation is propagated with a shared event. Playback keeps a process handle so barge-in controllers can terminate audio immediately instead of waiting for the current response to finish.

Metrics supported by `LatencyMetrics`:

- speech end -> transcript ready
- transcript ready -> first LLM token
- first LLM token -> first audio byte
- speech end -> first audible response
- barge-in detected -> playback stopped

## Integration matrix

| Component | Integration | Runtime cost | Default enabled |
|---|---|---:|---|
| llmfit | on-demand hardware/model-fit wrapper boundary | low/on-demand | no |
| LiveKit Agents | optional realtime session backend; local fallback remains | optional | no |
| Needle | optional guarded tool adapter boundary | optional | no |
| OpenViking | optional memory/context adapter; SQLite default | optional | no |
| Hermes | experimental orchestration compatibility boundary | optional | no |
| OpenHuman | experimental orchestration compatibility boundary | optional | no |
| Unsloth | offline training/export only | offline only | no |
| Soup | offline optimization boundary only | offline only | no |

No heavy training/orchestration package is loaded by the normal 8 GB Windows runtime.

# Low-bandwidth audio protocol

## Pipeline

`Microphone -> local VAD -> encoder -> AudioTransport -> direct STT or PHP relay -> STT`

VAD consumes 16-bit mono PCM frames locally. Only speech frames enter the encoder. The encoder is a replaceable process adapter; FFmpeg is available on the target Windows machine, but startup must verify `ffmpeg -encoders` and prefer `libopus`. Do not silently use FFmpeg's lower-quality native Opus encoder without a benchmark.

Codec selection is negotiated across local encoder, selected transport and selected STT provider. Current documentation confirms AssemblyAI streaming supports raw Opus and Ogg Opus; Deepgram supports Opus/Ogg Opus, including 16 kHz for Flux. The baseline therefore prefers 16 kHz mono Ogg Opus, but does not assume every provider model/endpoint supports it. PCM is a compatibility fallback, not the default. Compressed audio is never gzip-compressed by default.

Adaptive bitrates (24/16/12 kbps) are initial hypotheses, not accuracy claims. Benchmark Hebrew word error rate and latency before production use.

## Relay protocol v1

All endpoints require HTTPS and `Authorization: Bearer <agent-relay-token>`. Provider keys stay on the relay.

### Start or resume

`POST /v1/audio/sessions`

```json
{"session_id":"random-128-bit-id","codec":"ogg_opus","content_type":"audio/ogg; codecs=opus","sample_rate":16000,"channels":1,"bitrate_bps":16000,"resume":true}
```

Response: `{"session_id":"...","accepted":true,"received_sequences":[0,1]}`. The server may return its compact contiguous `resume_from` value instead of a long list.

### Upload one chunk

`PUT /v1/audio/sessions/{session_id}/chunks/{sequence}`

Headers: `Content-Type`, `X-Audio-Timestamp-Ms`, `X-Chunk-SHA256`, `X-Final-Chunk`.
The body is raw encoded bytes, never base64 JSON. Successful new or duplicate chunks return an acknowledgement containing session and sequence. Duplicate `(session_id, sequence)` uploads are idempotent; conflicting checksums are rejected with `409`.

### Finish/status

- `POST /v1/audio/sessions/{session_id}/finish` asks the relay to finalize/submit the stream.
- `GET /v1/audio/sessions/{session_id}` returns acknowledged sequences, provider state and transcription state.

Only the failed or missing chunk is retried. Client state stores the session ID and acknowledgements so an offline recording can resume without loading the entire file into RAM. `OfflineAudioSpool` stores each encoded chunk separately under a SHA-256-derived directory, verifies its checksum on read, and never uses an external filename.

## PHP relay rules

- Stream request bodies to server-generated paths outside the web root; never trust client filenames.
- Enforce per-session, per-chunk and total size limits before provider forwarding.
- Allowlist content types/codecs and validate declared metadata.
- Verify SHA-256 while streaming; never execute or serve uploaded files.
- Authenticate the agent separately from provider credentials and rate-limit by client/session.
- Do not log audio, transcript bodies, authorization headers or provider keys by default.
- Keep only operational counters and request IDs. Delete temporary audio using an explicit retention policy.
- Route providers and forward bytes; do not transcode or run inference in PHP unless benchmarks justify it.

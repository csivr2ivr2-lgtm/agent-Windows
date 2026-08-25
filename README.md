# agent-Windows

Lightweight, provider-agnostic foundation for a personal Windows AI agent. The target machine is Windows 11 with an Intel i3-10110U, 8 GB RAM, and integrated graphics, so the runtime keeps orchestration local and sends heavy inference to replaceable cloud providers.

## MVP flow

`voice/text -> STT -> orchestrator -> memory -> LLM router -> tool -> response -> TTS -> memory`

The first milestone implements the text-path contracts, ordered LLM fallback, tool execution, and a zero-dependency in-memory backend. Voice and external providers are adapters added in later milestones.

## Architectural decisions

- **Own thin orchestrator for the MVP.** Hermes Agent and OpenHuman overlap heavily and would make the first runtime larger and harder to control. Hermes remains a future integration candidate; OpenHuman remains an evaluation candidate, not a second orchestrator.
- **LiveKit Agents: optional voice adapter.** Use its Python framework and cloud service when realtime voice enters the MVP. Do not self-host the LiveKit server on the 8 GB laptop by default.
- **Needle: optional local tool router.** Its tiny bounded-memory design is promising for simple tool selection. It must pass Windows/CPU accuracy and latency tests before it can decide actions with side effects.
- **OpenViking: optional context backend.** Evaluate it behind `MemoryStore`. The default remains lightweight until its process footprint and AGPL implications are accepted.
- **llmfit: diagnostic only.** Run on demand to produce hardware recommendations; do not keep it resident.
- **Unsloth and Soup: development/future tools.** Neither is part of the running agent. Training on the current integrated GPU is not an MVP goal.

## Repository evaluation snapshot (2026-08-25)

| Project | Decision | Reason |
|---|---|---|
| llmfit | Adopt later as on-demand diagnostic | Windows support, JSON automation output, MIT; useful without runtime coupling. |
| Needle | Prototype behind tool-routing interface | Very small function-calling model and bounded memory; validate reliability before computer control. |
| OpenViking | Prototype as memory adapter | Strong memory/RAG/skills model, but it is a separate context service and AGPL-3.0. |
| LiveKit Agents | Adopt for voice milestone | Mature realtime STT/LLM/TTS adapter ecosystem and Apache-2.0. |
| Hermes Agent | Reference/evaluate, not MVP dependency | Capable and Windows-aware, but duplicates orchestration/tools/memory and increases footprint. |
| OpenHuman | Leave out of MVP | Overlaps Hermes and the core; early beta and GPL-3.0. |
| Unsloth | Optional offline/remote workflow | Supports Windows/Intel/CPU, but model running/training is not required for the cloud-first MVP. |
| Soup | Future remote-GPU fine-tuning | Apache-2.0 and efficient GPU training, but published laptop result still assumes a discrete NVIDIA GPU. |

Licences must be reviewed again before distributing a build that embeds or modifies AGPL/GPL components. Calling a separately deployed service through an adapter is intentionally distinct from copying its code into this repository.

## Core boundaries

- `LLMProvider`: availability plus completion/tool calls.
- `SpeechToText` and `TextToSpeech`: raw audio adapters.
- `MemoryStore`: search and remember.
- `Tool`: JSON-schema declaration and invocation.
- `LLMRouter`: ordered fallback only on explicit provider failures; no quota evasion.

Provider order is configured rather than hard-coded: Groq, Gemini, OpenRouter, then a viable local OpenAI-compatible endpoint. Every provider adapter must translate HTTP 429 into `ProviderRateLimited`, use bounded timeouts, and expose a health check. Retries must honor official limits and `Retry-After`.

## Safety model for Windows tools

Tools are allowlisted. Read-only actions may run directly; writes, process execution, credential access, external messages, purchases, and destructive actions require an explicit policy/confirmation layer. Needle or any LLM proposes calls—it never receives unrestricted shell access.

## Local development

Requires Python 3.11+.

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

No API key is needed for the core tests. Copy `.env.example` to `.env` only when adding provider adapters.

## Milestones

1. Core contracts, fallback router, memory, tool registry, tests. **Implemented.**
2. Groq/Gemini/OpenRouter adapters with mocked HTTP tests, timeouts, cooldowns, and health checks.
3. Persistent local SQLite memory; benchmark OpenViking and hosted vector backends separately.
4. Safe Windows tool pack with risk classes, confirmation, audit log, and dry-run.
5. LiveKit voice path; select STT/TTS only after checking current Hebrew quality, latency, and free-tier terms.
6. Run llmfit on the target PC; optionally benchmark Needle for non-destructive tool routing.

## Free-tier rule

Quotas and prices are operational configuration, never constants copied from a README. Before enabling a provider, verify its current official pricing/limits and terms. Fallback provides reliability; it must not rotate identities, accounts, or keys to evade limits.

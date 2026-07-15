# Backend

FastAPI backend for the Jarvis macOS assistant. See [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)
for the full design.

## Status

**Phase 4 complete.** The backend serves chat (REST + streaming WS) and a
full voice loop (wake word → Whisper STT → LLM → TTS) against local models.
Planner/tools/memory modules are still documented stubs for their phases.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [Ollama](https://ollama.com) — `brew install ollama`, daemon running

## Quickstart

```bash
# from the repo root
scripts/setup.sh --with-models   # uv sync + pull qwen2.5:3b-instruct-q4_K_M

cd backend
uv run jarvis-backend            # serves http://127.0.0.1:8765
```

Try it:

```bash
curl http://127.0.0.1:8765/health
curl -X POST http://127.0.0.1:8765/chat \
     -H 'Content-Type: application/json' \
     -d '{"message": "Hello Jarvis"}'
```

Interactive API docs (Swagger UI): http://127.0.0.1:8765/docs

## API

| Endpoint | Kind | Purpose |
|---|---|---|
| `GET /health` | REST, unauthenticated | liveness + Ollama reachability + active model |
| `POST /chat` | REST | one-shot chat: `{message, session_id?}` → `{session_id, reply}` |
| `WS /ws/chat` | WebSocket | streaming chat: send `{message, session_id?}`, receive `{"type":"token"}`* then `{"type":"done"}`; errors arrive as `{"type":"error"}` without closing the socket |

Pass `session_id` from a previous response to continue a conversation.
History is in-memory for now (persistence lands in Phase 7).

### Voice (`uv sync --extra voice`)

| Endpoint | Kind | Purpose |
|---|---|---|
| `WS /ws/voice` | WebSocket | stream 16kHz mono PCM16; get wake/transcript/reply events + spoken WAV back |
| `POST /voice/transcribe` | REST | WAV upload → text (debugging) |
| `POST /voice/speak` | REST | text → WAV (debugging) |

The wake word is **"hey jarvis"** (openWakeWord pretrained model). STT is
faster-whisper `base.en` int8 (~200MB resident); TTS is Piper when installed
(`--extra tts-piper` + `JARVIS_PIPER_VOICE_PATH`), otherwise macOS's built-in
`say`. Voice endpoints only mount when the voice extra is installed — the
chat API works without it.

Try it live against a running backend (asks for mic permission):

```bash
uv run python -m app.speech.mic_demo
```

### Auth

Set `JARVIS_AUTH_TOKEN` to require `Authorization: Bearer <token>` on
everything except `/health`. Unset (the default) means no auth — fine for
local development; the SwiftUI app will generate a token per session in
Phase 3.

## Configuration

Via env vars (`JARVIS_*`) or `backend/.env` — see [.env.example](.env.example).
Defaults: port `8765`, model `qwen2.5:3b-instruct-q4_K_M`, keep-alive `30m`.

**Model choice matters:** use *non-thinking instruct* models. Reasoning models
(qwen3, deepseek-r1) spend tens of seconds on hidden "thinking" tokens per
reply on 8GB hardware — measured ~30s to answer "say hi" — which is unusable
for voice. `JARVIS_POWER_MODE=true` switches to the 7B model
(`scripts/install_models.sh --power`) at the cost of most of your free RAM.

## Development

```bash
uv sync --extra dev
uv run pytest          # 22 tests, no Ollama required (fake client)
uv run ruff check app tests
uv run mypy app
```

Tests inject a `FakeOllamaClient` through the `create_app(settings=...,
ollama_client=...)` factory parameters, so the suite runs anywhere.

## Dependency extras

Heavy dependencies install with the phase that needs them:

| Extra | Phase | Contents |
|---|---|---|
| `dev` | all | pytest, ruff, mypy, httpx |
| `memory` | 7 | chromadb |
| `browser` | 9 | playwright |
| `macos` | 6 | pyobjc frameworks |

e.g. `uv sync --extra dev --extra macos`.

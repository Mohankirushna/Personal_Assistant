# Backend

FastAPI backend for the Jarvis macOS assistant. See [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)
for the full design.

## Status

Phase 1 skeleton only — every module below is a stub with a docstring describing its
future responsibility. No functional code, no installed dependencies yet. Implemented
starting Phase 2.

## Layout

```
app/
├── main.py       # FastAPI app entrypoint
├── api/          # HTTP/WebSocket routers
├── core/         # config, logging, model_manager, safety
├── planner/      # utterance -> structured tool-call plan
├── tools/        # one subpackage per tool
├── memory/       # SQLite + ChromaDB
├── speech/       # STT + wake word
├── tts/          # Piper wrapper
├── vision/       # Qwen2.5-VL wrapper
└── plugins/       # user/community tool packages
```

## Setup (Phase 2+)

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

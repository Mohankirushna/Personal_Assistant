# ADR 0001: Single-heavy-model RAM budget via a Model Manager

## Status

Accepted (Phase 1)

## Context

The target hardware is a MacBook Air M2 with 8GB of unified memory. macOS itself uses
roughly 2.5-3GB at idle. The assistant needs, at various points, an LLM for planning
and conversation (Qwen2.5 3B or 7B via Ollama), a vision-language model for on-demand
screen understanding (Qwen2.5-VL), speech-to-text (whisper.cpp), text-to-speech
(Piper), and wake-word detection (openWakeWord), plus the backend process, the SwiftUI
app, and occasionally Playwright/Chromium for browser automation.

Naively loading every model at startup and keeping it resident is not viable on this
hardware — a 7B Q4 LLM alone can use ~4.5-5GB, and Qwen2.5-VL is comparably large.
Running both simultaneously alongside STT/TTS would exceed available memory, causing
swapping and severe latency/battery regressions, or outright OOM.

## Decision

- Only one "heavy" model (text LLM *or* vision model) is loaded into Ollama at a time.
  A `ModelManager` service (`backend/app/core/model_manager.py`) owns this invariant:
  requesting the vision model unloads the text LLM first, and vice versa.
- STT (whisper.cpp), TTS (Piper), and wake-word (openWakeWord) are small enough
  (combined well under 1.5GB) to remain resident continuously alongside one heavy
  model, since voice interaction and LLM planning happen together constantly.
- The default LLM is Qwen2.5 3B Instruct (Q4_K_M, ~2GB), not 7B. A "power mode" toggle
  allows switching to 7B for users willing to accept reduced multitasking headroom.
- Vision is loaded strictly on-demand (only when the user explicitly asks the
  assistant to look at the screen), never proactively — this matches both the product
  spec ("Only when requested") and the RAM budget.

## Consequences

- **Positive**: the system stays responsive and avoids swap/OOM on 8GB hardware; the
  RAM budget is an explicit, testable invariant rather than an emergent property.
- **Negative**: switching between text and vision tasks incurs a model-load latency
  (roughly a few seconds, dependent on model size and disk speed) each time the active
  model changes. This is an accepted tradeoff — vision requests are inherently
  occasional ("look at my screen"), not the common case.
- **Negative**: users with more capable hardware (16GB+/M-series with more RAM) don't
  benefit from this constraint and could in principle run both models concurrently.
  This is left as a future enhancement (`config.py` could expose a
  `concurrent_models: bool` setting once we have a way to detect available RAM at
  startup) rather than solved now, per YAGNI — the target hardware for v1 is 8GB.

## Alternatives considered

- **Always keep both models loaded**: rejected — exceeds RAM budget on target hardware.
- **Use a single multimodal model for both text and vision** (e.g., always run
  Qwen2.5-VL for everything): rejected — vision-capable models are slower and heavier
  for pure-text planning/chat, which is the overwhelming majority of interactions;
  paying that cost on every request is worse than paying a load-latency cost
  occasionally.
- **Run models via a cloud API instead of locally**: rejected outright — violates the
  project's core "100% free, local-first" requirement.

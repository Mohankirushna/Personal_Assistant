"""Live microphone demo: wake word -> transcribe -> chat -> spoken reply.

Run against a running backend:

    cd backend && uv run python -m app.speech.mic_demo

Captures the default input device with sounddevice, streams PCM16 to
ws://127.0.0.1:8765/ws/voice, prints events, and plays reply audio with
`afplay`. The first run asks macOS for microphone permission (granted to
your terminal app).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from typing import Any

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 1280  # 80ms


async def main() -> None:
    import sounddevice as sd
    import websockets

    port = os.environ.get("JARVIS_PORT", "8765")
    token = os.environ.get("JARVIS_AUTH_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    uri = f"ws://127.0.0.1:{port}/ws/voice"

    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()

    def on_audio(indata: Any, _frames: Any, _time: Any, status: Any) -> None:
        if status:
            print(f"[mic] {status}")
        with contextlib.suppress(asyncio.QueueFull):
            loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

    print(f"Connecting to {uri} …")
    async with websockets.connect(uri, additional_headers=headers) as ws:
        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SAMPLES,
            channels=1,
            dtype="int16",
            callback=on_audio,
        )
        with stream:
            print("Listening — say 'hey jarvis' (Ctrl-C to quit)")

            async def pump_mic() -> None:
                while True:
                    ws_chunk = await audio_queue.get()
                    await ws.send(ws_chunk)

            async def pump_events() -> None:
                while True:
                    message = await ws.recv()
                    if isinstance(message, bytes):
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                            f.write(message)
                        player = await asyncio.create_subprocess_exec("afplay", f.name)
                        await player.wait()
                        os.unlink(f.name)
                        continue
                    event = json.loads(message)
                    match event.get("type"):
                        case "wake":
                            print(f"⚡ wake ({event['score']:.2f})")
                        case "listening":
                            print("🎙  listening…")
                        case "transcript":
                            print(f"you: {event['text']}")
                        case "tool":
                            print(f"⚙️  {event['tool']}: {event['status']}")
                        case "reply":
                            print(f"jarvis: {event['text']}")
                        case "nothing_heard":
                            print("(nothing heard)")
                        case "error":
                            print(f"error: {event['message']}")

            await asyncio.gather(pump_mic(), pump_events())


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())

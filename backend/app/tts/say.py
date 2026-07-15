"""TTS via macOS's built-in `say` command.

Always available on the target platform, fully offline, no Python
dependencies. Produces 22.05kHz 16-bit mono WAV.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


class SayTTS:
    def __init__(self, voice: str | None = None) -> None:
        self._voice = voice

    async def synthesize(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "speech.wav"
            command = ["/usr/bin/say", "-o", str(out_path), "--data-format=LEI16@22050"]
            if self._voice:
                command += ["-v", self._voice]
            command.append(text)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"say failed: {stderr.decode().strip()}")
            return out_path.read_bytes()

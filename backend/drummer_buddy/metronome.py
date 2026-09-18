from __future__ import annotations

import math
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time
from threading import RLock


class ServerMetronomeError(Exception):
    pass


class ServerMetronome:
    """Play the metronome on the machine running the backend."""

    def __init__(self, library_dir: Path):
        self.audio_path = library_dir / ".metronome.wav"
        self.log_path = library_dir / "server-metronome.log"
        self._lock = RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._bpm = 100
        self._beats = 4
        self._volume = 100
        self.socket_path = library_dir / ".metronome.sock"

    def _refresh(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            self._process = None

    def _write_audio(self, bpm: int, beats: int) -> None:
        sample_rate = 44100
        beat_samples = round(sample_rate * 60 / bpm)
        samples: list[int] = []
        for beat in range(beats):
            frequency = 1100 if beat == 0 else 750
            for index in range(beat_samples):
                elapsed = index / sample_rate
                envelope = max(0.0, 1.0 - elapsed / 0.075)
                value = math.sin(2 * math.pi * frequency * elapsed) * envelope * 0.42
                samples.append(round(value * 32767))
        payload = struct.pack(f"<{len(samples)}h", *samples)
        with self.audio_path.open("wb") as stream:
            stream.write(b"RIFF")
            stream.write(struct.pack("<I", 36 + len(payload)))
            stream.write(b"WAVEfmt ")
            stream.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
            stream.write(b"data")
            stream.write(struct.pack("<I", len(payload)))
            stream.write(payload)

    def start(self, bpm: int, beats: int, volume: int = 100) -> dict:
        with self._lock:
            self._refresh()
            executable = shutil.which("mpv")
            if executable is None:
                raise ServerMetronomeError("mpv is not installed")
            self.stop()
            self._write_audio(bpm, beats)
            try:
                self._process = subprocess.Popen(
                    [executable, "--no-config", "--no-video", "--audio-display=no", "--really-quiet",
                     f"--input-ipc-server={self.socket_path}", f"--volume={volume}", "--loop-file=inf",
                     f"--log-file={self.log_path}", str(self.audio_path)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError as error:
                raise ServerMetronomeError(f"could not start server metronome: {error}") from error
            self._bpm = bpm
            self._beats = beats
            self._volume = volume
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self.socket_path.unlink(missing_ok=True)
            return self.status()

    def status(self) -> dict:
        self._refresh()
        return {"running": self._process is not None, "bpm": self._bpm, "beats": self._beats, "volume": self._volume}

    def set_volume(self, volume: int) -> dict:
        with self._lock:
            self._refresh()
            if self._process is None:
                self._volume = volume
                return self.status()
            payload = json.dumps({"command": ["set_property", "volume", volume]}).encode() + b"\n"
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(1)
                    connection.connect(str(self.socket_path))
                    connection.sendall(payload)
                    connection.recv(65536)
            except OSError as error:
                raise ServerMetronomeError(f"could not change server metronome volume: {error}") from error
            self._volume = volume
            return self.status()

    def shutdown(self) -> None:
        self.stop()

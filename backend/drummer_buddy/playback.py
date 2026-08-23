from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import subprocess
from threading import Lock
import time


class ServerPlaybackError(Exception):
    pass


class ServerPlayer:
    def __init__(self, library_dir: Path):
        self.socket_path = library_dir / ".mpv.sock"
        self.log_path = library_dir / "server-player.log"
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._song_id: str | None = None
        self._variant: str | None = None
        self._error: str | None = None
        self._request_id = 0
        self._assumed_paused = True
        self._loop = False

    def _refresh(self) -> None:
        if self._process is None:
            return
        exit_code = self._process.poll()
        if exit_code is None:
            return
        self._process = None
        self.socket_path.unlink(missing_ok=True)
        if exit_code != 0:
            self._error = f"mpv exited with status {exit_code}; see {self.log_path}"

    def _send(self, command: list) -> object:
        if self._process is None:
            raise ServerPlaybackError("server player is not running")
        self._request_id += 1
        request_id = self._request_id
        payload = json.dumps({"command": command, "request_id": request_id}).encode() + b"\n"
        last_error = "mpv did not return a response"
        for attempt in range(3):
            received = b""
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(1)
                    connection.connect(str(self.socket_path))
                    connection.sendall(payload)
                    while b"\n" not in received:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        received += chunk
            except OSError as error:
                last_error = f"could not control mpv: {error}"
            for line in received.splitlines():
                response = json.loads(line)
                if response.get("request_id") != request_id:
                    continue
                if response.get("error") != "success":
                    raise ServerPlaybackError(f"mpv command failed: {response.get('error', 'unknown error')}")
                return response.get("data")
            if attempt < 2:
                time.sleep(0.05)
        raise ServerPlaybackError(last_error)

    def _property(self, name: str, default: object = None) -> object:
        try:
            return self._send(["get_property", name])
        except ServerPlaybackError as error:
            if "property unavailable" in str(error):
                return default
            raise

    def load(self, path: Path, song_id: str, variant: str, position: float = 0, autoplay: bool = True, loop: bool = False) -> dict:
        with self._lock:
            self._stop_unlocked()
            executable = shutil.which("mpv")
            if executable is None:
                raise ServerPlaybackError("mpv is not installed")
            self.socket_path.unlink(missing_ok=True)
            command = [
                executable,
                "--no-config",
                "--no-video",
                "--audio-display=no",
                "--really-quiet",
                "--keep-open=yes",
                f"--input-ipc-server={self.socket_path}",
                f"--log-file={self.log_path}",
                f"--start={max(0, position):.3f}",
                f"--pause={'no' if autoplay else 'yes'}",
                f"--loop-file={'inf' if loop else 'no'}",
                str(path),
            ]
            try:
                self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as error:
                raise ServerPlaybackError(f"could not start mpv: {error}") from error
            self._song_id = song_id
            self._variant = variant
            self._error = None
            self._assumed_paused = not autoplay
            self._loop = loop
            deadline = time.monotonic() + 2
            while not self.socket_path.exists():
                self._refresh()
                if self._process is None:
                    raise ServerPlaybackError(self._error or f"mpv failed to start; see {self.log_path}")
                if time.monotonic() >= deadline:
                    self._stop_unlocked()
                    raise ServerPlaybackError(f"mpv control socket did not start; see {self.log_path}")
                time.sleep(0.02)
            return self._status_unlocked()

    def _status_unlocked(self) -> dict:
        self._refresh()
        if self._process is None:
            return {
                "state": "idle",
                "song_id": self._song_id,
                "variant": self._variant,
                "position": 0,
                "duration": 0,
                "loop": False,
                "error": self._error,
            }
        try:
            paused = bool(self._property("pause", self._assumed_paused))
            eof = bool(self._property("eof-reached", False))
            position = self._property("time-pos", 0)
            duration = self._property("duration", 0)
            loop_value = self._property("loop-file", "inf" if self._loop else "no")
        except ServerPlaybackError as error:
            return {
                "state": "error",
                "song_id": self._song_id,
                "variant": self._variant,
                "position": 0,
                "duration": 0,
                "loop": False,
                "error": str(error),
            }
        return {
            "state": "paused" if paused or eof else "playing",
            "song_id": self._song_id,
            "variant": self._variant,
            "position": float(position or 0),
            "duration": float(duration or 0),
            "loop": loop_value not in {False, "no", 0, None},
            "error": None,
        }

    def status(self) -> dict:
        with self._lock:
            return self._status_unlocked()

    def command(self, action: str, value: float | bool | None = None) -> dict:
        with self._lock:
            self._refresh()
            if self._process is None:
                raise ServerPlaybackError("server player is not running")
            if action == "play":
                if bool(self._property("eof-reached", False)):
                    self._send(["seek", 0, "absolute+exact"])
                self._send(["set_property", "pause", False])
                self._assumed_paused = False
            elif action == "pause":
                self._send(["set_property", "pause", True])
                self._assumed_paused = True
            elif action == "seek":
                self._send(["seek", max(0, float(value or 0)), "absolute+exact"])
            elif action == "loop":
                self._loop = bool(value)
                self._send(["set_property", "loop-file", "inf" if self._loop else "no"])
            elif action == "stop":
                self._stop_unlocked()
                return self._status_unlocked()
            else:
                raise ServerPlaybackError("unsupported player command")
            return self._status_unlocked()

    def _stop_unlocked(self) -> None:
        self._refresh()
        process = self._process
        if process is None:
            self.socket_path.unlink(missing_ok=True)
            return
        try:
            self._send(["quit"])
            process.wait(timeout=2)
        except (ServerPlaybackError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self._process = None
        self.socket_path.unlink(missing_ok=True)

    def shutdown(self) -> None:
        with self._lock:
            self._stop_unlocked()

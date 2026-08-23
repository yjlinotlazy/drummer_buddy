from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from threading import Lock
from typing import IO

from .config import Config


class RecorderError(Exception):
    pass


class RecorderManager:
    def __init__(self, config: Config):
        self.config = config
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_file: IO[bytes] | None = None
        self._path: Path | None = None
        self._started_at: str | None = None
        self._exit_code: int | None = None
        self._error: str | None = None
        self._imported_song_id: str | None = None

    def _close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _refresh(self) -> None:
        if self._process is None:
            return
        exit_code = self._process.poll()
        if exit_code is None:
            return
        self._exit_code = exit_code
        self._process = None
        self._close_log()
        if exit_code != 0 and self._path is not None:
            log_path = self._path.with_suffix(".arecord.log")
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:].strip() if log_path.is_file() else ""
            self._error = detail or f"arecord exited with status {exit_code}"

    def status(self) -> dict:
        with self._lock:
            self._refresh()
            return {
                "running": self._process is not None,
                "path": str(self._path) if self._path else None,
                "started_at": self._started_at,
                "exit_code": self._exit_code,
                "error": self._error,
                "imported_song_id": self._imported_song_id,
                "default_directory": str(self.config.recording_dir),
                "device": self.config.recording_device,
            }

    def start(self, directory_value: str, song_name: str) -> dict:
        with self._lock:
            self._refresh()
            if self._process is not None:
                raise RecorderError("a recording is already running")
            if shutil.which("arecord") is None:
                raise RecorderError("arecord is not installed")

            clean_name = re.sub(r"\s+", "_", song_name.strip())
            if not clean_name or Path(clean_name).name != clean_name or clean_name in {".", ".."}:
                raise RecorderError("song name must be a filename, not a path")
            if not clean_name.lower().endswith(".wav"):
                clean_name += ".wav"
            directory = Path(directory_value).expanduser().absolute()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / clean_name
            if path.exists():
                raise RecorderError(f"recording already exists: {path}")

            log_file = path.with_suffix(".arecord.log").open("wb")
            try:
                process = subprocess.Popen(
                    ["arecord", "-D", self.config.recording_device, "-f", "cd", str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log_file,
                    start_new_session=True,
                )
            except OSError as error:
                log_file.close()
                raise RecorderError(f"could not start arecord: {error}") from error

            self._process = process
            self._log_file = log_file
            self._path = path
            self._started_at = datetime.now(UTC).isoformat()
            self._exit_code = None
            self._error = None
            self._imported_song_id = None
            return self.status_unlocked()

    def status_unlocked(self) -> dict:
        self._refresh()
        return {
            "running": self._process is not None,
            "path": str(self._path) if self._path else None,
            "started_at": self._started_at,
            "exit_code": self._exit_code,
            "error": self._error,
            "imported_song_id": self._imported_song_id,
            "default_directory": str(self.config.recording_dir),
            "device": self.config.recording_device,
        }

    def stop(self) -> dict:
        with self._lock:
            self._refresh()
            process = self._process
            if process is None:
                raise RecorderError("no recording is running")
            forced = False
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGINT)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                forced = True
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=2)
            self._exit_code = process.returncode
            self._process = None
            self._close_log()
            self._error = "arecord did not stop cleanly" if forced else None
            return self.status_unlocked()

    def completed_path(self) -> Path:
        with self._lock:
            self._refresh()
            if self._process is not None:
                raise RecorderError("stop the recording before cleaning it")
            if self._path is None:
                raise RecorderError("nothing has been recorded yet")
            if self._error is not None:
                raise RecorderError(self._error or "recording did not finish cleanly")
            if not self._path.is_file() or self._path.stat().st_size <= 44:
                raise RecorderError("recorded WAV is empty")
            return self._path

    def mark_imported(self, song_id: str) -> dict:
        with self._lock:
            self._imported_song_id = song_id
            return self.status_unlocked()

    def shutdown(self) -> None:
        with self._lock:
            self._refresh()
            process = self._process
            if process is not None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGINT)
                    process.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait()
                self._refresh()
            self._close_log()

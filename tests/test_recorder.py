from pathlib import Path
import signal

import pytest

from drummer_buddy.config import Config
from drummer_buddy.recorder import RecorderError, RecorderManager


class FakeProcess:
    pid = 1234

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -signal.SIGKILL


def test_record_stop_and_completed_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(library_dir=tmp_path / "library", recording_dir=tmp_path / "recordings", recording_device="test-monitor")
    manager = RecorderManager(config)
    process = FakeProcess()
    command: list[str] = []

    monkeypatch.setattr("drummer_buddy.recorder.shutil.which", lambda _: "/usr/bin/arecord")

    def fake_popen(args, **kwargs):
        command.extend(args)
        return process

    monkeypatch.setattr("drummer_buddy.recorder.subprocess.Popen", fake_popen)
    monkeypatch.setattr("drummer_buddy.recorder.os.getpgid", lambda _: process.pid)
    signals: list[int] = []
    monkeypatch.setattr("drummer_buddy.recorder.os.killpg", lambda _pid, sent: signals.append(sent))

    started = manager.start(str(config.recording_dir), "My Song")
    assert started["running"] is True
    assert command == ["arecord", "-D", "test-monitor", "-f", "cd", str(config.recording_dir / "My_Song.wav")]
    (config.recording_dir / "My_Song.wav").write_bytes(b"R" * 100)

    stopped = manager.stop()
    assert stopped["running"] is False
    assert signals == [signal.SIGINT]
    assert manager.completed_path() == config.recording_dir / "My_Song.wav"


def test_recording_rejects_paths_and_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(library_dir=tmp_path / "library", recording_dir=tmp_path / "recordings")
    manager = RecorderManager(config)
    monkeypatch.setattr("drummer_buddy.recorder.shutil.which", lambda _: "/usr/bin/arecord")

    with pytest.raises(RecorderError, match="filename"):
        manager.start(str(config.recording_dir), "../escape")

    config.recording_dir.mkdir()
    (config.recording_dir / "existing.wav").write_bytes(b"old")
    with pytest.raises(RecorderError, match="already exists"):
        manager.start(str(config.recording_dir), "existing.wav")

from pathlib import Path

import pytest

from drummer_buddy.playback import ServerPlaybackError, ServerPlayer


class RunningProcess:
    def poll(self) -> None:
        return None


def test_server_player_status_and_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    player = ServerPlayer(tmp_path)
    player._process = RunningProcess()  # type: ignore[assignment]
    player._song_id = "song-1"
    player._variant = "original"
    properties = {
        "pause": False,
        "eof-reached": False,
        "time-pos": 12.25,
        "duration": 180.0,
        "loop-file": "no",
    }
    commands: list[list] = []

    monkeypatch.setattr(player, "_property", lambda name, default=None: properties.get(name, default))

    def send(command: list):
        commands.append(command)
        if command[:2] == ["set_property", "pause"]:
            properties["pause"] = command[2]
        if command[:2] == ["set_property", "loop-file"]:
            properties["loop-file"] = command[2]

    monkeypatch.setattr(player, "_send", send)

    assert player.status()["state"] == "playing"
    assert player.command("pause")["state"] == "paused"
    player.command("seek", 24)
    assert ["seek", 24.0, "absolute+exact"] in commands
    assert player.command("loop", True)["loop"] is True
    with pytest.raises(ServerPlaybackError, match="unsupported"):
        player.command("invalid")

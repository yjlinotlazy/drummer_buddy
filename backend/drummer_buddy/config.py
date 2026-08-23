from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    library_dir: Path
    host: str = "127.0.0.1"
    port: int = 7005
    recording_dir: Path | None = None
    recording_device: str = "pulse"
    drumless_format: str = "flac"
    analysis_python: Path | None = None

    @property
    def database_path(self) -> Path:
        return self.library_dir / "drummer_buddy.sqlite3"


def default_config_path() -> Path:
    override = os.environ.get("DRUMMER_BUDDY_CONFIG")
    return Path(override).expanduser() if override else Path.home() / ".config" / "drummer_buddy" / "config.yaml"


def load_config(path: Path | None = None) -> Config:
    config_path = path or default_config_path()
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        project_root = Path(__file__).resolve().parents[2]
        defaults = {
            "library_dir": str(Path.home() / "Music" / "Drummer Buddy"),
            "server": {"host": "127.0.0.1", "port": 7005},
            "recording_dir": str(Path.home() / "Music" / "Drummer Buddy Recordings"),
            "recording_device": "pulse",
            "drumless_format": "flac",
            "analysis_python": str(project_root / ".analysis-venv" / "bin" / "python"),
        }
        config_path.write_text(yaml.safe_dump(defaults, sort_keys=False), encoding="utf-8")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not raw.get("library_dir"):
        raise ValueError(f"library_dir is required in {config_path}")
    server = raw.get("server") or {}
    output_format = str(raw.get("drumless_format", "flac")).lower()
    if output_format not in {"flac", "wav"}:
        raise ValueError("drumless_format must be flac or wav")

    default_analysis_python = Path(__file__).resolve().parents[2] / ".analysis-venv" / "bin" / "python"
    config = Config(
        library_dir=Path(raw["library_dir"]).expanduser().resolve(),
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 7005)),
        recording_dir=Path(raw.get("recording_dir", Path(raw["library_dir"]) / "recordings")).expanduser().absolute(),
        recording_device=str(raw.get("recording_device", "pulse")),
        drumless_format=output_format,
        analysis_python=Path(raw.get("analysis_python", default_analysis_python)).expanduser().resolve(),
    )
    config.library_dir.mkdir(parents=True, exist_ok=True)
    (config.library_dir / ".incoming").mkdir(exist_ok=True)
    (config.library_dir / "songs").mkdir(exist_ok=True)
    assert config.recording_dir is not None
    config.recording_dir.mkdir(parents=True, exist_ok=True)
    return config

from pathlib import Path

from drummer_buddy.config import load_config


def test_analysis_python_keeps_virtualenv_symlink(tmp_path: Path) -> None:
    real_python = tmp_path / "real-python"
    real_python.touch()
    virtualenv_python = tmp_path / "venv-python"
    virtualenv_python.symlink_to(real_python)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"library_dir: {tmp_path / 'library'}\nanalysis_python: {virtualenv_python}\ndrumless_format: mp3\n",
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.analysis_python == virtualenv_python
    assert config.drumless_format == "mp3"

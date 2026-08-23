from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def use_project_environment() -> None:
    if VENV_PYTHON.is_file() and Path(sys.prefix).resolve() != (PROJECT_ROOT / ".venv").resolve():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


def main() -> None:
    use_project_environment()

    import uvicorn

    from drummer_buddy.config import load_config

    config = load_config()
    parser = argparse.ArgumentParser(description="Serve Drummer Buddy")
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("drummer_buddy.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

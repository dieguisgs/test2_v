"""Entry point: `python run.py <command>`.

    python run.py make-synthetic                 Generate synthetic VWAPs from EEX
    python run.py refill --from 2026-08-10        Fill a historical date range
    python run.py daily --date 2026-09-29         Fill one reference date
    python run.py catchup                        Process pending dates through today
    python run.py backtest                       Compare leave-one-out errors
    python run.py tune --validation-days 20      Select parameters, then validate on later dates
    python run.py detect-conventions             Compare D+n / WE+n conventions

Use the adjacent config.toml unless --config overrides it. If the current
Python lacks the dependencies, relaunch through `uv run`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent


def main() -> int:
    args = sys.argv[1:]
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except ImportError:
        return _relaunch_with_uv(args)

    sys.path.insert(0, str(PROJECT / "src"))
    from vwaps.cli import main as cli_main

    return cli_main(args, config_path=PROJECT / "config.toml")


def _relaunch_with_uv(args: list[str]) -> int:
    if os.environ.get("VWAPS_RELAUNCHED"):
        print("Dependencies are missing even inside `uv run`; try `uv sync`.", file=sys.stderr)
        return 1
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
    if not Path(uv).exists() and not shutil.which(uv):
        print("This Python lacks the dependencies and `uv` was not found.", file=sys.stderr)
        return 1
    env = {**os.environ, "VWAPS_RELAUNCHED": "1"}
    cmd = [uv, "run", "--project", str(PROJECT), "python", str(Path(__file__).resolve()), *args]
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    sys.exit(main())

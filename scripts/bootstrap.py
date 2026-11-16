#!/usr/bin/env python3
"""Prepare build dependencies. Does not install hooks, services, or firmware."""

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without changing anything")
    args = parser.parse_args()
    environment = Path.home() / "Library/Application Support/Keychron Agent Bridge/venv"
    python = environment / "bin/python"
    commands = [] if python.exists() else [[sys.executable, "-m", "venv", str(environment)]]
    commands.append([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.lock")])
    for command in commands:
        print(shlex.join(command), flush=True)
    if args.dry_run:
        return
    if sys.platform != "darwin" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Run this setup on macOS with python3.12. Tests can run without a Mac or keyboard.")
    for command in commands:
        subprocess.run(command, check=True)
    print("Dependencies ready. Next: sh scripts/build.sh")
    print("Review the setup guide before running scripts/install.py.")


if __name__ == "__main__":
    main()

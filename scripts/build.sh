#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bridge_python="$HOME/Library/Application Support/Keychron Agent Bridge/venv/bin/python"
cd "$project_dir/packaging"
"$bridge_python" - "$project_dir" <<'PY'
import pathlib, shutil, sys
staging = pathlib.Path(sys.argv[1]) / "build/app-dist"
if staging.exists():
    shutil.rmtree(staging)
PY
"$bridge_python" setup.py py2app --dist-dir "$project_dir/build/app-dist"
"$bridge_python" - "$project_dir" <<'PY'
import pathlib, shutil, sys
root = pathlib.Path(sys.argv[1])
source = root / "build/app-dist/Keychron Agent Bridge.app"
target = root / "dist/Keychron Agent Bridge.app"
if not source.is_dir():
    raise SystemExit("Packaging did not produce the expected app")
target.parent.mkdir(exist_ok=True)
if target.exists():
    shutil.rmtree(target)
shutil.move(source, target)
PY
/usr/bin/codesign --force --deep --sign - "$project_dir/dist/Keychron Agent Bridge.app"

import sys
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

setup(
    name="Keychron Agent Bridge",
    version="0.1.0",
    packages=[],
    app=[str(ROOT / "launcher.py")],
    options={"py2app": {
        "argv_emulation": False,
        "packages": ["keychron_bridge"],
        "includes": ["hid", "AppKit", "Foundation", "PyObjCTools.AppHelper"],
        "excludes": ["pytest", "claude_agent_sdk", "tkinter", "matplotlib", "numpy"],
        "plist": {
            "CFBundleName": "Keychron Agent Bridge",
            "CFBundleDisplayName": "Keychron Agent Bridge",
            "CFBundleIdentifier": "io.nativz.keychron-agent-bridge",
            "CFBundleShortVersionString": "0.1.0",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    }},
)

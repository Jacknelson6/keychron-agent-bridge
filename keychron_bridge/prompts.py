"""Editable prompt presets and delivery through the original interactive CLIs."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from .hook import publish
from .paths import write_json


@dataclass(frozen=True)
class Preset:
    title: str
    prompt: str


PRESETS = {
    "review": Preset("Review changes", "Review the current uncommitted changes in this project. "
        "Read the project's agent instructions first. Prioritize concrete bugs, regressions, and "
        "missing validation. Explain actionable findings with file references. Do not edit files."),
    "explain": Preset("Explain clipboard", "Explain the following code or text in plain English. "
        "Use this project's context when relevant, and identify anything that needs closer review. "
        "Do not modify files.\n\n{clipboard}"),
    "checks": Preset("Run project checks", "Read this project's agent instructions and find its "
        "documented local validation commands. Run the checks appropriate to the current changes, "
        "serially. Report the results and actionable failures. Do not edit files, install packages, "
        "start development servers, push, or deploy."),
    "summary": Preset("Summarize session", "Summarize this session in plain English for Jack. "
        "Lead with the outcome, then give 3 to 7 short bullets covering what changed, validation, "
        "and anything unfinished. State whether the work is discussed, committed, or shipped. "
        "Use fewer than 120 words and no em dashes. If this is a new session, say that no earlier "
        "session history is available and summarize the project's visible current changes instead."),
}


def find_cli(agent: str) -> str:
    if agent not in ("claude", "codex"):
        raise ValueError("Unknown agent")
    candidates = [Path.home() / ".local/bin" / agent, Path("/opt/homebrew/bin") / agent]
    found = shutil.which(agent)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise FileNotFoundError(f"The {agent} CLI is not installed")


def command_arguments(task: dict) -> list[str]:
    agent = task["agent"]
    executable = find_cli(agent)
    project = Path(task["cwd"]).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Choose a project directory")
    prompt = task["prompt"]
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
        raise ValueError("Prompt must contain 1 to 100,000 characters")
    sid = task.get("resume")
    if sid:
        uuid.UUID(sid)
    if agent == "codex":
        return [executable, "-C", str(project)] + (["resume", sid] if sid else []) + ["--", prompt]
    return [executable] + (["--resume", sid] if sid else []) + ["--", prompt]


def prepare_task(root: Path, agent: str, cwd: str, prompt: str, resume: str | None = None) -> Path:
    task = {"agent": agent, "cwd": cwd, "prompt": prompt, "resume": resume}
    command_arguments(task)  # Validate before creating a launchable file.
    directory = root / "tasks"
    directory.mkdir(mode=0o700, exist_ok=True)
    identifier = str(uuid.uuid4())
    request = directory / f"{identifier}.json"
    write_json(request, task)
    launcher = directory / f"{agent}-{identifier}.command"
    # Only application-controlled paths enter shell text. User prompts remain JSON data.
    python = root / "venv/bin/python"
    runner = root / "run-task.py"
    launcher.write_text("#!/bin/zsh\nexec " + shlex.join([str(python), str(runner), str(request)]) + "\n")
    launcher.chmod(0o700)
    return launcher


def run_task(request_path: str) -> int:
    path = Path(request_path).resolve()
    task = json.loads(path.read_text())
    status = 1
    try:
        arguments = command_arguments(task)
        environment = os.environ.copy()
        # Login shells and GUI launches need the normal CLI installation locations.
        environment["PATH"] = str(Path.home() / ".local/bin") + ":/opt/homebrew/bin:/usr/bin:/bin:" + environment.get("PATH", "")
        status = subprocess.call(arguments, cwd=task["cwd"], env=environment)
        if status not in (0, 130):
            publish({"kind": "event", "agent": task["agent"], "session_id": "launch:" + path.stem,
                     "cwd": task["cwd"], "event": "LaunchFailed"})
    except KeyboardInterrupt:
        status = 130
    finally:
        path.unlink(missing_ok=True)
        (path.parent / f"{task['agent']}-{path.stem}.command").unlink(missing_ok=True)
    return status


if __name__ == "__main__":
    sys.exit(run_task(sys.argv[1]))

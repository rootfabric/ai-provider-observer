from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


def portable_clone_validate(root: Path) -> list[str]:
    """Re-run the completion validator in an ordinary fresh clone.

    The child run explicitly disables recursive portable checking. Local reflogs,
    worktrees and refs/replace are intentionally absent from the clone.
    """
    errors: list[str] = []
    env = os.environ.copy()
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    with tempfile.TemporaryDirectory(prefix="hybrid-harness-r8-portable-") as td:
        clone = Path(td) / "repo"
        proc = subprocess.run(
            ["git", "clone", "-q", "--no-local", str(root), str(clone)],
            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            return [f"PORTABLE_CLONE_FAILED:{proc.stderr.strip()}"]
        child_env = env.copy()
        child_env["HARNESS_PORTABLE_CHILD"] = "1"
        state_path = clone / "config/control/project-state.v1.json"
        command = "validate"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            active = state.get("active_mission") if isinstance(state, dict) else None
            if isinstance(state, dict) and (state.get("status") == "MISSION_COMPLETE" or (isinstance(active, dict) and active.get("complete") is True)):
                command = "validate-ready"
        except Exception:
            command = "validate"
        proc = subprocess.run(
            [str(clone / "CONTROL_HARNESS.sh"), command],
            cwd=clone, env=child_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if proc.returncode != 0:
            tail = " | ".join(proc.stdout.strip().splitlines()[-12:])
            errors.append(f"PORTABLE_CLEAN_CLONE_VALIDATION_FAILED:{tail}")
        # Defense in depth: an ordinary clone must not acquire replace refs.
        repl = subprocess.run(
            ["git", "-C", str(clone), "replace", "-l"], env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if repl.stdout.strip():
            errors.append(f"PORTABLE_CLONE_HAS_REPLACE_REFS:{repl.stdout.strip()}")
    return errors

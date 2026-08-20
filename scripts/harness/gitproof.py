from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Iterable


class GitProofError(ValueError):
    pass


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    # R8: acceptance truth must never depend on local refs/replace overlays.
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _git_cmd(root: Path, *args: str) -> list[str]:
    return ["git", "-C", str(root), *args]


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(_git_cmd(root, *args), env=_git_env(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise GitProofError(f"GIT_COMMAND_FAILED:{' '.join(args)}:{proc.stderr.strip()}")
    return proc.stdout.strip()


def is_repo(root: Path) -> bool:
    return git(root, "rev-parse", "--is-inside-work-tree", check=False) == "true"


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def branch(root: Path) -> str:
    return git(root, "branch", "--show-current")


def commit_exists(root: Path, commit: str) -> bool:
    proc = subprocess.run(_git_cmd(root, "cat-file", "-e", f"{commit}^{{commit}}"), env=_git_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def commit_parent(root: Path, commit: str) -> str | None:
    out = git(root, "rev-list", "--parents", "-n", "1", commit, check=False)
    parts = out.split()
    return parts[1] if len(parts) >= 2 else None


def commit_parents(root: Path, commit: str) -> list[str]:
    out = git(root, "rev-list", "--parents", "-n", "1", commit, check=False).split()
    return out[1:]


def commit_timestamp(root: Path, commit: str) -> str:
    return git(root, "show", "-s", "--format=%cI", commit)


def file_at_commit(root: Path, commit: str, path: str) -> str | None:
    proc = subprocess.run(_git_cmd(root, "show", f"{commit}:{path}"), env=_git_env(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.stdout if proc.returncode == 0 else None


def bytes_at_commit(root: Path, commit: str, path: str) -> bytes | None:
    proc = subprocess.run(_git_cmd(root, "show", f"{commit}:{path}"), env=_git_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.stdout if proc.returncode == 0 else None


def is_ancestor(root: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    proc = subprocess.run(_git_cmd(root, "merge-base", "--is-ancestor", ancestor, descendant), env=_git_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def changed_paths(root: Path, base: str, head_ref: str = "HEAD") -> list[str]:
    out = git(root, "diff", "--name-only", f"{base}..{head_ref}")
    return [line for line in out.splitlines() if line]


def commit_changed_paths(root: Path, commit: str) -> list[str]:
    parent = commit_parent(root, commit)
    if parent is None:
        out = git(root, "show", "--pretty=", "--name-only", commit)
    else:
        out = git(root, "diff", "--name-only", f"{parent}..{commit}")
    return [line for line in out.splitlines() if line]


def commits_between(root: Path, base_exclusive: str, head_inclusive: str) -> list[str]:
    out = git(root, "rev-list", "--reverse", f"{base_exclusive}..{head_inclusive}")
    return [x for x in out.splitlines() if x]


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
        elif fnmatch.fnmatchcase(path, pat):
            return True
    return False


def commit_author_email(root: Path, commit: str) -> str:
    return git(root, "show", "-s", "--format=%ae", commit)


def commit_adding_path(root: Path, path: str) -> str | None:
    out = git(root, "log", "--diff-filter=A", "--format=%H", "--", path, check=False)
    lines = [x for x in out.splitlines() if x]
    return lines[-1] if lines else None


def is_tracked(root: Path, path: str) -> bool:
    proc = subprocess.run(_git_cmd(root, "ls-files", "--error-unmatch", "--", path), env=_git_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def path_matches_head(root: Path, path: str) -> bool:
    p = root / path
    if not p.is_file() or not is_tracked(root, path):
        return False
    committed = bytes_at_commit(root, "HEAD", path)
    return committed is not None and committed == p.read_bytes()


def commits_touching_path_after(root: Path, anchor_commit: str, path: str, head_ref: str = "HEAD") -> list[str]:
    out = git(root, "log", "--format=%H", f"{anchor_commit}..{head_ref}", "--", path, check=False)
    return [x for x in out.splitlines() if x]


def path_immutable_since_add(root: Path, path: str) -> bool:
    """True only when the path was added once and never touched afterwards.

    R7 compared only first-vs-final bytes, allowing mutate->use->revert laundering.
    R8 makes immutability a history property.
    """
    add = commit_adding_path(root, path)
    if not add:
        return False
    if bytes_at_commit(root, add, path) is None or bytes_at_commit(root, "HEAD", path) is None:
        return False
    return not commits_touching_path_after(root, add, path)


def replace_refs(root: Path) -> list[str]:
    out = git(root, "replace", "-l", check=False)
    return [x for x in out.splitlines() if x]


def blob_oid_at_commit(root: Path, commit: str, path: str) -> str | None:
    out = git(root, "rev-parse", f"{commit}:{path}", check=False)
    value = out.strip()
    return value if len(value) == 40 and all(ch in "0123456789abcdef" for ch in value) else None


def worktree_blob_oid(root: Path, path: str) -> str | None:
    proc = subprocess.run(_git_cmd(root, "hash-object", "--", path), env=_git_env(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    value = proc.stdout.strip() if proc.returncode == 0 else ""
    return value if len(value) == 40 and all(ch in "0123456789abcdef" for ch in value) else None


def trajectory_violations(root: Path, base_exclusive: str, head_inclusive: str, allowed_patterns: Iterable[str]) -> list[tuple[str, str]]:
    """Return every (commit,path) violating the allowed path policy.

    Unlike net diff, this cannot be laundered by a later revert.
    """
    bad: list[tuple[str, str]] = []
    for commit in commits_between(root, base_exclusive, head_inclusive):
        for path in commit_changed_paths(root, commit):
            if not path_matches(path, allowed_patterns):
                bad.append((commit, path))
    return bad


def worktree_changed_paths(root: Path) -> list[str]:
    out = git(root, "status", "--porcelain=v1", "--untracked-files=all", check=False)
    paths: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        text = line[3:]
        if " -> " in text:
            text = text.split(" -> ", 1)[1]
        paths.append(text.strip('"'))
    return paths


def suspicious_reflog_actions(root: Path, branch_name: str, since_iso: str | None) -> list[str]:
    if not is_repo(root):
        return []
    args = ["reflog", "show", "--format=%ad%x09%gs", "--date=iso-strict"]
    if since_iso:
        args.append(f"--since={since_iso}")
    args.append(branch_name)
    out = git(root, *args, check=False)
    bad: list[str] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        _, action = line.split("\t", 1)
        lowered = action.lower()
        if "commit (amend)" in lowered or lowered.startswith("reset:") or "rebase" in lowered:
            bad.append(action)
    return bad


def suspicious_reflog_actions_after_ref(root: Path, branch_name: str, anchor_sha: str) -> list[str]:
    """Local diagnostic: scan reflog entries newer than anchor_sha.

    This is not a durable acceptance proof (clean clones may not retain reflog),
    so R6 safety never depends on it. It is useful for catching local amend/reset/rebase.
    """
    if not is_repo(root):
        return []
    out = git(root, "reflog", "show", "--format=%H%x09%gs", branch_name, check=False)
    bad: list[str] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, action = line.split("\t", 1)
        if sha == anchor_sha:
            break
        lowered = action.lower()
        if "commit (amend)" in lowered or lowered.startswith("reset:") or "rebase" in lowered:
            bad.append(action)
    return bad

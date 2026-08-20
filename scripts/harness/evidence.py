from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strictjson import StrictJSONError, load
from gitproof import (
    commit_adding_path,
    head as git_head,
    is_ancestor,
    path_immutable_since_add,
    path_matches_head,
    worktree_changed_paths,
    commit_exists,
    changed_paths,
    path_matches,
    trajectory_violations,
    worktree_blob_oid,
)

BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "COMSPEC", "PATHEXT")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _sanitized_env(overrides: dict[str, str]) -> dict[str, str]:
    env = {key: os.environ[key] for key in BASE_ENV_KEYS if key in os.environ}
    env.update(overrides)
    return env


def _non_evidence_dirty(root: Path) -> list[str]:
    return [p for p in worktree_changed_paths(root) if not (p == "evidence" or p.startswith("evidence/"))]


def write_receipt(root: Path, receipt_id: str, command: list[str], env_overrides: dict[str, str] | None = None, *, subject_override: str | None = None, input_paths: list[str] | None = None) -> int:
    if not receipt_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in receipt_id):
        raise ValueError("RECEIPT_ID_INVALID")
    if not command:
        raise ValueError("EVIDENCE_COMMAND_REQUIRED")
    env_overrides = dict(env_overrides or {})
    input_paths = list(input_paths or [])
    dirty = _non_evidence_dirty(root)
    if dirty:
        raise ValueError(f"EVIDENCE_SUBJECT_TREE_DIRTY:{','.join(dirty[:12])}")

    execution_head = git_head(root)
    subject = execution_head
    lock_path = root / "evidence" / "candidate-lock.v1.json"
    candidate = None
    if lock_path.is_file():
        lock = load(lock_path)
        if isinstance(lock, dict) and isinstance(lock.get("candidate_head"), str):
            candidate = lock["candidate_head"]
    if subject_override:
        subject = candidate if subject_override == "candidate" else subject_override
        if not isinstance(subject, str) or not commit_exists(root, subject):
            raise ValueError(f"EVIDENCE_SUBJECT_INVALID:{subject}")
        if not is_ancestor(root, subject, execution_head):
            raise ValueError(f"EVIDENCE_SUBJECT_NOT_ANCESTOR:{subject}:{execution_head}")
        policy = load(root / "config/control/harness/harness-policy.v1.json")
        patterns = policy.get("closure_tail", {}).get("allowed_paths", []) if isinstance(policy, dict) else []
        bad = trajectory_violations(root, subject, execution_head, patterns)
        if bad:
            rendered = ",".join(f"{c[:12]}:{p}" for c,p in bad[:12])
            raise ValueError(f"EVIDENCE_EXECUTION_HEAD_CHANGES_SUBJECT:{rendered}")

    out_dir = root / "evidence" / "raw"
    receipt_dir = root / "evidence" / "receipts"
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{receipt_id}.log"
    receipt_path = receipt_dir / f"{receipt_id}.json"
    if receipt_path.exists() or output_path.exists():
        raise ValueError(f"RECEIPT_ALREADY_EXISTS:{receipt_id}")

    env = _sanitized_env(env_overrides)
    executable = shutil.which(command[0], path=env.get("PATH")) or command[0]
    started = utc_now()
    proc = subprocess.run(command, cwd=root, env=env, text=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    finished = utc_now()
    output = proc.stdout or b""
    output_path.write_bytes(output)
    receipt = {
        "schema": "hybrid_harness.evidence_receipt.v4",
        "receipt_id": receipt_id,
        "subject_head": subject,
        "execution_head": execution_head,
        "command": command,
        "cwd": ".",
        "environment_mode": "SANITIZED_PLUS_DECLARED_OVERRIDES",
        "environment_overrides": env_overrides,
        "base_environment": {key: env.get(key) for key in BASE_ENV_KEYS if key in env},
        "resolved_executable": executable,
        "python_version": sys.version.split()[0] if Path(executable).name.startswith("python") else None,
        "clean_subject_tree": True,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "exit_code": proc.returncode,
        "output_path": output_path.relative_to(root).as_posix(),
        "output_sha256": sha256_bytes(output),
        "input_files": [
            {"path": rel, "sha256": sha256_file(root / rel)}
            for rel in input_paths if (root / rel).is_file()
        ],
        "runner": "HARNESS_COMMAND_API_R8",
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.write(1, output)
    print(f"\nEVIDENCE_RECEIPT={receipt_path.relative_to(root)}")
    print(f"EVIDENCE_SUBJECT_HEAD={subject}")
    print(f"EVIDENCE_EXIT_CODE={proc.returncode}")
    return proc.returncode


def _durable_path_errors(root: Path, rel: str, code_prefix: str) -> list[str]:
    errors: list[str] = []
    if not path_matches_head(root, rel):
        errors.append(f"{code_prefix}_NOT_DURABLE_AT_HEAD:{rel}")
    if not path_immutable_since_add(root, rel):
        errors.append(f"{code_prefix}_MUTATED_AFTER_ADD:{rel}")
    return errors


def validate_receipt(root: Path, path: Path, expected_head: str) -> list[str]:
    errors: list[str] = []
    rel = path.relative_to(root).as_posix()
    try:
        r = load(path)
    except (StrictJSONError, OSError) as exc:
        return [f"EVIDENCE_RECEIPT_INVALID:{path}:{exc}"]
    if not isinstance(r, dict):
        return [f"EVIDENCE_RECEIPT_INVALID:{path}:object required"]
    errors.extend(_durable_path_errors(root, rel, "EVIDENCE_RECEIPT"))
    if r.get("runner") != "HARNESS_COMMAND_API_R8":
        errors.append(f"EVIDENCE_RECEIPT_RUNNER_UNTRUSTED:{path}")
    if r.get("subject_head") != expected_head:
        errors.append(f"EVIDENCE_RECEIPT_STALE:{path}:{r.get('subject_head')}!={expected_head}")
    execution_head = r.get("execution_head")
    if not isinstance(execution_head, str) or not commit_exists(root, execution_head) or not is_ancestor(root, execution_head, "HEAD"):
        errors.append(f"EVIDENCE_EXECUTION_HEAD_INVALID:{path}:{execution_head}")
    elif not is_ancestor(root, expected_head, execution_head):
        errors.append(f"EVIDENCE_EXECUTION_HEAD_NOT_AFTER_SUBJECT:{path}:{execution_head}")
    inputs = r.get("input_files", [])
    if not isinstance(inputs, list):
        errors.append(f"EVIDENCE_INPUTS_INVALID:{path}")
    else:
        for item in inputs:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                errors.append(f"EVIDENCE_INPUT_INVALID:{path}:{item!r}")
                continue
            rel_input = item["path"]
            if not (root / rel_input).is_file():
                errors.append(f"EVIDENCE_INPUT_MISSING:{path}:{rel_input}")
                continue
            errors.extend(_durable_path_errors(root, rel_input, "EVIDENCE_INPUT"))
            if sha256_file(root / rel_input) != item["sha256"]:
                errors.append(f"EVIDENCE_INPUT_HASH_MISMATCH:{path}:{rel_input}")
    if r.get("exit_code") != 0:
        errors.append(f"EVIDENCE_RECEIPT_FAILED:{path}:exit={r.get('exit_code')}")
    if r.get("clean_subject_tree") is not True:
        errors.append(f"EVIDENCE_RECEIPT_DIRTY_SUBJECT:{path}")
    if r.get("environment_mode") != "SANITIZED_PLUS_DECLARED_OVERRIDES":
        errors.append(f"EVIDENCE_RECEIPT_ENVIRONMENT_UNCAPTURED:{path}")
    if not isinstance(r.get("environment_overrides"), dict):
        errors.append(f"EVIDENCE_RECEIPT_ENVIRONMENT_INVALID:{path}")
    if r.get("cwd") != ".":
        errors.append(f"EVIDENCE_RECEIPT_CWD_UNEXPECTED:{path}")
    out_rel = r.get("output_path")
    if not isinstance(out_rel, str) or not (root / out_rel).is_file():
        errors.append(f"EVIDENCE_OUTPUT_MISSING:{path}")
    else:
        errors.extend(_durable_path_errors(root, out_rel, "EVIDENCE_OUTPUT"))
        actual = sha256_file(root / out_rel)
        if actual != r.get("output_sha256"):
            errors.append(f"EVIDENCE_OUTPUT_HASH_MISMATCH:{path}")
    return errors


def write_candidate_lock(root: Path, closure_patterns: list[str]) -> Path:
    dirty = worktree_changed_paths(root)
    if dirty:
        raise ValueError(f"CANDIDATE_FREEZE_REQUIRES_CLEAN_TREE:{','.join(dirty[:12])}")
    subject = git_head(root)
    branch = subprocess.run(["git", "-C", str(root), "branch", "--show-current"], text=True, stdout=subprocess.PIPE, check=True).stdout.strip()
    path = root / "evidence" / "candidate-lock.v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = load(path)
        if isinstance(old, dict) and old.get("candidate_head") != subject:
            raise ValueError("CANDIDATE_ALREADY_FROZEN_AT_DIFFERENT_HEAD")
        return path
    data = {
        "schema": "hybrid_harness.candidate_lock.v3",
        "candidate_head": subject,
        "branch": branch,
        "created_at_utc": utc_now(),
        "closure_allowed_paths": closure_patterns,
        "rule": "Any non-closure diff after candidate invalidates review freshness.",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def event_hash(path: Path) -> str:
    return sha256_file(path)


def write_event(root: Path, phase: str, actor_id: str, verdict: str, extra: dict[str, Any] | None = None) -> Path:
    event_dir = root / "evidence" / "events"
    event_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(p for p in event_dir.glob("*.json") if p.is_file())
    seq = len(existing) + 1
    prev = event_hash(existing[-1]) if existing else None
    lock = load(root / "evidence" / "candidate-lock.v1.json")
    if not isinstance(lock, dict) or not isinstance(lock.get("candidate_head"), str):
        raise ValueError("EVENT_REQUIRES_CANDIDATE_LOCK")
    event = {
        "schema": "hybrid_harness.event.v2",
        "seq": seq,
        "prev_event_sha256": prev,
        "phase": phase,
        "actor_id": actor_id,
        "subject_head": lock["candidate_head"],
        "verdict": verdict,
        "recorded_at_utc": utc_now(),
    }
    if extra:
        overlap = set(event) & set(extra)
        if overlap:
            raise ValueError(f"EVENT_RESERVED_FIELD:{','.join(sorted(overlap))}")
        event.update(extra)

    # R8: a consumer event must bind the exact bytes/object id of any external
    # attestation it consumes. This prevents mutate->consume->revert laundering.
    for container_name in ("assurance", "authorization"):
        container = event.get(container_name)
        if not isinstance(container, dict):
            continue
        rel = container.get("attestation")
        if not isinstance(rel, str):
            continue
        att_path = root / rel
        if not att_path.is_file():
            raise ValueError(f"ATTESTATION_BINDING_SOURCE_MISSING:{rel}")
        container["attestation_sha256"] = sha256_file(att_path)
        blob = worktree_blob_oid(root, rel)
        if not blob:
            raise ValueError(f"ATTESTATION_BINDING_BLOB_FAILED:{rel}")
        container["attestation_git_blob"] = blob

    slug = phase.lower().replace("_", "-")
    path = event_dir / f"{seq:04d}-{slug}.json"
    path.write_text(json.dumps(event, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def validate_event_chain(root: Path, candidate_head: str) -> tuple[list[str], list[tuple[Path, dict[str, Any], str | None]]]:
    event_dir = root / "evidence" / "events"
    if not event_dir.is_dir():
        return ["EVIDENCE_EVENT_CHAIN_MISSING"], []
    paths = sorted(p for p in event_dir.glob("*.json") if p.is_file())
    if not paths:
        return ["EVIDENCE_EVENT_CHAIN_EMPTY"], []
    errors: list[str] = []
    events: list[tuple[Path, dict[str, Any], str | None]] = []
    prev_hash: str | None = None
    prev_add_commit: str | None = None
    expected_seq = 1
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            event = load(path)
        except StrictJSONError as exc:
            errors.append(f"EVIDENCE_EVENT_INVALID:{path}:{exc}")
            continue
        if not isinstance(event, dict):
            errors.append(f"EVIDENCE_EVENT_INVALID:{path}:object required")
            continue
        if event.get("seq") != expected_seq:
            errors.append(f"EVIDENCE_EVENT_SEQUENCE_INVALID:{path}:expected={expected_seq}:got={event.get('seq')}")
        if event.get("subject_head") != candidate_head:
            errors.append(f"EVIDENCE_EVENT_STALE:{path}")
        if expected_seq == 1:
            if event.get("prev_event_sha256") not in (None, ""):
                errors.append(f"EVIDENCE_EVENT_CHAIN_ROOT_INVALID:{path}")
        elif event.get("prev_event_sha256") != prev_hash:
            errors.append(f"EVIDENCE_EVENT_CHAIN_BROKEN:{path}")
        errors.extend(_durable_path_errors(root, rel, "EVIDENCE_EVENT"))
        add_commit = commit_adding_path(root, rel)
        if prev_add_commit and add_commit:
            if add_commit == prev_add_commit or not is_ancestor(root, prev_add_commit, add_commit):
                errors.append(f"EVIDENCE_EVENT_COMMIT_ORDER_INVALID:{rel}")
        prev_add_commit = add_commit
        prev_hash = event_hash(path)
        expected_seq += 1
        events.append((path, event, add_commit))
    return errors, events

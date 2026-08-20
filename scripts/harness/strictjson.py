from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StrictJSONError(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StrictJSONError(f"JSON_DUPLICATE_KEY:{key}")
        out[key] = value
    return out


def loads(text: str, *, label: str = "<memory>") -> Any:
    try:
        return json.loads(text, object_pairs_hook=_pairs)
    except StrictJSONError as exc:
        raise StrictJSONError(f"{label}:{exc}") from exc
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f"JSON_INVALID:{label}:{exc.msg}:line={exc.lineno}:col={exc.colno}") from exc


def load(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StrictJSONError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    return loads(text, label=str(path))

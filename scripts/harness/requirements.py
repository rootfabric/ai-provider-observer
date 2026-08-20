from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from semantic import infer_semantic_tags, required_partitions


@dataclass(frozen=True)
class NormativeClause:
    clause_id: str
    line: int
    text: str
    sha256: str


_NORMATIVE_RE = re.compile(
    r"(?:\bmust\b|\bmust not\b|\bshall\b|\bshould\b|\brequired\b|\brequires\b|"
    r"\bcannot\b|\bcan not\b|\bmay not\b|\bis forbidden\b|\bare forbidden\b|"
    r"\bonly\b|\bnever\b|\bno\s+.+\bmay\b|\bno\s+.+\bcan\b|"
    r"\bдолжен\b|\bдолжна\b|\bдолжно\b|\bдолжны\b|\bне должен\b|\bне должна\b|"
    r"\bобязан\b|\bобязана\b|\bобязательно\b|\bнельзя\b|\bзапрещено\b|\bтолько\b|\bникогда\b)",
    re.IGNORECASE,
)
_IMPERATIVE_RE = re.compile(
    r"^(?:implement|create|build|verify|ensure|reject|preserve|record|return|use|store|persist|"
    r"реализуй|создай|проверь|обеспечь|запрети|сохрани|верни|используй|храни)\b",
    re.IGNORECASE,
)
_NORMATIVE_HEADINGS = (
    "requirement", "requirements", "mandatory", "rules", "constraints", "acceptance", "invariant",
    "обязатель", "требован", "правил", "ограничен", "критер", "инвариант",
)


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[[ xX]\])\s+(.*)$")


def _clean_line(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^[-*+]\s+", "", text)
    text = re.sub(r"^\d+[.)]\s+", "", text)
    text = re.sub(r"^\[[ xX]\]\s+", "", text)
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    # Deterministic sentence segmentation after Markdown list continuation
    # lines have been folded into their logical bullet.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9`])", text)
    return [p.strip() for p in parts if p.strip()]


def _markdown_units(specification_text: str) -> list[tuple[int, str, bool, bool]]:
    """Return logical Markdown units as (start_line, text, list_like, normative_section).

    R10 folds both list-item continuations and ordinary prose paragraphs before
    sentence segmentation. Physical Markdown wrapping therefore cannot create
    or delete normative clauses.
    """
    out: list[tuple[int, str, bool, bool]] = []
    in_code = False
    normative_section = False
    pending_line: int | None = None
    pending_parts: list[str] = []
    pending_normative = False
    pending_list_like = False

    def flush_pending() -> None:
        nonlocal pending_line, pending_parts, pending_normative, pending_list_like
        if pending_line is not None and pending_parts:
            text = " ".join(part.strip() for part in pending_parts if part.strip()).strip()
            if text:
                out.append((pending_line, text, pending_list_like, pending_normative))
        pending_line = None
        pending_parts = []
        pending_normative = False
        pending_list_like = False

    for line_no, raw in enumerate(specification_text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush_pending()
            in_code = not in_code
            continue
        if in_code:
            continue
        if not stripped:
            flush_pending()
            continue
        if stripped.startswith("#"):
            flush_pending()
            heading = stripped.lstrip("#").strip().casefold()
            normative_section = any(token in heading for token in _NORMATIVE_HEADINGS)
            continue

        m = _LIST_ITEM_RE.match(raw)
        if m:
            flush_pending()
            pending_line = line_no
            pending_parts = [m.group(1).strip()]
            pending_normative = normative_section
            pending_list_like = True
            continue

        if pending_line is not None and pending_list_like and raw[:1].isspace():
            pending_parts.append(stripped)
            continue

        # A non-list line continues the current prose paragraph until a blank,
        # heading, code fence or list item starts a new logical unit.
        if pending_line is not None and not pending_list_like:
            pending_parts.append(_clean_line(raw))
            continue

        flush_pending()
        cleaned = _clean_line(raw)
        if cleaned:
            pending_line = line_no
            pending_parts = [cleaned]
            pending_normative = normative_section
            pending_list_like = False

    flush_pending()
    return out


def extract_normative_clauses(specification_text: str) -> list[NormativeClause]:
    clauses: list[NormativeClause] = []
    for line_no, logical_text, list_like, normative_section in _markdown_units(specification_text):
        for sentence in _split_sentences(logical_text):
            is_normative = bool(_NORMATIVE_RE.search(sentence) or _IMPERATIVE_RE.search(sentence))
            if normative_section and list_like:
                is_normative = True
            if not is_normative:
                continue
            digest = hashlib.sha256(sentence.encode("utf-8")).hexdigest()
            clauses.append(NormativeClause(
                clause_id=f"CLAUSE-L{line_no}-{digest[:10]}",
                line=line_no,
                text=sentence,
                sha256=digest,
            ))
    return clauses

def _requirement_text(req: dict[str, Any], clause_map: dict[str, NormativeClause]) -> str:
    parts = [str(req.get("statement", ""))]
    for cid in req.get("source_clause_ids", []) if isinstance(req.get("source_clause_ids"), list) else []:
        clause = clause_map.get(cid)
        if clause:
            parts.append(clause.text)
    return "\n".join(parts)


def validate_requirements_manifest(
    manifest: dict[str, Any],
    *,
    specification_text: str,
    specification_rel: str,
    specification_sha256: str,
    mission_id: str,
    work_order_id: str,
    acceptance: dict[str, Any],
    semantic_policy: dict[str, Any],
) -> tuple[list[str], list[NormativeClause]]:
    errors: list[str] = []
    required_top = {
        "schema", "manifest_id", "mission_id", "work_order_id", "specification",
        "specification_sha256", "requirements"
    }
    missing = sorted(required_top - set(manifest))
    for key in missing:
        errors.append(f"REQUIREMENTS_MANIFEST_FIELD_MISSING:{key}")
    if manifest.get("schema") != "hybrid_harness.requirements_manifest.v1":
        errors.append(f"REQUIREMENTS_MANIFEST_SCHEMA_INVALID:{manifest.get('schema')}")
    if manifest.get("mission_id") != mission_id or manifest.get("work_order_id") != work_order_id:
        errors.append("REQUIREMENTS_MANIFEST_SUBJECT_MISMATCH")
    if manifest.get("specification") != specification_rel:
        errors.append(f"REQUIREMENTS_MANIFEST_SPEC_REF_MISMATCH:{manifest.get('specification')}")
    if manifest.get("specification_sha256") != specification_sha256:
        errors.append("REQUIREMENTS_MANIFEST_SPEC_HASH_MISMATCH")

    clauses = extract_normative_clauses(specification_text)
    clause_map = {c.clause_id: c for c in clauses}
    requirements = manifest.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("REQUIREMENTS_MANIFEST_EMPTY")
        return errors, clauses

    req_by_id: dict[str, dict[str, Any]] = {}
    mapped_clauses: dict[str, list[str]] = {}
    predicate_by_id = {
        p.get("predicate_id"): p for p in acceptance.get("predicates", [])
        if isinstance(p, dict) and isinstance(p.get("predicate_id"), str)
    } if isinstance(acceptance.get("predicates"), list) else {}

    for idx, req in enumerate(requirements):
        if not isinstance(req, dict):
            errors.append(f"REQUIREMENT_INVALID:{idx}")
            continue
        req_required = {
            "requirement_id", "statement", "source_clause_ids", "class", "semantic_tags",
            "required_partitions", "predicate_ids"
        }
        for key in sorted(req_required - set(req)):
            errors.append(f"REQUIREMENT_FIELD_MISSING:{idx}:{key}")
        rid = req.get("requirement_id")
        if not isinstance(rid, str) or not rid:
            errors.append(f"REQUIREMENT_ID_INVALID:{idx}")
            continue
        if rid in req_by_id:
            errors.append(f"REQUIREMENT_ID_DUPLICATE:{rid}")
        req_by_id[rid] = req
        source_ids = req.get("source_clause_ids")
        if not isinstance(source_ids, list) or not source_ids:
            errors.append(f"REQUIREMENT_SOURCE_CLAUSE_MISSING:{rid}")
            source_ids = []
        for cid in source_ids:
            if cid not in clause_map:
                errors.append(f"REQUIREMENT_SOURCE_CLAUSE_UNKNOWN:{rid}:{cid}")
            else:
                mapped_clauses.setdefault(cid, []).append(rid)
        predicate_ids = req.get("predicate_ids")
        if not isinstance(predicate_ids, list) or not predicate_ids:
            errors.append(f"REQUIREMENT_PREDICATE_LINK_MISSING:{rid}")
            predicate_ids = []
        for pid in predicate_ids:
            if pid not in predicate_by_id:
                errors.append(f"REQUIREMENT_PREDICATE_UNKNOWN:{rid}:{pid}")
            elif rid not in (predicate_by_id[pid].get("requirement_ids") or []):
                errors.append(f"REQUIREMENT_PREDICATE_LINK_NOT_RECIPROCAL:{rid}:{pid}")

        # Infer semantic tags from the requirement + exact source clauses.  This is
        # supplementary to clause traceability: unknown requirements still cannot disappear.
        pseudo_wo = {"mission": {"success_condition": _requirement_text(req, clause_map)}}
        pseudo_contract = {"predicates": []}
        inferred = infer_semantic_tags(semantic_policy, pseudo_wo, pseudo_contract, _requirement_text(req, clause_map))
        declared_tags = {x for x in req.get("semantic_tags", []) if isinstance(x, str)} if isinstance(req.get("semantic_tags"), list) else set()
        # R7 deliberately does not make keyword inference the source of requirement truth.
        # The full specification is machine-scanned globally by semantic_contract_errors;
        # requirement-level tags are explicit traceability metadata reviewed externally.
        # This avoids false coupling such as a conservation sentence mentioning `transfer`
        # and therefore inheriting every transaction partition.
        required_parts = {x for x in req.get("required_partitions", []) if isinstance(x, str)} if isinstance(req.get("required_partitions"), list) else set()
        for tag in declared_tags:
            for part in sorted(required_partitions(semantic_policy, tag) - required_parts):
                errors.append(f"REQUIREMENT_PARTITION_MISSING:{rid}:{tag}:{part}")
        linked_parts: set[str] = set()
        for pid in predicate_ids:
            pred = predicate_by_id.get(pid, {})
            linked_parts.update(x for x in pred.get("partitions", []) if isinstance(x, str))
        for part in sorted(required_parts - linked_parts):
            errors.append(f"REQUIREMENT_PARTITION_NOT_IN_PREDICATE:{rid}:{part}")

    for clause in clauses:
        if clause.clause_id not in mapped_clauses:
            errors.append(f"NORMATIVE_CLAUSE_UNMAPPED:{clause.clause_id}:{clause.text}")

    # Every acceptance predicate must say which source requirements it proves.
    for pid, predicate in predicate_by_id.items():
        rids = predicate.get("requirement_ids")
        if not isinstance(rids, list) or not rids:
            errors.append(f"PREDICATE_REQUIREMENT_LINK_MISSING:{pid}")
            continue
        for rid in rids:
            if rid not in req_by_id:
                errors.append(f"PREDICATE_REQUIREMENT_UNKNOWN:{pid}:{rid}")
            elif pid not in (req_by_id[rid].get("predicate_ids") or []):
                errors.append(f"PREDICATE_REQUIREMENT_LINK_NOT_RECIPROCAL:{pid}:{rid}")

    return errors, clauses


def requirement_coverage_errors(
    manifest: dict[str, Any], acceptance: dict[str, Any], verifier_manifest: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    coverage = {
        e.get("predicate_id"): e for e in verifier_manifest.get("predicate_coverage", [])
        if isinstance(e, dict) and isinstance(e.get("predicate_id"), str)
    } if isinstance(verifier_manifest.get("predicate_coverage"), list) else {}
    predicates = {
        p.get("predicate_id"): p for p in acceptance.get("predicates", [])
        if isinstance(p, dict) and isinstance(p.get("predicate_id"), str)
    } if isinstance(acceptance.get("predicates"), list) else {}
    for req in manifest.get("requirements", []) if isinstance(manifest.get("requirements"), list) else []:
        if not isinstance(req, dict) or not isinstance(req.get("requirement_id"), str):
            continue
        rid = req["requirement_id"]
        pids = [p for p in req.get("predicate_ids", []) if isinstance(p, str)] if isinstance(req.get("predicate_ids"), list) else []
        if not pids:
            errors.append(f"REQUIREMENT_EVIDENCE_PATH_MISSING:{rid}")
            continue
        for pid in pids:
            if pid not in predicates:
                errors.append(f"REQUIREMENT_PREDICATE_UNKNOWN:{rid}:{pid}")
                continue
            if pid not in coverage:
                errors.append(f"REQUIREMENT_PREDICATE_UNVERIFIED:{rid}:{pid}")
    return errors

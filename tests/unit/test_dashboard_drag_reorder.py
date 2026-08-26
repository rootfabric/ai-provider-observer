"""Manual dashboard panel reordering via drag & drop.

The dashboard is vanilla JS, so like ``test_static_assets`` these tests are
static-analysis contracts over the front-end sources: provider cards must be
draggable, the full HTML5 DnD event set must be wired, the chosen order must
persist to localStorage and every render path must go through
``applyPanelOrder`` so the order survives reloads and 30s re-renders.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "app" / "static"
APP_JS = STATIC_DIR / "app.js"
STYLE_CSS = STATIC_DIR / "style.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_provider_cards_are_draggable() -> None:
    js = _read(APP_JS)
    assert 'draggable="true"' in js, "provider cards must carry draggable=true"


def test_full_html5_dnd_event_set_wired() -> None:
    js = _read(APP_JS)
    for evt in ("dragstart", "dragover", "dragleave", "drop", "dragend"):
        pattern = re.compile(rf"addEventListener\(\s*['\"]{evt}['\"]")
        assert pattern.search(js), f"missing {evt} listener"


def test_panel_order_persists_to_localstorage() -> None:
    js = _read(APP_JS)
    m = re.search(r"PANEL_ORDER_KEY\s*=\s*['\"]([\w.]+)['\"]", js)
    assert m, "PANEL_ORDER_KEY constant missing"
    key = m.group(1)
    assert key.startswith("aio."), "order key must be namespaced"
    assert "localStorage.getItem" in js and "localStorage.setItem" in js


def test_order_access_is_failure_tolerant() -> None:
    """localStorage may throw (privacy mode/quota); reads/writes are guarded."""
    js = _read(APP_JS)
    load_m = re.search(r"function loadPanelOrder\(\)\s*\{(.*?)\n\}", js, re.S)
    save_m = re.search(r"function savePanelOrder\([^)]*\)\s*\{(.*?)\n\}", js, re.S)
    assert load_m and save_m, "loadPanelOrder/savePanelOrder missing"
    assert "try" in load_m.group(1), "loadPanelOrder must guard localStorage access"
    assert "try" in save_m.group(1), "savePanelOrder must guard localStorage access"


def test_every_render_path_uses_manual_order() -> None:
    js = _read(APP_JS)
    assert "function applyPanelOrder(" in js, "applyPanelOrder missing"
    # The main load path renders through applyPanelOrder, otherwise the next
    # 30s poll would silently reset the user's manual order.
    assert re.search(r"(?:const|let|var)\s+providers\s*=\s*applyPanelOrder\(", js)


def test_unknown_providers_fall_back_to_canonical_tail() -> None:
    """Rank of an id absent from the saved order sorts behind known ids."""
    js = _read(APP_JS)
    assert "Number.MAX_SAFE_INTEGER" in js, (
        "applyPanelOrder must rank unknown providers after saved ones"
    )


def test_drop_side_detection_for_multicolumn_grid() -> None:
    js = _read(APP_JS)
    m = re.search(r"function dropAfterPoint\([^)]*\)\s*\{(.*?)\n\}", js, re.S)
    assert m, "dropAfterPoint side-detection helper missing"
    body = m.group(1)
    assert "getBoundingClientRect" in body
    # Pointer position reaches the helper via its x/y parameters; the call
    # site must pass e.clientX/e.clientY.
    assert "clientX" in js and "clientY" in js


def test_drop_commits_dom_order_and_refreshes_table() -> None:
    js = _read(APP_JS)
    dragend_m = re.search(
        r"cards\.addEventListener\(\s*['\"]dragend['\"].*?\n\}\);", js, re.S
    )
    assert dragend_m, "dragend commit handler missing"
    assert "persistPanelOrderFromDom()" in dragend_m.group(0), (
        "drag end must persist the manual order"
    )
    fn_m = re.search(r"function persistPanelOrderFromDom\(\)\s*\{(.*?)\n\}", js, re.S)
    assert fn_m, "persistPanelOrderFromDom helper missing"
    body = fn_m.group(1)
    assert "savePanelOrder(" in body, "manual order must persist after drop"
    assert "renderBottlenecks(" in body, "risk table must follow panel order"


def test_drag_feedback_styles_present() -> None:
    css = _read(STYLE_CSS)
    for selector in (".card .grip", ".card.dragging", ".card.drop-target", ".mv-btn"):
        assert selector in css, f"missing drag feedback style: {selector}"


def test_move_buttons_fallback_wired() -> None:
    """▲/▼ buttons must exist and persist the order like a drag."""
    js = _read(APP_JS)
    assert 'data-move="up"' in js and 'data-move="down"' in js, (
        "move buttons missing from card markup"
    )
    # Buttons live inside a card-extra row: visible only when the panel
    # is expanded via "подробнее".
    m = re.search(
        r'<div class="card-extra move-row">.*?</div>', js, re.S
    )
    assert m, "move buttons must render inside the card-extra (expanded) row"
    assert "move-label" in m.group(0)
    m = re.search(r"addEventListener\(\s*['\"]click['\"].*?\.mv-btn.*?\n\}\);", js, re.S)
    assert m, "mv-btn click handler missing"
    body = m.group(0)
    assert "previousElementSibling.before" in body and "nextElementSibling.after" in body
    assert "persistPanelOrderFromDom()" in body, "buttons must persist the order"


def test_static_assets_revalidate() -> None:
    """Deployed JS/CSS must not be heuristically cached by browsers."""
    text = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "Cache-Control" in text and 'no-cache' in text
    assert 'startswith("/static")' in text

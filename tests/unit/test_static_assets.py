"""Static asset integrity tests for the dashboard front-end.

The dashboard is a vanilla-JS application: no frameworks, no build step, no
external CDNs. These tests enforce that constraint and assert that every
``id`` referenced from the JS layer is present in the HTML and that every
``href``/``src`` points to a real file inside ``app/static``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "app" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"
CHARTS_JS = STATIC_DIR / "charts.js"
STYLE_CSS = STATIC_DIR / "style.css"

ASSET_PATHS = [INDEX_HTML, APP_JS, CHARTS_JS, STYLE_CSS]

# Pattern that flags any external script/style/image/font reference. We allow
# the SVG namespace URL (required for inline SVG construction) but nothing
# else that hits the network at runtime.
EXTERNAL_URL_RE = re.compile(
    r"""
    (?P<url>
      https?:// [^\s"'<>]+                        # explicit http(s) URLs
    | // [A-Za-z0-9._-]+ \. [^\s"'<>]+             # protocol-relative CDN URLs
    | ["'`](?: src | href ) \s*=\s* ["'`]
        (?: https?: | // )                        # src/href to an external host
        [^"'`\s]+
    )
    """,
    re.VERBOSE,
)

ALLOWED_EXTERNAL_FRAGMENTS = (
    # SVG namespace — used purely for inline SVG construction.
    "www.w3.org/2000/svg",
)

# Forbidden runtime patterns. We don't allow ``eval`` or ``new Function``
# anywhere in the JS layer.
FORBIDDEN_JS_PATTERNS = (
    re.compile(r"\beval\s*\("),
    re.compile(r"\bnew\s+Function\s*\("),
)

# DOM ids referenced from JS via ``document.querySelector('#...')``.
QUERY_SELECTOR_RE = re.compile(r"document\.querySelector\(\s*['\"]#([A-Za-z][\w-]*)['\"]")
GET_ELEMENT_BY_ID_RE = re.compile(r"document\.getElementById\(\s*['\"]([A-Za-z][\w-]*)['\"]")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_asset_exists() -> None:
    for p in ASSET_PATHS:
        assert p.exists(), f"Required static asset missing: {p}"
        assert p.stat().st_size > 0, f"Static asset is empty: {p}"


def _strip_external_allowed(text: str) -> str:
    """Remove allowed substrings (e.g. SVG namespace) so the test does not flag them."""
    cleaned = text
    for frag in ALLOWED_EXTERNAL_FRAGMENTS:
        cleaned = cleaned.replace(frag, "")
    return cleaned


# ---------------------------------------------------------------------------


def test_required_assets_present() -> None:
    _assert_asset_exists()


@pytest.mark.parametrize("asset", [INDEX_HTML, APP_JS, CHARTS_JS, STYLE_CSS])
def test_no_external_urls(asset: Path) -> None:
    text = _strip_external_allowed(_read(asset))
    bad = []
    for m in EXTERNAL_URL_RE.finditer(text):
        bad.append(m.group("url"))
    assert not bad, f"External URLs found in {asset.name}: {bad}"


@pytest.mark.parametrize("asset", [APP_JS, CHARTS_JS])
def test_no_forbidden_js_patterns(asset: Path) -> None:
    text = _read(asset)
    offenders = []
    for pat in FORBIDDEN_JS_PATTERNS:
        for m in pat.finditer(text):
            offenders.append((asset.name, pat.pattern, m.group(0)))
    assert not offenders, f"Forbidden runtime patterns: {offenders}"


def test_ids_consistency() -> None:
    html = _read(INDEX_HTML)
    js_text = _read(APP_JS)

    referenced = set(QUERY_SELECTOR_RE.findall(js_text))
    referenced |= set(GET_ELEMENT_BY_ID_RE.findall(js_text))

    declared = set(re.findall(r'id="([A-Za-z][\w-]*)"', html))

    missing = referenced - declared
    assert not missing, f"JS references ids missing from HTML: {sorted(missing)}"


def test_html_references_local_assets() -> None:
    html = _read(INDEX_HTML)
    href_src = re.findall(
        r"(?P<attr>href|src)\s*=\s*['\"](?P<val>[^'\"]+)['\"]",
        html,
    )

    candidates = []
    for attr, val in href_src:
        # ignore external / anchor / mailto / data: URIs
        if val.startswith(("http://", "https://", "//", "mailto:", "data:", "#")):
            continue
        candidates.append((attr, val))

    assert candidates, "No local asset references in index.html"

    missing = []
    for attr, val in candidates:
        rel = val.lstrip("/")
        # Strip any ``static/`` prefix so we resolve against ``app/static``.
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        target = STATIC_DIR / rel
        if not target.exists():
            missing.append((attr, val, str(target)))

    assert not missing, f"HTML references missing local assets: {missing}"


def test_app_js_loads_after_charts() -> None:
    """The HTML must include ``charts.js`` before ``app.js`` so ``ChartUtils``
    is defined when ``app.js`` runs.
    """
    html = _read(INDEX_HTML)
    charts_pos = html.find("/static/charts.js")
    app_pos = html.find("/static/app.js")
    assert charts_pos >= 0, "charts.js script tag not found"
    assert app_pos >= 0, "app.js script tag not found"
    assert charts_pos < app_pos, "charts.js must be loaded before app.js"


def test_app_js_uses_dashboard_endpoints() -> None:
    """The front-end must talk to the contract endpoints listed in the plan."""
    text = _read(APP_JS)
    for needle in (
        "/api/analytics",
        "/api/status",
        "/api/refresh",
        "/api/history/",
    ):
        assert needle in text, f"Expected reference to {needle} in app.js"


def test_app_js_handles_missing_fields() -> None:
    """Defensive rendering: every numeric formatter must fall back to ``—``."""
    text = _read(APP_JS)
    # All formatters are used repeatedly; assert at least the patterns exist.
    for fn in ("fmtNum", "fmtPercent", "fmtDuration", "fmtTime"):
        assert f"function {fn}" in text, f"Defensive formatter {fn} missing"
    # ``fmtNum`` must return ``—`` on non-numeric input (the most relied on one).
    assert "'—'" in text or '"—"' in text, "Dashboard does not use the em-dash placeholder"


def test_chart_utils_exposed() -> None:
    """charts.js must publish a stable global used by app.js."""
    text = _read(CHARTS_JS)
    assert "window.ChartUtils" in text, "charts.js must expose window.ChartUtils"
    for fn in ("duration", "formatTime", "renderHistoricalChart"):
        assert f"{fn}:" in text or f"{fn} :" in text, f"ChartUtils.{fn} missing"

"""Behavioral regression test for the history chart balance mode.

Bug: balance/credit windows store the money in ``remaining`` while
``used_percent``/``used`` are always null (see ``app/normalize.py::
_balance_row``). The chart renderer only accepted ``used_percent``, so every
balance point was filtered out and the panel showed "нет данных за выбранный
диапазон" even with thousands of points (e.g. codex · balance · 24h with
1111 points).

The test executes ``app/static/charts.js`` in Node (if available) with a
minimal DOM stub and asserts that a codex-shaped balance payload renders a
series line instead of the empty state, and that percent mode still works.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHARTS_JS = ROOT / "app" / "static" / "charts.js"

NODEHarness = """
function makeNode(name, attrs) {
  return {
    name, attrs: attrs || {}, children: [], textContent: '',
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
    get firstChild() { return this.children[0] || null; },
    setAttribute(k, v) { this.attrs[k] = v; },
  };
}
global.document = { createElementNS: (_ns, name) => makeNode(name) };
global.window = {};
require(process.argv[2]);
const CU = global.window.ChartUtils;
function flatten(n, acc = []) { acc.push(n); for (const c of n.children) flatten(c, acc); return acc; }
function render(mode, points) {
  const c = makeNode('div'); c.clientWidth = 720; c.clientHeight = 280;
  CU.renderHistoricalChart(c, { mode: mode, points: points, burns: {}, etaIso: null });
  const flat = flatten(c);
  return {
    empty: flat.filter(n => n.name === 'text' && /нет данных/.test(n.textContent)).length,
    lines: flat.filter(n => n.name === 'path' && n.attrs.class === 'series-line').length,
    bars: flat.filter(n => n.name === 'rect' && n.attrs.class === 'spend-bar').length,
  };
}
// Codex-shaped balance rows: money in remaining, used/used_percent always null.
const points = [];
const t0 = Date.parse('2026-08-26T06:32:20Z');
for (let i = 0; i < 1111; i++) {
  points.push({
    collected_at: new Date(t0 + i * 61000).toISOString(),
    used: null, remaining: 0.0, limit_value: null,
    used_percent: null, unit: 'USD', reset_at: null,
  });
}
const balance = render('balance', points);
const spend = render('spend', points.slice(0, 2880)); // two local days of rows
const percent = CU.buildSeries([
  { collected_at: '2026-08-27T00:00:00Z', used_percent: 10 },
  { collected_at: '2026-08-27T01:00:00Z', used_percent: 40 },
], 'percent');
const out = {
  balance_empty: balance.empty, balance_lines: balance.lines,
  spend_empty: spend.empty, spend_bars: spend.bars,
  percent_count: percent.length,
};
console.log('JSON:' + JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_balance_mode_renders_codex_shaped_points(tmp_path) -> None:
    assert CHARTS_JS.exists(), f"missing {CHARTS_JS}"
    harness = tmp_path / "chart_harness.js"
    harness.write_text(NODEHarness, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(CHARTS_JS)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    line = next(l for l in proc.stdout.splitlines() if l.startswith("JSON:"))
    out = eval(line[len("JSON:"):])  # noqa: S307 — trusted literal from this harness

    # The core regression: balance mode must NOT collapse to the empty state.
    assert out["balance_empty"] == 0, (
        "balance mode rendered the empty state for codex-shaped points "
        f"(lines={out['balance_lines']})"
    )
    assert out["balance_lines"] >= 1, "balance mode drew no series line"
    # Daily-spend fallback: remaining deltas must also produce bars.
    assert out["spend_empty"] == 0, "spend mode rendered the empty state for balance rows"
    assert out["spend_bars"] >= 1, "spend mode drew no bars"
    # Percent mode (usage windows) must be unchanged.
    assert out["percent_count"] == 2

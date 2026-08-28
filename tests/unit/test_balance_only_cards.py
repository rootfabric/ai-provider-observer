"""Balance-only operator cards (OpenRouter, DeepSeek).

Bug: balance/credits windows are hidden on collapsed cards
(``style.css``: ``.card:not(.expanded) .window.is-balance``). Providers
that own nothing but balance windows — pay-as-you-go operators like
OpenRouter and DeepSeek, which have no 5h/weekly packages — collapsed to
a bare "BOTTLENECK Баланс · 15" row where the risk score read like a
balance of 15 and the actual money was invisible.

Fix contract exercised here:
1. ``renderProviderCard`` must tag cards whose windows are all
   balance/credits-shaped with an ``only-balance`` class (derived from
   the payload, never from hardcoded provider ids, so demo/live/new
   pay-as-you-go providers behave identically).
2. ``style.css`` must override the collapsed hide rule for that class so
   the balance block stays visible without expanding.
3. The bottleneck row must label the risk score explicitly
   ("Баланс · score 15"), never render it as a bare number that reads
   like an amount.

The behavioral part executes ``app/static/app.js`` in Node (if
available) with a minimal DOM stub, like ``test_chart_balance_render``.
"""
from __future__ import annotations

import json
import shutil
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "app" / "static" / "app.js"
STYLE_CSS = ROOT / "app" / "static" / "style.css"

NodeHarness = """
function elStub() {
  return {
    innerHTML: '', textContent: '', hidden: false, value: '', disabled: false,
    addEventListener() {}, classList: { toggle() { return false; } },
  };
}
global.document = { querySelector: () => elStub() };
global.window = {
  ChartUtils: {
    duration: s => Math.round((s || 0) / 60) + 'm',
    formatTime: () => '12:00',
    renderHistoricalChart() {},
  },
  addEventListener() {},
};
global.fetch = async () => ({ ok: true, json: async () => ({}) });
global.setInterval = () => 0; // keep the Node process from hanging
// app.js is a plain script (no exports): run it in this context so its
// top-level function declarations become reachable as globals.
const fs = require('fs'), vm = require('vm');
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'), { filename: process.argv[2] });

const balWin = {
  window_type: 'balance',
  latest: { remaining: 36.12, used: null, used_percent: null, limit: null, unit: 'USD' },
  burns: {}, forecast: {}, runway: { runway_days: 656.6, usd_per_day: 0.05 },
};
const quotaWin = {
  window_type: 'five_hour',
  latest: { used_percent: 67, remaining: 656, limit: 2000, unit: 'credits' },
  burns: { '10m': { value: 29.75 }, '15m': { value: 30.1 }, '1h': { value: 573.68 } },
  forecast: {}, burn_acceleration: { band: 'accelerating' },
};

function render(windows, bottleneck) {
  return renderProviderCard({
    provider: 'openrouter', label: 'OpenRouter', status: 'ok',
    risk: { level: 'healthy', score: 15, bottleneck: bottleneck || 'balance' },
    windows, recommendation: {},
  });
}

// Pay-as-you-go operator: only a balance window -> money must be on the card.
const openrouter = render({ balance: balWin });
// Multi-currency suffix keys (e.g. DeepSeek "balance:CNY") are balance-like too.
const deepseek = render({ balance: balWin, 'balance:CNY': balWin });
// credits-only payload behaves the same.
const creditsOnly = render({ credits: balWin });
// Package operator (5h/weekly + balance) must NOT be tagged: its balance
// stays expand-only by design.
const packaged = render({ five_hour: quotaWin, weekly: quotaWin, balance: balWin });
// No windows at all -> plain collapsed card, no tag.
const empty = render({});

const bareScoreLeak = /Баланс · 15</.test(openrouter);
const out = {
  openrouter: {
    tagged: openrouter.includes('card collapsed only-balance'),
    money: /bal-amount/.test(openrouter) && /36[.,]12/.test(openrouter),
    runway: openrouter.includes('Runway:'),
    emptyState: openrouter.includes('Нет числовых метрик'),
    scoreLabeled: openrouter.includes('Баланс · score 15'),
    bareScoreLeak,
  },
  deepseek: { tagged: deepseek.includes('only-balance') },
  creditsOnly: { tagged: creditsOnly.includes('only-balance') },
  packaged: { tagged: packaged.includes('only-balance') },
  empty: { tagged: empty.includes('only-balance') },
};
console.log('JSON:' + JSON.stringify(out));
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- static ---


def test_css_keeps_balance_visible_on_collapsed_balance_only_cards() -> None:
    css = _read(STYLE_CSS)
    assert ".card:not(.expanded) .window.is-balance { display: none; }" in css, (
        "collapsed hide rule for balance windows must stay in place"
    )
    override = (
        ".card.only-balance:not(.expanded) .window.is-balance { display: block; }"
    )
    assert override in css, (
        "balance-only cards need an explicit collapsed-state override"
    )


def test_bottleneck_row_labels_the_risk_score() -> None:
    js = _read(APP_JS)
    m = re.search(r"function renderBottleneckRow\([^)]*\)\s*\{(.*?)\n\}", js, re.S)
    assert m, "renderBottleneckRow missing"
    body = m.group(1)
    assert "' · score '" in body, (
        "bottleneck row must prefix the risk score with 'score' so a "
        "balance bottleneck never reads like an amount"
    )


# ----------------------------------------------------------- behavioral ---


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_balance_only_cards_render_money_and_labeled_score(tmp_path) -> None:
    assert APP_JS.exists(), f"missing {APP_JS}"
    harness = tmp_path / "card_harness.js"
    harness.write_text(NodeHarness, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(APP_JS)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    line = next(l for l in proc.stdout.splitlines() if l.startswith("JSON:"))
    out = json.loads(line[len("JSON:"):])

    orr = out["openrouter"]
    assert orr["tagged"], "balance-only card must carry the only-balance class"
    assert orr["money"], "balance-only collapsed card must show the money amount"
    assert orr["runway"], "balance-only collapsed card must show the runway"
    assert not orr["emptyState"], (
        "balance-only card must not degrade to 'Нет числовых метрик'"
    )
    assert orr["scoreLabeled"], "bottleneck score must be labeled ('score 15')"
    assert not orr["bareScoreLeak"], (
        "'Баланс · 15' must not leak — it reads like a balance of 15"
    )

    assert out["deepseek"]["tagged"], "suffixed balance keys stay balance-only"
    assert out["creditsOnly"]["tagged"], "credits-only providers are balance-only too"
    assert not out["packaged"]["tagged"], (
        "providers with package windows keep the expand-only balance"
    )
    assert not out["empty"]["tagged"], "window-less cards must not be tagged"

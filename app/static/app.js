/* AI Provider Observer — dashboard front-end.
 *
 * Loads /api/analytics for the main dashboard, /api/status for the DEMO/live
 * pill (or `live` when analytics is enabled), and /api/history/.../... for the
 * chart. All rendering is defensive — every missing/unknown field renders as
 * `—` and never as a zero.
 */

const $ = sel => document.querySelector(sel);
const summary = $('#summary');
const cards = $('#cards');
const mode = $('#mode');
const generated = $('#generated');
const refresh = $('#refresh');
const bottlenecksBody = $('#bottlenecks-body');
const chartProvider = $('#chart-provider');
const chartWindow = $('#chart-window');
const chartRange = $('#chart-range');
const chartModeLabel = $('#chart-mode-label');
const chartMode = $('#chart-mode');
const chartCanvas = $('#chart-canvas');
const chartMeta = $('#chart-meta');
const chartError = $('#chart-error');

const POLL_MS = 30000;
const PROFILE_LABELS = {
  five_hour: '5-часовое',
  daily: 'Дневное',
  weekly: 'Недельное',
  monthly: 'Месячное',
  balance: 'Баланс',
  credits: 'Кредиты',
  unknown: 'Неизвестно',
};

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}[c]));

function fmtNum(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(v);
}

function fmtPercent(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  return fmtNum(v) + '%';
}

function fmtDuration(seconds) {
  return window.ChartUtils.duration(seconds);
}

function fmtTime(iso) {
  return window.ChartUtils.formatTime(iso);
}

function fmtConfidence(c) {
  if (!c) return '—';
  const v = String(c).toLowerCase();
  if (v === 'high') return 'high';
  if (v === 'medium') return 'medium';
  if (v === 'low') return 'low';
  return v;
}

function resetLabel(window) {
  if (!window) return '—';
  const mode = window.forecast && window.forecast.recovery_mode;
  if (mode === 'rolling') return 'rolling';
  if (mode === 'estimated_reset' || mode === 'unknown') {
    return window.latest && window.latest.reset_at
      ? fmtTime(window.latest.reset_at) + ' (estimated)'
      : 'estimated';
  }
  if (window.latest && window.latest.reset_at) return fmtTime(window.latest.reset_at);
  return '—';
}

function fmtMargin(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—';
  const sign = seconds >= 0 ? '+' : '−';
  return sign + fmtDuration(Math.abs(seconds));
}

function statusText(s) {
  return ({ ok: 'OK', partial: 'PARTIAL', error: 'ERROR', disabled: 'OFF' })[s] || (s || '—');
}

function levelClass(level) {
  const v = String(level || '').toLowerCase();
  if (v === 'healthy') return 'lvl-healthy';
  if (v === 'watch') return 'lvl-watch';
  if (v === 'warning') return 'lvl-warning';
  if (v === 'high') return 'lvl-high';
  if (v === 'critical') return 'lvl-critical';
  return 'lvl-neutral';
}

function levelChip(level, score) {
  if (!level) return '<span class="chip lvl-neutral">—</span>';
  const txt = esc(level.toUpperCase()) + (typeof score === 'number' ? ' · ' + Math.round(score) : '');
  return `<span class="chip ${levelClass(level)}">${txt}</span>`;
}

function fillClass(pct) {
  if (typeof pct !== 'number') return '';
  if (pct >= 95) return 'bad';
  if (pct >= 85) return 'critical';
  if (pct >= 70) return 'warn';
  return '';
}

function burnArrow(band) {
  const v = String(band || '').toLowerCase();
  if (v === 'accelerating') return '↑';
  if (v === 'decelerating') return '↓';
  if (v === 'steady' || v === 'stable') return '→';
  if (v === 'insufficient_data' || v === 'unknown') return '?';
  return '·';
}

/* --------------------------- summary panel ---------------------------- */

function renderSummary(data) {
  const sum = data.summary || {};
  const counts = [
    { key: 'healthy', label: 'healthy', n: sum.providers_healthy, cls: 'lvl-healthy' },
    { key: 'watch', label: 'watch', n: sum.providers_watch, cls: 'lvl-watch' },
    { key: 'warning', label: 'warning', n: sum.providers_warning, cls: 'lvl-warning' },
    { key: 'critical', label: 'critical', n: sum.providers_critical, cls: 'lvl-critical' },
  ];

  function etaFromSeconds(secs) {
    if (typeof secs !== 'number' || !Number.isFinite(secs) || secs <= 0) return '—';
    return fmtDuration(secs);
  }

  function overspendLabel(o) {
    if (!o) return '—';
    const pct = typeof o.projected_percent === 'number' ? Math.round(o.projected_percent) + '%' : '—';
    return esc(o.provider || '—') + ' · ' + esc(pct);
  }

  function exhaustionLabel(e) {
    if (!e) return '—';
    const eta = etaFromSeconds(e.eta_seconds);
    const win = esc(PROFILE_LABELS[e.window_type] || e.window_type || '—');
    return esc(e.provider || '—') + ' · ' + win + ' · ETA ' + eta;
  }

  function bottleneckLabel(b) {
    if (!b) return '—';
    return esc(b.provider || '—') + ' · ' + esc(PROFILE_LABELS[b.bottleneck] || b.bottleneck || '—') +
      (typeof b.score === 'number' ? ' · score ' + Math.round(b.score) : '');
  }

  function runwayLabel(r) {
    if (!r) return '—';
    const days = typeof r.runway_days === 'number'
      ? (Math.round(r.runway_days * 10) / 10) + ' дн.'
      : '—';
    return esc(r.provider || '—') + ' · ' + days;
  }

  const counters = counts.map(c => {
    const n = typeof c.n === 'number' ? c.n : 0;
    return `<div class="stat ${c.cls}"><span class="stat-n">${n}</span><span class="stat-label">${esc(c.label)}</span></div>`;
  }).join('');

  const lines = `
    <div class="summary-row"><span class="lbl">Most constrained</span><span>${bottleneckLabel(sum.most_constrained)}</span></div>
    <div class="summary-row"><span class="lbl">First expected exhaustion</span><span>${exhaustionLabel(sum.first_expected_exhaustion)}</span></div>
    <div class="summary-row"><span class="lbl">Highest weekly overspend</span><span>${overspendLabel(sum.highest_weekly_overspend)}</span></div>
    <div class="summary-row"><span class="lbl">Lowest runway</span><span>${runwayLabel(sum.lowest_runway)}</span></div>
  `;

  summary.innerHTML = `<div class="summary-counters">${counters}</div>${lines}`;
}

/* -------------------------- per-window block --------------------------- */

function renderWindow(window, typeLabel) {
  if (!window) return '';
  const latest = window.latest || {};
  const used = typeof latest.used_percent === 'number' ? latest.used_percent : null;
  const cls = fillClass(used || 0);
  const remaining = latest.remaining;
  const limit = latest.limit;
  const usedAbs = latest.used;

  const burns = window.burns || {};
  const burn15 = burns['15m'] && burns['15m'].value;
  const burn1h = burns['1h'] && burns['1h'].value;
  const accel = window.burn_acceleration || {};
  const arrow = burnArrow(accel.band);

  const forecast = window.forecast || {};
  const eta = forecast.eta_current_seconds;
  const confidence = forecast.confidence;
  const margin = forecast.survival_margin_seconds;

  const resetText = resetLabel(window);

  const pace = window.pacing;
  const weeklyExtra = (window.window_type === 'weekly' && pace)
    ? `
      <div class="kv-row">
        <span class="kv-key">Неделя</span>
        <span class="kv-val">elapsed ${fmtPercent(pace.elapsed_percent)} · expected ${fmtPercent(pace.expected_usage_by_now)}</span>
      </div>
      <div class="kv-row">
        <span class="kv-key">Pace</span>
        <span class="kv-val">
          <span class="chip ${levelClass(pace.pace_band)}">${esc(pace.pace_band || '—')} ${typeof pace.pace_ratio === 'number' ? fmtNum(pace.pace_ratio) + 'x' : ''}</span>
        </span>
      </div>
      <div class="kv-row">
        <span class="kv-key">Projected</span>
        <span class="kv-val">
          ${fmtPercent(pace.projected_whole_window)}
          <span class="muted small"> 24h ${fmtPercent(pace.projected_pace_24h)} · 3d ${fmtPercent(pace.projected_pace_3d)}</span>
        </span>
      </div>
    `
    : '';

  const isBalanceLike = ['balance', 'credits'].includes(window.window_type);
  if (isBalanceLike) {
    const runway = window.runway;
    return `
      <div class="window">
        <div class="windowhead">
          <span class="windowname">${esc(typeLabel)}${window.window_label ? ' · ' + esc(window.window_label) : ''}</span>
          <span class="windowvalue">${typeof usedAbs === 'number' ? fmtNum(usedAbs) + ' ' + esc(latest.unit || '') : '—'}</span>
        </div>
        <div class="bal-big">
          <div class="bal-amount">${typeof usedAbs === 'number' ? fmtNum(usedAbs) : '—'} <span class="muted small">${esc(latest.unit || '')}</span></div>
          <div class="bal-aside">
            <div>USD/day: ${runway && typeof runway.spend_per_day_usd === 'number' ? fmtNum(runway.spend_per_day_usd) : '—'}</div>
            <div>Runway: ${runway && typeof runway.runway_days === 'number'
              ? (Math.round(runway.runway_days * 10) / 10) + ' дн.'
              : '—'}</div>
            <div>Monthly spend: ${runway && typeof runway.projected_monthly_spend === 'number'
              ? fmtNum(runway.projected_monthly_spend)
              : '—'}</div>
          </div>
        </div>
        <div class="sub"><span>bottleneck: ${esc(window.window_type)}</span><span>—</span></div>
      </div>
    `;
  }

  return `
    <div class="window">
      <div class="windowhead">
        <span class="windowname">${esc(typeLabel)}${window.window_label ? ' · ' + esc(window.window_label) : ''}</span>
        <span class="windowvalue">${used == null ? '—' : fmtPercent(used) + ' used'}</span>
      </div>
      ${used == null ? '' : `<div class="bar"><div class="fill ${cls}" style="width:${Math.max(0, Math.min(100, used))}%"></div></div>`}
      <div class="kv">
        <div class="kv-row">
          <span class="kv-key">Remaining</span>
          <span class="kv-val">${typeof remaining === 'number' ? fmtNum(remaining) + ' ' + esc(latest.unit || '') : '—'}</span>
        </div>
        <div class="kv-row">
          <span class="kv-key">Limit</span>
          <span class="kv-val">${typeof limit === 'number' ? fmtNum(limit) + ' ' + esc(latest.unit || '') : '—'}</span>
        </div>
        <div class="kv-row">
          <span class="kv-key">Burn 15m / 1h</span>
          <span class="kv-val">
            ${typeof burn15 === 'number' ? fmtNum(burn15) : '—'} / ${typeof burn1h === 'number' ? fmtNum(burn1h) : '—'} ед/ч
            <span class="accel">${arrow} ${esc(accel.band || '')}</span>
          </span>
        </div>
        <div class="kv-row">
          <span class="kv-key">Exhaustion</span>
          <span class="kv-val">
            ${typeof eta === 'number' && eta > 0 ? fmtDuration(eta) : '—'}
            <span class="muted small">confidence: ${fmtConfidence(confidence)}</span>
          </span>
        </div>
        <div class="kv-row">
          <span class="kv-key">Reset</span>
          <span class="kv-val">${esc(resetText)}</span>
        </div>
        <div class="kv-row">
          <span class="kv-key">Margin</span>
          <span class="kv-val ${typeof margin === 'number' && margin < 0 ? 'margin-bad' : ''}">
            ${typeof margin === 'number' ? fmtMargin(margin) : '—'}
            ${typeof margin === 'number' && margin < 0 ? ' ⚠' : ''}
          </span>
        </div>
        ${weeklyExtra}
      </div>
    </div>
  `;
}

function renderProviderCard(p) {
  const risk = p.risk || {};
  const recommendation = p.recommendation || {};
  const windows = p.windows || {};
  const windowHtml = Object.keys(windows)
    .map(k => renderWindow(windows[k], PROFILE_LABELS[k] || k))
    .join('');

  const reasonLines = Array.isArray(recommendation.reason_lines) ? recommendation.reason_lines : [];
  const firstReasons = reasonLines.slice(0, 2).map(r => `<li>${esc(r)}</li>`).join('');

  const recParts = [];
  recParts.push(`<div class="rec-action">${esc(recommendation.title || recommendation.action || '—')}</div>`);
  if (firstReasons) recParts.push(`<ul class="rec-reasons">${firstReasons}</ul>`);
  if (typeof recommendation.recommended_capacity_ratio === 'number') {
    recParts.push(`<div class="rec-cap">Recommended capacity ≈ ${fmtNum(recommendation.recommended_capacity_ratio)}x</div>`);
  }

  const errorBlock = p.error ? `<div class="error">${esc(p.error)}</div>` : '';
  const statusCls = esc(p.status || 'ok');

  return `
    <article class="card">
      <div class="cardtop">
        <div>
          <div class="provider">${esc(p.label || p.provider || '—')}</div>
          <div class="plan">${esc(p.plan || '')}</div>
        </div>
        <div class="card-badges">
          ${levelChip(risk.level, risk.score)}
          <span class="status ${statusCls}">${esc(statusText(p.status))}</span>
        </div>
      </div>
      ${renderBottleneckRow(risk.bottleneck, risk.score)}
      ${windowHtml || '<div class="empty">Нет числовых метрик</div>'}
      ${recParts.length ? `<div class="recommendation">${recParts.join('')}</div>` : ''}
      ${errorBlock}
    </article>
  `;
}

function renderBottleneckRow(bottleneck, score) {
  if (!bottleneck) return '';
  const label = PROFILE_LABELS[bottleneck] || bottleneck;
  return `<div class="bottleneck-row"><span class="lbl">BOTTLENECK</span><span>${esc(label)}${typeof score === 'number' ? ' · ' + Math.round(score) : ''}</span></div>`;
}

/* ------------------------- bottlenecks table --------------------------- */

function renderBottlenecks(providers) {
  if (!Array.isArray(providers) || !providers.length) {
    bottlenecksBody.innerHTML = '<tr><td colspan="6" class="empty">—</td></tr>';
    return;
  }
  const rows = providers.map(p => {
    const risk = p.risk || {};
    const win = (p.windows || {})[risk.bottleneck];
    const fc = (win && win.forecast) || {};
    const margin = fc.survival_margin_seconds;
    return `
      <tr>
        <td><span class="status ${esc(p.status || 'ok')}">${esc(statusText(p.status))}</span> ${esc(p.label || p.provider || '—')}</td>
        <td>${esc(PROFILE_LABELS[risk.bottleneck] || risk.bottleneck || '—')}</td>
        <td>${typeof fc.eta_current_seconds === 'number' && fc.eta_current_seconds > 0 ? fmtDuration(fc.eta_current_seconds) : '—'}</td>
        <td>${resetLabel(win)}</td>
        <td class="${typeof margin === 'number' && margin < 0 ? 'margin-bad' : ''}">${typeof margin === 'number' ? fmtMargin(margin) : '—'}</td>
        <td>${levelChip(risk.level, risk.score)}</td>
      </tr>
    `;
  }).join('');
  bottlenecksBody.innerHTML = rows;
}

/* ------------------------------- chart -------------------------------- */

function populateChartSelectors(providers) {
  const list = (providers || []).filter(p => Object.keys(p.windows || {}).length > 0);
  chartProvider.innerHTML = list.map(p => `<option value="${esc(p.provider)}">${esc(p.label || p.provider)}</option>`).join('');
  updateWindowSelector();
}

function updateWindowSelector() {
  const pid = chartProvider.value;
  const data = window.__LAST_ANALYTICS__ || {};
  const p = (data.providers || []).find(x => x.provider === pid);
  const wins = p ? Object.keys(p.windows || {}) : [];
  chartWindow.innerHTML = wins.map(w => `<option value="${esc(w)}">${esc(PROFILE_LABELS[w] || w)}</option>`).join('');
  // Toggle balance-mode switcher.
  const cur = chartWindow.value;
  chartModeLabel.hidden = !(cur === 'balance' || cur === 'credits');
  if (chartModeLabel.hidden) {
    chartMode.value = 'balance';
  }
}

async function loadChart() {
  chartError.hidden = true;
  chartError.textContent = '';
  const pid = chartProvider.value;
  const wtype = chartWindow.value;
  const hours = parseInt(chartRange.value, 10);
  if (!pid || !wtype) {
    chartCanvas.innerHTML = '<div class="empty">Нет данных для графика</div>';
    chartMeta.textContent = '';
    return;
  }
  try {
    const url = `/api/history/${encodeURIComponent(pid)}/${encodeURIComponent(wtype)}?hours=${hours}`;
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    const points = Array.isArray(data.points) ? data.points : [];

    let etaIso = null;
    const analytics = window.__LAST_ANALYTICS__ || {};
    const provider = (analytics.providers || []).find(p => p.provider === pid);
    const win = provider && provider.windows ? provider.windows[wtype] : null;
    const fc = win && win.forecast;
    if (fc && typeof fc.eta_current_seconds === 'number' && fc.eta_current_seconds > 0) {
      etaIso = new Date(Date.now() + fc.eta_current_seconds * 1000).toISOString();
    }

    let burns = {};
    if (win && win.burns) {
      burns = {
        '15m': win.burns['15m'] && win.burns['15m'].value,
        '1h': win.burns['1h'] && win.burns['1h'].value,
      };
    }

    const isBalanceLike = (wtype === 'balance' || wtype === 'credits');
    const mode = isBalanceLike ? chartMode.value : 'percent';

    window.ChartUtils.renderHistoricalChart(chartCanvas, {
      mode: mode,
      points: points,
      burns: burns,
      etaIso: etaIso,
    });

    const span = points.length
      ? `${points.length} точек`
      : '—';
    chartMeta.innerHTML =
      `<span>${esc(pid)} · ${esc(wtype)} · ${hours}h</span>` +
      `<span>${esc(span)}</span>` +
      (etaIso ? `<span>ETA: ${esc(fmtTime(etaIso))}</span>` : '');
  } catch (e) {
    chartError.hidden = false;
    chartError.textContent = 'График: ' + (e && e.message ? e.message : String(e));
    chartCanvas.innerHTML = '';
    chartMeta.innerHTML = '';
  }
}

chartProvider.addEventListener('change', () => {
  updateWindowSelector();
  loadChart();
});
chartWindow.addEventListener('change', loadChart);
chartRange.addEventListener('change', loadChart);
chartMode.addEventListener('change', () => {
  updateWindowSelector();
  loadChart();
});
window.addEventListener('resize', () => {
  if (chartCanvas.firstChild) loadChart();
});

/* -------------------------- main data load ----------------------------- */

async function fetchJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
  return r.json();
}

async function load() {
  let analytics = null;
  let status = null;
  try {
    [analytics, status] = await Promise.all([
      fetchJSON('/api/analytics').catch(() => null),
      fetchJSON('/api/status').catch(() => null),
    ]);
  } catch (e) {
    cards.innerHTML = `<div class="error">Не удалось загрузить данные: ${esc(e && e.message ? e.message : e)}</div>`;
    return;
  }

  // pill: prefer analytics.generated_at; pill text from /api/status when
  // analytics is unavailable.
  if (status && status.demo_mode) {
    mode.textContent = 'DEMO MODE';
  } else if (analytics) {
    mode.textContent = 'live';
  } else {
    mode.textContent = 'offline';
  }
  generated.textContent = analytics && analytics.generated_at ? 'срез: ' + fmtTime(analytics.generated_at) : '';

  if (!analytics || !analytics.analytics_enabled) {
    cards.innerHTML = '<div class="empty">Аналитика недоступна. Проверьте ANALYTICS_ENABLED или посмотрите /api/status.</div>';
    summary.innerHTML = '';
    bottlenecksBody.innerHTML = '<tr><td colspan="6" class="empty">—</td></tr>';
    chartProvider.innerHTML = '';
    return;
  }

  window.__LAST_ANALYTICS__ = analytics;
  const providers = analytics.providers || [];
  renderSummary(analytics);
  cards.innerHTML = providers.length
    ? providers.map(renderProviderCard).join('')
    : '<div class="empty">Провайдеры не настроены.</div>';
  renderBottlenecks(providers);

  // refresh chart selectors if the list of providers changed
  const opts = Array.from(chartProvider.options).map(o => o.value);
  const incoming = providers.map(p => p.provider);
  if (opts.join('|') !== incoming.join('|')) {
    populateChartSelectors(providers);
  } else {
    updateWindowSelector();
  }
  // Keep selected provider when still available.
  if (!chartProvider.value && providers[0]) chartProvider.value = providers[0].provider;
  if (!chartWindow.value) {
    const firstP = providers.find(p => p.provider === chartProvider.value) || providers[0];
    if (firstP) {
      const keys = Object.keys(firstP.windows || {});
      if (keys.length) chartWindow.value = keys[0];
    }
  }
  updateWindowSelector();
  await loadChart();
}

refresh.addEventListener('click', async () => {
  refresh.disabled = true;
  const oldText = refresh.textContent;
  refresh.textContent = 'Проверяю…';
  try {
    await fetch('/api/refresh', { method: 'POST' });
    await load();
  } finally {
    refresh.disabled = false;
    refresh.textContent = oldText;
  }
});

load().catch(e => {
  cards.innerHTML = `<div class="error">${esc(e && e.message ? e.message : e)}</div>`;
});
setInterval(() => load().catch(() => {}), POLL_MS);

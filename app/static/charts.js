/**
 * charts.js — pure rendering functions for the AI Provider Observer dashboard.
 *
 * All inputs are plain JS values/strings. The functions never throw on missing
 * data; missing fields produce neutral renderings (—, no marker). Times are
 * handled as ISO strings; callers format for display via toLocaleString().
 *
 * No external dependencies, no framework — vanilla DOM SVG construction.
 */
(function (global) {
  'use strict';

  /* ----------------------------- formatting ----------------------------- */

  function duration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—';
    const sign = seconds < 0 ? '-' : '';
    const abs = Math.abs(seconds);
    if (abs < 90) {
      // show in minutes (e.g. 12 мин)
      return sign + Math.round(abs / 60) + ' мин';
    }
    if (abs < 48 * 3600) {
      const h = Math.floor(abs / 3600);
      const m = Math.round((abs % 3600) / 60);
      // 2-значные минуты: 2ч 09м
      return sign + h + 'ч ' + String(m).padStart(2, '0') + 'м';
    }
    const days = abs / 86400;
    return sign + (Math.round(days * 10) / 10) + 'д';
  }

  function formatTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString();
  }

  function formatTimeShort(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function escStr(s) {
    return String(s == null ? '' : s);
  }

  /* -------------------------- chart utilities --------------------------- */

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', name);
    if (attrs) {
      for (const k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] !== undefined && attrs[k] !== null) {
          el.setAttribute(k, attrs[k]);
        }
      }
    }
    return el;
  }

  /**
   * Reduce history points to (time, used_percent). Defensive against
   * missing/garbled entries: only returns points with a parseable
   * collected_at and a numeric used_percent.
   */
  function buildSeries(points) {
    const out = [];
    if (!Array.isArray(points)) return out;
    for (const p of points) {
      if (!p) continue;
      const t = new Date(p.collected_at);
      const v = typeof p.used_percent === 'number' ? p.used_percent : null;
      if (Number.isNaN(t.getTime()) || v === null) continue;
      out.push({ time: t, value: v, reset_at: p.reset_at || null });
    }
    out.sort((a, b) => a.time - b.time);
    return out;
  }

  function xRange(series) {
    if (!series.length) {
      const now = new Date();
      return { min: now.getTime() - 3600 * 1000, max: now.getTime() };
    }
    let min = series[0].time.getTime();
    let max = series[series.length - 1].time.getTime();
    // Pad by 3% on each side so points aren't at the chart edge.
    const span = Math.max(60_000, max - min);
    return {
      min: min - Math.round(span * 0.03),
      max: max + Math.round(span * 0.03),
    };
  }

  function buildDailySpend(points) {
    // Aggregate points into local-time-day buckets; return delta (proxy).
    const out = [];
    if (!Array.isArray(points)) return out;
    const byDay = new Map();
    for (const p of points) {
      if (typeof p.used !== 'number') continue;
      const d = new Date(p.collected_at);
      if (Number.isNaN(d.getTime())) continue;
      const key = d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
      const arr = byDay.get(key) || [];
      arr.push({ t: d, v: p.used });
      byDay.set(key, arr);
    }
    const keys = Array.from(byDay.keys()).sort();
    let prev = null;
    for (const k of keys) {
      const arr = byDay.get(k).sort((a, b) => a.t - b.t);
      const last = arr[arr.length - 1].v;
      const first = arr[0].v;
      const delta = prev == null ? 0 : Math.max(0, last - prev);
      out.push({
        day: new Date(arr[arr.length - 1].t.getFullYear(), arr[arr.length - 1].t.getMonth(), arr[arr.length - 1].t.getDate()),
        value: delta,
      });
      prev = last;
    }
    return out;
  }

  /* ------------------------------- chart -------------------------------- */

  /**
   * Render the historical chart into the given container.
   *
   * options:
   *   mode: 'percent' | 'spend'
   *   points: array of history points
   *   burns: { '1h': value, '15m': value }  // for slope annotation
   *   etaIso: ISO string projected exhaustion marker (optional)
   */
  function renderHistoricalChart(container, options) {
    if (!container) return;
    clear(container);
    const W = container.clientWidth || 720;
    const H = container.clientHeight || 280;
    const PAD_L = 44;
    const PAD_R = 16;
    const PAD_T = 22;
    const PAD_B = 34;
    const innerW = Math.max(50, W - PAD_L - PAD_R);
    const innerH = Math.max(50, H - PAD_T - PAD_B);

    const svg = svgEl('svg', {
      xmlns: 'http://www.w3.org/2000/svg',
      viewBox: '0 0 ' + W + ' ' + H,
      width: String(W),
      height: String(H),
      class: 'history-svg',
    });
    container.appendChild(svg);

    if (options && options.mode === 'spend') {
      renderSpendBars(svg, options, {
        x: PAD_L,
        y: PAD_T,
        w: innerW,
        h: innerH,
      });
      drawAxisFrame(svg, W, H, PAD_L, PAD_R, PAD_T, PAD_B);
      return;
    }

    const points = (options && options.points) || [];
    const series = buildSeries(points);
    if (!series.length) {
      drawEmptyState(svg, W, H, 'нет данных за выбранный диапазон');
      drawAxisFrame(svg, W, H, PAD_L, PAD_R, PAD_T, PAD_B);
      return;
    }
    const xr = xRange(series);
    const yMin = 0;
    const yMax = 100;

    function sx(t) {
      return PAD_L + ((t.getTime() - xr.min) / (xr.max - xr.min)) * innerW;
    }
    function sy(v) {
      return PAD_T + (1 - (v - yMin) / (yMax - yMin)) * innerH;
    }

    // grid + axis labels -----------------------------------------------
    drawYGrid(svg, PAD_L, PAD_T, innerW, innerH, [0, 25, 50, 75, 100]);
    drawXLabels(svg, PAD_L, PAD_T, innerW, innerH, xr);

    // threshold lines 70/85/95 -----------------------------------------
    const thresholds = [
      { v: 70, label: 'warn', cls: 'warn' },
      { v: 85, label: 'high', cls: 'high' },
      { v: 95, label: 'crit', cls: 'crit' },
    ];
    for (const th of thresholds) {
      const y = sy(th.v);
      svg.appendChild(
        svgEl('line', {
          x1: PAD_L, x2: PAD_L + innerW, y1: y, y2: y,
          class: 'threshold ' + th.cls,
        }),
      );
      svg.appendChild(
        svgEl('text', {
          x: PAD_L + innerW - 4, y: y - 3,
          class: 'threshold-label ' + th.cls,
          'text-anchor': 'end',
        }),
      ).textContent = th.v + '% ' + th.label;
    }

    // reset markers (vertical lines where reset_at changes) ------------
    let prevReset = null;
    for (let i = 0; i < series.length; i++) {
      const cur = series[i].reset_at;
      if (cur && prevReset !== null && cur !== prevReset && i > 0) {
        const x = sx(series[i].time);
        svg.appendChild(
          svgEl('line', {
            x1: x, x2: x, y1: PAD_T, y2: PAD_T + innerH,
            class: 'reset-marker',
          }),
        );
        svg.appendChild(
          svgEl('text', {
            x: x + 3, y: PAD_T + 11, class: 'reset-label',
          }),
        ).textContent = 'reset';
      }
      if (cur) prevReset = cur;
    }

    // line ------------------------------------------------------------
    const path = series
      .map((p, i) => (i === 0 ? 'M' : 'L') + sx(p.time).toFixed(1) + ',' + sy(p.value).toFixed(1))
      .join(' ');
    svg.appendChild(
      svgEl('path', {
        d: path,
        class: 'series-line',
        fill: 'none',
      }),
    );

    // area under the line for visual weight ----------------------------
    const areaPath =
      path +
      ' L' + sx(series[series.length - 1].time).toFixed(1) + ',' + (PAD_T + innerH).toFixed(1) +
      ' L' + sx(series[0].time).toFixed(1) + ',' + (PAD_T + innerH).toFixed(1) +
      ' Z';
    svg.appendChild(
      svgEl('path', {
        d: areaPath,
        class: 'series-area',
      }),
    );

    // projected exhaustion (now + eta) --------------------------------
    if (options && options.etaIso) {
      const t = new Date(options.etaIso);
      if (!Number.isNaN(t.getTime()) && t.getTime() >= xr.min && t.getTime() <= xr.max) {
        const x = sx(t);
        svg.appendChild(
          svgEl('line', {
            x1: x, x2: x, y1: PAD_T, y2: PAD_T + innerH,
            class: 'eta-marker',
          }),
        );
        svg.appendChild(
          svgEl('text', {
            x: x + 4, y: PAD_T + 11, class: 'eta-label',
          }),
        ).textContent = 'ETA ' + formatTimeShort(t);
      }
    }

    // slope annotation -------------------------------------------------
    if (options && options.burns) {
      const lines = [];
      const b15 = options.burns['15m'];
      const b1h = options.burns['1h'];
      if (typeof b15 === 'number') lines.push('burn 15m: ' + (Math.round(b15 * 10) / 10) + ' ед/ч');
      if (typeof b1h === 'number') lines.push('burn 1h: ' + (Math.round(b1h * 10) / 10) + ' ед/ч');
      if (lines.length) {
        const txt = svgEl('text', {
          x: PAD_L + 6, y: PAD_T + innerH - 6,
          class: 'slope-note',
        });
        txt.textContent = lines.join(' · ');
        svg.appendChild(txt);
      }
    }

    drawAxisFrame(svg, W, H, PAD_L, PAD_R, PAD_T, PAD_B);
  }

  function renderSpendBars(svg, options, geom) {
    const points = (options && options.points) || [];
    const series = buildDailySpend(points);
    if (!series.length) {
      drawEmptyState(svg, geom.x + geom.w, geom.y + geom.h, 'нет данных для расхода');
      return;
    }
    const maxV = series.reduce((m, d) => (d.value > m ? d.value : m), 0) || 1;
    const barW = Math.max(8, (geom.w / series.length) * 0.6);
    const gap = (geom.w - barW * series.length) / Math.max(1, series.length + 1);
    series.forEach((d, i) => {
      const x = geom.x + gap + i * (barW + gap);
      const h = (d.value / maxV) * (geom.h - 24);
      const y = geom.y + (geom.h - 24) - h;
      svg.appendChild(
        svgEl('rect', {
          x: x, y: y, width: barW, height: h, rx: 4,
          class: 'spend-bar',
        }),
      );
      const label = svgEl('text', {
        x: x + barW / 2, y: geom.y + geom.h - 8,
        class: 'spend-label',
        'text-anchor': 'middle',
      });
      label.textContent = String(d.day.getDate());
      svg.appendChild(label);
      const value = svgEl('text', {
        x: x + barW / 2, y: y - 3,
        class: 'spend-value',
        'text-anchor': 'middle',
      });
      value.textContent = (Math.round(d.value * 10) / 10).toString();
      svg.appendChild(value);
    });
  }

  function drawYGrid(svg, x, y, w, h, vals) {
    for (const v of vals) {
      const yy = y + (1 - v / 100) * h;
      svg.appendChild(
        svgEl('line', {
          x1: x, x2: x + w, y1: yy, y2: yy,
          class: 'grid-line',
        }),
      );
      const t = svgEl('text', {
        x: x - 6, y: yy + 3,
        class: 'axis-label',
        'text-anchor': 'end',
      });
      t.textContent = v + '%';
      svg.appendChild(t);
    }
  }

  function drawXLabels(svg, x, y, w, h, xr) {
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const ratio = i / ticks;
      const t = new Date(xr.min + (xr.max - xr.min) * ratio);
      const xx = x + ratio * w;
      svg.appendChild(
        svgEl('line', {
          x1: xx, x2: xx, y1: y + h, y2: y + h + 3,
          class: 'tick-line',
        }),
      );
      const lbl = svgEl('text', {
        x: xx, y: y + h + 16,
        class: 'axis-label',
        'text-anchor': 'middle',
      });
      lbl.textContent = formatTimeShort(t);
      svg.appendChild(lbl);
    }
  }

  function drawAxisFrame(svg, W, H, pl, pr, pt, pb) {
    // left axis line
    svg.appendChild(
      svgEl('line', {
        x1: pl, x2: pl, y1: pt, y2: pt + (H - pt - pb),
        class: 'axis',
      }),
    );
    // bottom axis line
    svg.appendChild(
      svgEl('line', {
        x1: pl, x2: pl + (W - pl - pr), y1: pt + (H - pt - pb), y2: pt + (H - pt - pb),
        class: 'axis',
      }),
    );
  }

  function drawEmptyState(svg, w, h, msg) {
    const t = svgEl('text', {
      x: w / 2, y: h / 2,
      class: 'empty-state',
      'text-anchor': 'middle',
    });
    t.textContent = msg;
    svg.appendChild(t);
  }

  /* ------------------------------- exports ------------------------------ */

  window.ChartUtils = {
    duration: duration,
    formatTime: formatTime,
    formatTimeShort: formatTimeShort,
    buildSeries: buildSeries,
    renderHistoricalChart: renderHistoricalChart,
  };
})(window);

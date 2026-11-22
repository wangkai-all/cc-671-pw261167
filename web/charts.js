/* Small SVG chart helpers for the plan-preference analyser.
   No dependencies. All chart forms used here are horizontal bars,
   grouped/stacked share bars, a diverging change chart and a heatmap.
   Marks follow the dataviz spec: <=24px bars, 4px rounded data-ends,
   2px surface gaps, hairline grids, direct labels in text tokens. */

(function () {
  const NS = "http://www.w3.org/2000/svg";

  function el(name, attrs, children) {
    const node = document.createElementNS(NS, name);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined) continue;
        node.setAttribute(k, v);
      }
    }
    if (children) {
      for (const child of children) {
        if (child) node.appendChild(child);
      }
    }
    return node;
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function text(value, attrs) {
    const node = el("text", attrs);
    node.textContent = value;
    return node;
  }

  function pct(v, digits = 1) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return (v * 100).toFixed(digits) + "%";
  }

  function signedPct(v, digits = 1) {
    const sign = v > 0 ? "+" : v < 0 ? "−" : "";
    return sign + Math.abs(v * 100).toFixed(digits) + "pp";
  }

  function money(v, digits = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return "¥" + v.toFixed(digits);
  }

  function niceMax(value) {
    if (!value || value <= 0) return 0.01;
    const exp = Math.floor(Math.log10(value));
    const base = Math.pow(10, exp);
    const norm = value / base;
    let step;
    if (norm <= 1) step = 1;
    else if (norm <= 2) step = 2;
    else if (norm <= 2.5) step = 2.5;
    else if (norm <= 5) step = 5;
    else step = 10;
    return step * base;
  }

  // ----------------------------------------------------------------
  // Horizontal grouped bars: one group per plan, two bars (A vs B).
  // Excellent for share comparison; never a dual axis.
  // ----------------------------------------------------------------
  function groupedBars(container, opts) {
    container.innerHTML = "";
    const rows = opts.rows || [];
    if (!rows.length) {
      container.innerHTML = '<div class="chart-empty">没有可比较的套餐。</div>';
      return;
    }
    const width = Math.max(container.clientWidth || 640, 320);
    const labelW = opts.labelWidth || 132;
    const padR = 58;
    const rowH = 42;
    const barH = 13;
    const gap = 2;
    const top = 22;
    const plotW = width - labelW - padR;
    const max = niceMax(Math.max(...rows.flatMap((r) => [r.a, r.b]), 0.0001));
    const height = top + rows.length * rowH + 22;
    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width: width,
      height: height,
      role: "img",
      "aria-label": opts.ariaLabel || "份额对比条形图",
    });

    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = (max / ticks) * i;
      const x = labelW + (plotW * i) / ticks;
      svg.appendChild(el("line", {
        x1: x, x2: x, y1: top - 6, y2: height - 20,
        class: i === 0 ? "axis-base" : "axis-tick",
      }));
      svg.appendChild(text(Number.isInteger(v * 100) ? (v * 100).toFixed(0) + "%" : pct(v, 0), {
        x, y: top - 10, "text-anchor": "middle", class: "axis-label",
      }));
    }

    rows.forEach((row, index) => {
      const y = top + index * rowH;
      const label = text(row.label, {
        x: 0, y: y + 17, class: "axis-label", "text-anchor": "start",
      });
      svg.appendChild(label);
      if (row.sub) {
        svg.appendChild(text(row.sub, { x: 0, y: y + 30, class: "chart-sub" }));
      }
      const entries = [
        { key: "a", value: row.a, color: opts.colorA, name: opts.nameA || "基准" },
        { key: "b", value: row.b, color: opts.colorB, name: opts.nameB || "情景" },
      ];
      entries.forEach((entry, i) => {
        const barY = y + 4 + i * (barH + gap);
        const barW = Math.max(0, (entry.value / max) * plotW);
        const g = el("g", { class: "bar-mark", tabindex: "0", role: "listitem" });
        g.setAttribute("aria-label", `${row.label} · ${entry.name} ${pct(entry.value)}`);
        g.appendChild(el("rect", {
          x: labelW, y: barY - 1, width: Math.max(plotW, 4), height: barH + 2,
          fill: "transparent",
        }));
        g.appendChild(el("rect", {
          x: labelW, y: barY, width: barW, height: barH, rx: 4,
          fill: entry.color,
        }));
        if (barW > 0.5) {
          g.appendChild(text(pct(entry.value), {
            x: labelW + barW + 6, y: barY + barH - 2.5, class: "bar-label",
          }));
        }
        attachTip(g, () => tipHtml(entry.name, pct(entry.value), entry.color, [
          `${row.label}`,
          row.note || "",
        ]));
        svg.appendChild(g);
      });
    });
    container.appendChild(svg);
    container.appendChild(legend([
      { name: opts.nameA || "基准", color: opts.colorA },
      { name: opts.nameB || "情景", color: opts.colorB },
    ]));
  }

  // ----------------------------------------------------------------
  // Diverging change chart: gains to the right, losses to the left.
  // ----------------------------------------------------------------
  function divergingChart(container, opts) {
    container.innerHTML = "";
    const rows = (opts.rows || []).filter((r) => Math.abs(r.value) > 1e-9);
    if (!rows.length) {
      container.innerHTML = '<div class="chart-empty">情景与基准没有可显示的份额差。</div>';
      return;
    }
    rows.sort((a, b) => b.value - a.value);
    const width = Math.max(container.clientWidth || 640, 320);
    const labelW = opts.labelWidth || 136;
    const padR = 64;
    const rowH = 30;
    const barH = 16;
    const top = 24;
    const plotW = width - labelW - padR;
    const maxAbs = niceMax(Math.max(...rows.map((r) => Math.abs(r.value)), 1e-4));
    const height = top + rows.length * rowH + 14;
    const mid = labelW + plotW / 2;
    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width, height, role: "img",
      "aria-label": opts.ariaLabel || "份额变化图",
    });
    svg.appendChild(el("line", { x1: mid, x2: mid, y1: top - 8, y2: height - 10, class: "axis-base" }));
    for (const frac of [-1, -0.5, 0.5, 1]) {
      const x = mid + (plotW / 2) * frac;
      svg.appendChild(el("line", { x1: x, x2: x, y1: top - 8, y2: height - 10, class: "axis-tick" }));
      svg.appendChild(text(signedPct(maxAbs * frac, 0).replace("+", ""), {
        x, y: top - 12, "text-anchor": "middle", class: "axis-label",
      }));
    }
    rows.forEach((row, index) => {
      const y = top + index * rowH;
      const barW = Math.max(1.5, (Math.abs(row.value) / maxAbs) * (plotW / 2));
      const isGain = row.value >= 0;
      const g = el("g", { class: "bar-mark", tabindex: "0" });
      g.setAttribute("aria-label", `${row.label} ${signedPct(row.value)}`);
      g.appendChild(el("rect", {
        x: labelW, y: y - 2, width: plotW, height: barH + 4, fill: "transparent",
      }));
      g.appendChild(el("rect", {
        x: isGain ? mid : mid - barW,
        y: y + 2, width: barW, height: barH, rx: 4,
        fill: isGain ? opts.positive : opts.negative,
      }));
      g.appendChild(text(signedPct(row.value), {
        x: isGain ? mid + barW + 6 : mid - barW - 6,
        y: y + barH - 2,
        "text-anchor": isGain ? "start" : "end",
        class: "bar-label",
      }));
      attachTip(g, () => tipHtml(row.name || row.label, signedPct(row.value),
        isGain ? opts.positive : opts.negative, [row.note || ""]));
      svg.appendChild(g);
      svg.appendChild(text(row.label, {
        x: 0, y: y + barH - 2, class: "axis-label", "text-anchor": "start",
      }));
      if (row.sub) {
        svg.appendChild(text(row.sub, { x: 0, y: y + barH + 10, class: "chart-sub" }));
      }
    });
    container.appendChild(svg);
    container.appendChild(legend([
      { name: opts.namePositive || "份额增加", color: opts.positive },
      { name: opts.nameNegative || "份额减少", color: opts.negative },
    ]));
  }

  // ----------------------------------------------------------------
  // Single-series horizontal bars — used for absorption / contribution.
  // ----------------------------------------------------------------
  function singleBars(container, opts) {
    container.innerHTML = "";
    const rows = (opts.rows || []).filter((r) => r.value > 1e-9);
    if (!rows.length) {
      container.innerHTML = `<div class="chart-empty">${opts.empty || "没有可显示的数据。"}</div>`;
      return;
    }
    rows.sort((a, b) => b.value - a.value);
    const width = Math.max(container.clientWidth || 640, 320);
    const labelW = opts.labelWidth || 132;
    const padR = 60;
    const rowH = 30;
    const barH = 16;
    const top = 22;
    const plotW = width - labelW - padR;
    const max = niceMax(Math.max(...rows.map((r) => r.value), 1e-4));
    const height = top + rows.length * rowH + 14;
    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
      "aria-label": opts.ariaLabel || "份额图",
    });
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i;
      const x = labelW + (plotW * i) / 4;
      svg.appendChild(el("line", {
        x1: x, x2: x, y1: top - 6, y2: height - 12,
        class: i === 0 ? "axis-base" : "axis-tick",
      }));
      svg.appendChild(text(pct(v, 0), { x, y: top - 10, "text-anchor": "middle", class: "axis-label" }));
    }
    rows.forEach((row, index) => {
      const y = top + index * rowH;
      const barW = Math.max(1.5, (row.value / max) * plotW);
      const g = el("g", { class: "bar-mark", tabindex: "0" });
      g.setAttribute("aria-label", `${row.label} ${pct(row.value)}`);
      g.appendChild(el("rect", { x: labelW, y: y - 2, width: plotW, height: barH + 4, fill: "transparent" }));
      g.appendChild(el("rect", {
        x: labelW, y: y + 2, width: barW, height: barH, rx: 4,
        fill: row.color || opts.color,
      }));
      g.appendChild(text(opts.valueLabel ? opts.valueLabel(row) : pct(row.value), {
        x: labelW + barW + 6, y: y + barH - 2, class: "bar-label",
      }));
      attachTip(g, () => tipHtml(row.name || row.label,
        opts.valueLabel ? opts.valueLabel(row) : pct(row.value),
        row.color || opts.color, row.tip || []));
      svg.appendChild(g);
      svg.appendChild(text(row.label, { x: 0, y: y + barH - 2, class: "axis-label" }));
      if (row.sub) svg.appendChild(text(row.sub, { x: 0, y: y + barH + 10, class: "chart-sub" }));
    });
    container.appendChild(svg);
    if (opts.legend) container.appendChild(legend(opts.legend));
  }

  // ----------------------------------------------------------------
  // Stacked 100% bar for one menu — shows the whole (including none).
  // 2px surface gaps separate segments; labels only when they fit.
  // ----------------------------------------------------------------
  function stackedShareBar(container, opts) {
    container.innerHTML = "";
    const parts = (opts.parts || []).filter((p) => p.value > 1e-5);
    const total = parts.reduce((s, p) => s + p.value, 0) || 1;
    const width = Math.max(container.clientWidth || 640, 320);
    const height = 58;
    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
      "aria-label": opts.ariaLabel || "菜单份额构成",
    });
    const gap = 2;
    let x = 0;
    parts.forEach((part) => {
      const w = (part.value / total) * width;
      const rectW = Math.max(1, w - gap);
      const g = el("g", { class: "bar-mark", tabindex: "0" });
      g.setAttribute("aria-label", `${part.label} ${pct(part.value)}`);
      g.appendChild(el("rect", {
        x, y: 4, width: rectW, height: 26, rx: 4, fill: part.color,
      }));
      if (rectW > 46) {
        g.appendChild(text(pct(part.value, 0), {
          x: x + rectW / 2, y: 21, "text-anchor": "middle",
          fill: part.dark ? "#0b0b0b" : "#ffffff", "font-size": "11", "font-family": "var(--font)",
        }));
      }
      attachTip(g, () => tipHtml(part.label, pct(part.value), part.color, []));
      svg.appendChild(g);
      x += w;
    });
    container.appendChild(svg);
    container.appendChild(legend(parts.map((p) => ({ name: p.label, color: p.color }))));
  }

  // ----------------------------------------------------------------
  // Heatmap for correlations. Diverging blue↔red with neutral gray mid.
  // ----------------------------------------------------------------
  function heatmap(container, opts) {
    container.innerHTML = "";
    const labels = opts.labels || [];
    const matrix = opts.matrix || [];
    const n = labels.length;
    if (!n) return;
    const cell = Math.min(64, Math.max(38, (container.clientWidth || 600) / n));
    const padL = 88;
    const padT = 72;
    const width = padL + cell * n + 8;
    const height = padT + cell * n + 8;
    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
      "aria-label": "属性相关性矩阵",
    });
    const pos = cssVar("--series-1");
    const neg = cssVar("--series-8");
    const neutral = cssVar("--surface-2");
    function mix(a, b, t) {
      const pa = hex(a), pb = hex(b);
      const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
    function hex(value) {
      const m = value.replace("#", "");
      if (m.length === 3) return m.split("").map((c) => parseInt(c + c, 16));
      return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16), parseInt(m.slice(4, 6), 16)];
    }
    labels.forEach((label, i) => {
      svg.appendChild(text(label, {
        x: padL - 8, y: padT + i * cell + cell / 2 + 4,
        "text-anchor": "end", class: "hm-label",
      }));
      svg.appendChild(text(label, {
        x: padL + i * cell + cell / 2, y: padT - 10,
        "text-anchor": "start", class: "hm-label",
        transform: `rotate(-40 ${padL + i * cell + cell / 2} ${padT - 10})`,
      }));
    });
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const v = matrix[i][j];
        const t = Math.min(1, Math.abs(v));
        const fill = v >= 0 ? mix(neutral, pos, t) : mix(neutral, neg, t);
        const g = el("g", { tabindex: "0" });
        g.setAttribute("aria-label", `${labels[i]} 与 ${labels[j]} 相关 ${v.toFixed(2)}`);
        g.appendChild(el("rect", {
          x: padL + j * cell, y: padT + i * cell,
          width: cell, height: cell, fill, class: "hm-cell",
        }));
        if (i !== j) {
          g.appendChild(text(v.toFixed(2).replace("0.", ".").replace("-.", "−."), {
            x: padL + j * cell + cell / 2, y: padT + i * cell + cell / 2 + 4,
            "text-anchor": "middle", class: "hm-value",
            fill: t > 0.55 ? "#ffffff" : cssVar("--text-secondary"),
          }));
        }
        attachTip(g, () => tipHtml(`${labels[i]} × ${labels[j]}`, v.toFixed(3), fill, []));
        svg.appendChild(g);
      }
    }
    container.appendChild(svg);
    container.appendChild(legend([
      { name: "负相关", color: neg },
      { name: "无相关", color: "transparent", border: cssVar("--axis") },
      { name: "正相关", color: pos },
    ]));
  }

  // ----------------------------------------------------------------
  // tooltip + legend helpers
  // ----------------------------------------------------------------
  function tipHtml(title, value, color, extra) {
    const rows = (extra || []).filter(Boolean).map((line) =>
      `<div class="tt-row">${escapeHtml(line)}</div>`).join("");
    return `<div class="tt-value">${escapeHtml(value)}</div>` +
      `<div class="tt-row"><span class="tt-key" style="background:${color}"></span>` +
      `${escapeHtml(title)}</div>${rows}`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const tooltip = document.getElementById("tooltip");
  function attachTip(node, htmlFn) {
    function show(evt) {
      if (!tooltip) return;
      tooltip.innerHTML = htmlFn();
      tooltip.hidden = false;
      const rect = tooltip.getBoundingClientRect();
      let x = (evt.clientX || 0) + 14;
      let y = (evt.clientY || 0) + 14;
      if (x + rect.width > window.innerWidth - 10) x = window.innerWidth - rect.width - 10;
      if (y + rect.height > window.innerHeight - 10) y = window.innerHeight - rect.height - 10;
      tooltip.style.left = x + "px";
      tooltip.style.top = y + "px";
    }
    function hide() { if (tooltip) tooltip.hidden = true; }
    node.addEventListener("pointermove", show);
    node.addEventListener("pointerenter", show);
    node.addEventListener("pointerleave", hide);
    node.addEventListener("focus", () => {
      const r = node.getBoundingClientRect();
      show({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
    });
    node.addEventListener("blur", hide);
  }

  function legend(items) {
    const box = document.createElement("div");
    box.className = "legend";
    for (const item of items) {
      const span = document.createElement("span");
      span.className = "item";
      const sw = document.createElement("span");
      sw.className = item.line ? "line-key" : "swatch";
      sw.style.background = item.color;
      if (item.border) {
        sw.style.border = `1px solid ${item.border}`;
        sw.style.background = "transparent";
      }
      span.appendChild(sw);
      span.appendChild(document.createTextNode(item.name));
      box.appendChild(span);
    }
    return box;
  }

  window.Charts = {
    groupedBars, divergingChart, singleBars, stackedShareBar, heatmap,
    legend, pct, signedPct, money, cssVar, escapeHtml,
  };
})();

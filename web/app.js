/* Plan preference analyser — page logic.
   All data comes from the local, fictional sample API. Nothing is sent out. */

(function () {
  const { pct, signedPct, money, escapeHtml } = window.Charts;
  const cssVar = window.Charts.cssVar;

  const state = {
    sample: "main",
    searchPrices: {},
    model: null,
    menu: null,
    catalog: [],
    scenario: [],          // [{package_id, price, unit_cost, ...}]
    baseline: [],          // snapshot of current menu for comparison
    compare: null,
    activePlan: null,
    search: null,
    searchSelection: null,
    candidateIds: [],
    searchResult: null,
    dirty: false,
  };

  const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4",
                  "--series-5", "--series-6", "--series-7", "--series-8"];

  function seriesColor(index) {
    return cssVar(SERIES[index % SERIES.length]);
  }

  function planColorMap(ids) {
    const map = new Map();
    const stable = [...ids].sort((a, b) => a.localeCompare(b));
    stable.forEach((id, i) => map.set(id, seriesColor(i)));
    return map;
  }

  // ------------------------------------------------------------------
  // API
  // ------------------------------------------------------------------
  async function api(path, options) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let body = null;
    try { body = await response.json(); } catch (e) { body = null; }
    if (!response.ok) {
      const message = (body && (body.message || body.error)) || `请求失败（${response.status}）`;
      const error = new Error(message);
      error.body = body;
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function setStatus(id, message, kind) {
    const node = document.getElementById(id);
    if (!node) return;
    if (!message) { node.hidden = true; node.innerHTML = ""; return; }
    node.hidden = false;
    node.className = "global-status " + (kind || "");
    const icon = kind === "critical" ? "!" : kind === "warning" ? "△" : kind === "good" ? "✓" : "·";
    node.innerHTML = `<span class="icon" aria-hidden="true">${icon}</span><div>${escapeHtml(message)}</div>`;
  }

  // ------------------------------------------------------------------
  // formatting helpers
  // ------------------------------------------------------------------
  function fmtInt(v) {
    if (v === null || v === undefined) return "—";
    return Number(v).toLocaleString("zh-CN");
  }
  function fmtNum(v, digits = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return Number(v).toFixed(digits);
  }
  function fmtSigned(v, digits = 2) {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(digits);
  }
  function fmtP(v) {
    if (v === null || v === undefined) return "—";
    if (v < 0.0001) return "<0.0001";
    return v.toFixed(4);
  }
  function planName(id) {
    const found = state.catalog.find((p) => p.id === id || p.package_id === id);
    return found ? found.name : id;
  }
  function planInfo(id) {
    return state.catalog.find((p) => p.id === id) || null;
  }
  function tierLabel(tier) {
    return { entry: "入门档", mid: "中档", premium: "高档" }[tier] || tier;
  }
  function statusTag(info) {
    if (!info) return "";
    if (info.status === "retired") return '<span class="tag retired">已停售（样本候选）</span>';
    if (info.status === "candidate") return '<span class="tag candidate">未上市（情景候选）</span>';
    return '<span class="tag">在售</span>';
  }

  // ------------------------------------------------------------------
  // boot
  // ------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    wireTabs();
    wireTheme();
    document.getElementById("sample-select").addEventListener("change", async (evt) => {
      state.sample = evt.target.value;
      await loadAll();
    });
    document.getElementById("reload-model").addEventListener("click", loadAll);
    document.querySelectorAll("[data-toggle-table]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = document.getElementById(btn.dataset.toggleTable);
        if (!target) return;
        target.hidden = !target.hidden;
        if (!target.hidden && btn.dataset.toggleTable === "corr-heatmap") {
          renderCorrHeatmap();
        }
      });
    });
    await loadSamples();
    await loadAll();
  }

  function wireTabs() {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
        document.querySelectorAll(".view").forEach((v) => {
          v.classList.toggle("active", v.id === "view-" + tab.dataset.view);
        });
        window.scrollTo({ top: 0, behavior: "smooth" });
        // charts size themselves from container width
        requestAnimationFrame(() => redrawActiveView());
      });
    });
  }

  function wireTheme() {
    const root = document.documentElement;
    const saved = localStorage.getItem("planprefs-theme");
    if (saved) root.dataset.theme = saved;
    document.getElementById("theme-toggle").addEventListener("click", () => {
      const order = ["auto", "light", "dark"];
      const current = root.dataset.theme || "auto";
      const next = order[(order.indexOf(current) + 1) % order.length];
      root.dataset.theme = next;
      localStorage.setItem("planprefs-theme", next);
      redrawActiveView();
    });
  }

  function redrawActiveView() {
    if (!state.model) return;
    const active = document.querySelector(".tab.active").dataset.view;
    if (active === "overview") renderOverview();
    if (active === "scenario") renderScenario();
    if (active === "plan") renderPlanDetail();
    if (active === "search") renderSearch();
  }

  async function loadSamples() {
    const data = await api("/api/samples");
    const select = document.getElementById("sample-select");
    select.innerHTML = "";
    data.samples.forEach((sample) => {
      const option = document.createElement("option");
      option.value = sample.key;
      option.textContent = sample.name;
      select.appendChild(option);
    });
    state.sample = data.default;
    select.value = data.default;
  }

  async function loadAll() {
    setStatus("global-status", "正在读取样本与拟合模型…", "");
    try {
      const [model, menu] = await Promise.all([
        api(`/api/model?sample=${encodeURIComponent(state.sample)}`),
        api(`/api/menu?sample=${encodeURIComponent(state.sample)}`),
      ]);
      state.model = model;
      state.menu = menu;
      state.catalog = menu.catalog;
      state.scenario = menu.plans.map((p) => ({ ...p }));
      state.baseline = menu.plans.map((p) => ({ ...p }));
      state.compare = null;
      state.planLoadedKey = null;
      state.activePlan = menu.plans.length ? menu.plans[0].package_id : null;
      state.candidateIds = menu.plans.map((p) => p.package_id).slice(0, 10);
      state.searchPrices = {};
      state.searchResult = null;
      state.searchSelection = null;
      state.dirty = false;
      setStatus("global-status", "", "");
      renderAll();
      if (!state.model.blocked) {
        await runCompare();
        document.getElementById("search-status").textContent = "尚未搜索：选择候选后点击“搜索组合”。";
      }
      if (state.model.blocked) {
        setStatus("global-status",
          "模型诊断未通过（见“数据与模型”页），情景与搜索已禁用；这里只保留诊断，不给出经营建议。",
          "critical");
      }
    } catch (error) {
      setStatus("global-status", error.message, "critical");
    }
  }

  function renderAll() {
    renderOverview();
    renderScenario();
    renderPlanDetail();
    renderSearch();
    renderBlockedState();
  }

  function renderBlockedState() {
    const blocked = state.model && state.model.blocked;
    ["scenario", "search"].forEach((view) => {
      const id = view === "scenario" ? "scenario-warning" : "search-warning";
      if (blocked) {
        setStatus(id, "模型诊断未通过，本页的情景与组合计算已停用。请先在“数据与模型”页查看诊断。", "critical");
      } else {
        setStatus(id, "", "");
      }
    });
    if (blocked) {
      setStatus("plan-warning",
        "模型诊断未通过：下面的份额数字只是拟合结果，不可用于经营判断；转移与情景分析已在来源页停用。",
        "critical");
    } else {
      setStatus("plan-warning", "", "");
    }
    document.querySelectorAll("#view-scenario button, #view-scenario input, #view-scenario select, #view-scenario [tabindex], #view-search button, #view-search input, #view-search select")
      .forEach((node) => {
        if (node.closest(".tabs")) return;
        if (node.id === "reset-scenario") return;
        node.toggleAttribute("disabled", !!blocked);
      });
    document.querySelectorAll("#view-scenario .chip, #view-search .chip").forEach((node) => {
      if (blocked) node.setAttribute("disabled", "true"); else node.removeAttribute("disabled");
    });
  }

  // ==================================================================
  // OVERVIEW
  // ==================================================================
  function renderOverview() {
    const model = state.model;
    renderTiles(model);
    renderDiagnostics(model);
    renderFacts(model);
    renderCoefficients(model);
    renderPlanTable(model);
    renderExamples(model);
    renderQuality(model);
    renderCorrHeatmap();
    document.getElementById("model-footnote").textContent =
      `对数似然 ${fmtNum(model.loglik, 1)} / 空模型 ${fmtNum(model.loglik_null, 1)}；` +
      `伪 R² ${fmtNum(model.pseudo_r2, 3)}；迭代 ${model.iterations} 次；` +
      `梯度范数 ${model.gradient_norm.toExponential(1)}。`;
  }

  function renderTiles(model) {
    const ds = model.dataset;
    const tiles = [
      { label: "有效选择记录", value: fmtInt(model.n_records), note: "每条整记录一次似然" },
      { label: "展示过的套餐项", value: fmtInt(model.n_alternatives), note: "来自各自当时的菜单" },
      { label: "“都不选”比例", value: ds.none_rate === null ? "—" : pct(ds.none_rate),
        note: `${fmtInt(ds.none_chosen)} 条未成交` },
      { label: "平均菜单规模", value: ds.menu_size_avg === null ? "—" : fmtNum(ds.menu_size_avg, 1) + " 款",
        note: ds.menu_size_range ? `范围 ${ds.menu_size_range[0]}–${ds.menu_size_range[1]}` : "" },
      { label: "不同菜单组合", value: fmtInt(ds.distinct_menus), note: "历史展示集合的去重数" },
      { label: "重复提交忽略", value: fmtInt(ds.duplicates_ignored), note: "按幂等键去重" },
    ];
    const box = document.getElementById("summary-tiles");
    box.innerHTML = "";
    tiles.forEach((tile) => {
      const div = document.createElement("div");
      div.className = "tile";
      div.innerHTML = `<div class="label">${escapeHtml(tile.label)}</div>` +
        `<div class="value">${escapeHtml(String(tile.value))}</div>` +
        `<div class="note">${escapeHtml(tile.note)}</div>`;
      box.appendChild(div);
    });
  }

  function renderDiagnostics(model) {
    const box = document.getElementById("diagnostics");
    box.innerHTML = "";
    if (!model.diagnostics.length) {
      const ok = document.createElement("div");
      ok.className = "diag-ok";
      ok.innerHTML = '<span class="diag-icon" aria-hidden="true">✓</span>' +
        '<div><div class="diag-title">诊断通过</div>' +
        '<div class="diag-detail">模型收敛、信息矩阵正定、价格系数为负且显著，属性未见高度共线。份额情景可以解释，但仍基于虚构样本。</div></div>';
      box.appendChild(ok);
      return;
    }
    model.diagnostics.forEach((diag) => {
      const div = document.createElement("div");
      div.className = "diag " + (diag.severity || "warning");
      const icon = diag.severity === "critical" ? "!" : "△";
      div.innerHTML = `<span class="diag-icon" aria-hidden="true">${icon}</span>` +
        `<div><div class="diag-title">${escapeHtml(diag.message)}</div>` +
        `<div class="diag-detail">${escapeHtml(diag.detail || "")}</div></div>`;
      box.appendChild(div);
    });
    if (model.blocked) {
      const note = document.createElement("div");
      note.className = "diag critical";
      note.innerHTML = '<span class="diag-icon" aria-hidden="true">!</span>' +
        '<div><div class="diag-title">结论：不直接给经营建议</div>' +
        '<div class="diag-detail">出现严重诊断时，系数与份额不可解释；菜单情景和组合搜索已停用。</div></div>';
      box.appendChild(note);
    }
  }

  function renderFacts(model) {
    const ds = model.dataset;
    const facts = [
      ["样本文件", state.sample],
      ["日期范围", ds.date_range ? `${ds.date_range[0]} → ${ds.date_range[1]}` : "—"],
      ["原始记录", fmtInt(ds.raw_count)],
      ["有效记录", fmtInt(ds.records)],
      ["校验拒绝", fmtInt(ds.rejected_count)],
      ["模型参数", `${model.coefficients.length} 个`],
      ["收敛", model.converged ? "是" : "否"],
      ["价格系数方向", model.coefficients.find((c) => c.key === "price").beta < 0 ? "为负" : "异常"],
    ];
    const dl = document.getElementById("dataset-facts");
    dl.innerHTML = "";
    facts.forEach(([k, v]) => {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
  }

  function collinearKeys() {
    const keys = new Set();
    (state.model.diagnostics || []).forEach((d) => {
      if (d.code === "collinear" && d.pair) d.pair.forEach((k) => keys.add(k));
    });
    return keys;
  }

  function collinearPairs() {
    return (state.model.diagnostics || [])
      .filter((d) => d.code === "collinear" && d.pair)
      .map((d) => d.pair);
  }

  function renderCoefficients(model) {
    const tbody = document.querySelector("#coef-table tbody");
    tbody.innerHTML = "";
    const flagged = collinearKeys();
    const wtpBlocked = model.blocked || (model.coefficients.find((c) => c.key === "price") || {}).beta >= 0;
    model.coefficients.forEach((coef) => {
      const tr = document.createElement("tr");
      const isPrice = coef.key === "price";
      const collide = flagged.has(coef.key);
      let wtp = "—";
      if (!isPrice && coef.key !== "none" && collide) {
        wtp = '<span class="muted">不可分</span>';
      } else if (!isPrice && coef.key !== "none" && !wtpBlocked && coef.wtp_per_unit !== null) {
        wtp = `¥${coef.wtp_per_unit.toFixed(2)} / ${coef.wtp_unit}`;
      } else if (!isPrice && coef.key !== "none" && wtpBlocked) {
        wtp = '<span class="muted">不可用</span>';
      }
      let note = coef.description;
      if (collide) note += " ⚠ 与其他属性高度共线，单独效应不可分。";
      if (coef.key === "none") note = coef.description;
      const sig = coef.significant ? "" : ' <span class="muted">（不显著）</span>';
      tr.innerHTML = `
        <td><strong>${escapeHtml(coef.label)}</strong>${coef.key === "none" ? ' <span class="tag">基准</span>' : ""}</td>
        <td class="num">${fmtSigned(coef.beta, 5)}</td>
        <td class="num">${coef.key === "none" ? "—" : fmtSigned(coef.beta_scaled, 4)}</td>
        <td class="num">${coef.se === null || coef.se === undefined ? "—" : fmtNum(coef.se, 5)}</td>
        <td class="num">${coef.z === null || coef.z === undefined ? "—" : fmtSigned(coef.z, 2)}</td>
        <td class="num">${fmtP(coef.p_value)}${sig}</td>
        <td class="num">${wtp}</td>
        <td class="small">${escapeHtml(note)}</td>`;
      tbody.appendChild(tr);
    });
    const pairText = collinearPairs()
      .map(([a, b]) => `${planNameKey(a)} × ${planNameKey(b)}`)
      .join("；");
    document.getElementById("coef-note").textContent = pairText
      ? `共线提示：${pairText} 在历史菜单中几乎同步变化，上述成对偏好无法分开估计。`
      : "属性间未见 ≥0.9 的相关，单项支付意愿可读。";
  }

  function planNameKey(key) {
    const map = {
      price: "月费", data_gb: "流量", voice_min: "语音", streaming: "影音权益",
      family: "家庭共享", device: "终端补贴", none: "都不选",
    };
    return map[key] || key;
  }

  function renderCorrHeatmap() {
    const corr = state.model && state.model.correlation;
    const wrap = document.getElementById("corr-heatmap");
    if (wrap && wrap.hidden) return;
    if (!corr || !corr.keys || !corr.matrix.length) {
      document.getElementById("corr-chart").innerHTML =
        '<div class="chart-empty">样本中没有展示记录。</div>';
      return;
    }
    Charts.heatmap(document.getElementById("corr-chart"), {
      labels: corr.keys.map(planNameKey),
      matrix: corr.matrix,
    });
  }

  function renderPlanTable(model) {
    const tbody = document.querySelector("#plan-table tbody");
    tbody.innerHTML = "";
    model.plans.forEach((plan) => {
      const tr = document.createElement("tr");
      const priceRange = plan.price_range
        ? `¥${fmtNum(plan.price_range[0], 0)}–${fmtNum(plan.price_range[1], 0)}`
        : "—";
      let status = statusTag(plan);
      if (!plan.ever_shown) status = '<span class="tag never">未展示 · 不参与拟合</span>';
      tr.innerHTML = `
        <td>${escapeHtml(plan.name)}${plan.is_entry ? ' <span class="tag entry">入门</span>' : ""}</td>
        <td class="num">${fmtInt(plan.shown)}</td>
        <td class="num">${fmtInt(plan.chosen)}</td>
        <td class="num">${plan.rate === null ? "—" : pct(plan.rate)}</td>
        <td class="num">${priceRange}</td>
        <td>${status}</td>`;
      tbody.appendChild(tr);
    });
  }

  function renderExamples(model) {
    const box = document.getElementById("record-examples");
    box.innerHTML = "";
    if (!model.examples.length) {
      box.innerHTML = '<div class="chart-empty">没有可展示的记录。</div>';
      return;
    }
    model.examples.forEach((record) => {
      const div = document.createElement("div");
      div.className = "record";
      const options = record.options.map((o) => {
        const chosen = record.choice === o.package_id;
        const tag = chosen ? '<span class="opt-chip chosen">' : '<span class="opt-chip">';
        return `${tag}${escapeHtml(planName(o.package_id))} · ¥${fmtNum(o.price, 0)}</span>`;
      });
      options.push(record.choice === null
        ? '<span class="opt-chip none chosen">都不选</span>'
        : '<span class="opt-chip none">都不选</span>');
      div.innerHTML = `
        <div class="record-head">
          <span>${escapeHtml(record.submitted_at)}</span>
          <span class="record-choice">选择：${escapeHtml(record.choice_name)}</span>
        </div>
        <div class="record-options">${options.join("")}</div>`;
      box.appendChild(div);
    });
  }

  function renderQuality(model) {
    const box = document.getElementById("quality-body");
    box.innerHTML = "";
    const ds = model.dataset;
    const rows = [
      {
        ok: ds.rejected_count === 0,
        text: ds.rejected_count === 0
          ? "所有记录的被选项都在当时展示集合内，校验通过。"
          : `${ds.rejected_count} 条记录未通过校验（被选项不在当时展示集合内），已拒绝进入拟合；明细见下表。`,
      },
      {
        ok: true,
        text: `重复提交按幂等键去重：忽略 ${ds.duplicates_ignored} 条；原始 ${ds.raw_count} 条，有效 ${ds.records} 条。`,
      },
      {
        ok: true,
        text: "未展示套餐不进入该次选择集；展示次数为 0 的套餐在表中标注“不参与拟合”，不按零次成交处理。",
      },
    ];
    rows.forEach((row) => {
      const div = document.createElement("div");
      div.className = "quality-row";
      div.innerHTML = `<span class="icon" aria-hidden="true">${row.ok ? "✓" : "!"}</span>` +
        `<div>${escapeHtml(row.text)}</div>`;
      box.appendChild(div);
    });
    if (ds.rejected && ds.rejected.length) {
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      const table = document.createElement("table");
      table.className = "data-table compact";
      table.innerHTML = `<thead><tr><th>#</th><th>原因</th><th>选项</th></tr></thead>`;
      const tbody = document.createElement("tbody");
      ds.rejected.forEach((item) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${escapeHtml(String(item.index))}</td>` +
          `<td>${escapeHtml(item.reason)}</td>` +
          `<td class="small">${escapeHtml((item.options || []).join("、"))}</td>`;
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      wrap.appendChild(table);
      box.appendChild(wrap);
    }
  }

  // ==================================================================
  // SCENARIO
  // ==================================================================
  async function runCompare() {
    if (!state.model || state.model.blocked) return;
    const baseline = state.baseline.map(toRequestPlan);
    const scenario = state.scenario.map(toRequestPlan);
    try {
      const result = await api("/api/compare", {
        method: "POST",
        body: JSON.stringify({ sample: state.sample, baseline, scenario }),
      });
      state.compare = result;
      state.dirty = false;
      renderScenario();
    } catch (error) {
      setStatus("scenario-warning", error.message, "critical");
    }
  }

  function toRequestPlan(plan) {
    return {
      package_id: plan.package_id,
      price: Number(plan.price),
      unit_cost: plan.unit_cost === undefined ? undefined : Number(plan.unit_cost),
      data_gb: plan.data_gb,
      voice_min: plan.voice_min,
      streaming: plan.streaming,
      family: plan.family,
      device: plan.device,
    };
  }

  function renderScenario() {
    if (!state.model) return;
    renderMenuEditor();
    renderScenarioComparison();
    renderScenarioTiles();
    renderDisplacement();
    renderScenarioPlanDetail();
    renderAddPlanOptions();
  }

  function renderMenuEditor() {
    const box = document.getElementById("menu-editor");
    box.innerHTML = "";
    const head = document.createElement("div");
    head.className = "menu-head-row";
    head.innerHTML = "<span></span><span>套餐</span><span>情景价</span><span>成本</span><span>属性</span>";
    box.appendChild(head);
    const inMenu = new Set(state.scenario.map((p) => p.package_id));
    const allIds = [...new Set([...state.scenario.map((p) => p.package_id),
                                 ...state.menu.plans.map((p) => p.package_id)])];
    allIds.forEach((id) => {
      const plan = state.scenario.find((p) => p.package_id === id);
      const row = document.createElement("div");
      row.className = "menu-row" + (plan ? "" : " off");
      const info = planInfo(id);
      const name = planName(id);
      row.innerHTML = `
        <input type="checkbox" ${plan ? "checked" : ""} aria-label="纳入 ${escapeHtml(name)}" />
        <span class="mname"><span>${escapeHtml(name)}</span>
          <span class="sub">${escapeHtml(tierLabel(info ? info.tier : ""))} · 流量 ${fmtNum((plan || {}).data_gb, 0)}G</span></span>
        <input type="number" min="1" step="1" value="${plan ? fmtNum(plan.price, 0) : "—"}" ${plan ? "" : "disabled"} aria-label="${escapeHtml(name)} 情景价格" />
        <input type="number" min="0" step="1" value="${plan ? fmtNum(plan.unit_cost, 0) : "—"}" ${plan ? "" : "disabled"} aria-label="${escapeHtml(name)} 单位成本" />
        <span class="mini">${plan ? attributeLine(plan) : "不在情景中"}</span>`;
      const toggle = row.querySelector('input[type="checkbox"]');
      toggle.addEventListener("change", () => {
        if (toggle.checked) {
          const base = (state.menu.plans.find((p) => p.package_id === id)) ||
            (state.catalogPlan(id)) || state.scenario[0];
          if (base) {
            state.scenario.push({
              package_id: id, price: base.price, unit_cost: base.unit_cost,
              data_gb: base.data_gb, voice_min: base.voice_min,
              streaming: base.streaming, family: base.family, device: base.device,
            });
          }
        } else {
          state.scenario = state.scenario.filter((p) => p.package_id !== id);
        }
        state.dirty = true;
        runCompare();
      });
      const priceInput = row.querySelectorAll('input[type="number"]')[0];
      const costInput = row.querySelectorAll('input[type="number"]')[1];
      if (plan) {
        priceInput.addEventListener("change", () => {
          const value = Number(priceInput.value);
          if (!Number.isFinite(value) || value <= 0) { priceInput.value = fmtNum(plan.price, 0); return; }
          plan.price = value;
          state.dirty = true;
          runCompare();
        });
        costInput.addEventListener("change", () => {
          const value = Number(costInput.value);
          if (!Number.isFinite(value) || value < 0) { costInput.value = fmtNum(plan.unit_cost, 0); return; }
          plan.unit_cost = value;
          state.dirty = true;
          runCompare();
        });
      }
      box.appendChild(row);
    });
  }

  function attributeLine(plan) {
    const bits = [];
    if (plan.streaming) bits.push("影音");
    if (plan.family) bits.push("家庭");
    if (plan.device) bits.push("终端");
    bits.push(`语音 ${fmtNum(plan.voice_min, 0)}′`);
    return bits.join(" · ");
  }

  function renderAddPlanOptions() {
    const select = document.getElementById("add-plan");
    select.innerHTML = "";
    const inMenu = new Set(state.scenario.map((p) => p.package_id));
    state.catalog.forEach((plan) => {
      if (inMenu.has(plan.id)) return;
      const option = document.createElement("option");
      option.value = plan.id;
      option.textContent = `${plan.name}（${statusLabel(plan.status)}）`;
      select.appendChild(option);
    });
    const btn = document.getElementById("add-plan-btn");
    btn.disabled = state.model.blocked || select.options.length === 0;
    if (!btn.dataset.wired) {
      btn.dataset.wired = "1";
      btn.addEventListener("click", () => {
        const id = select.value;
        if (!id) return;
        const base = state.catalogPlan(id) || state.menu.plans.find((p) => p.package_id === id);
        if (!base) return;
        state.scenario.push({
          package_id: id, price: base.price, unit_cost: base.unit_cost,
          data_gb: base.data_gb, voice_min: base.voice_min,
          streaming: base.streaming, family: base.family, device: base.device,
        });
        state.dirty = true;
        runCompare();
      });
    }
    document.getElementById("reset-scenario").onclick = () => {
      state.scenario = state.menu.plans.map((p) => ({ ...p }));
      runCompare();
    };
  }

  function statusLabel(status) {
    return { active: "在售", retired: "已停售", candidate: "未上市" }[status] || status;
  }

  function renderScenarioComparison() {
    if (!state.compare) {
      document.getElementById("share-chart").innerHTML = "";
      document.getElementById("scenario-table").innerHTML = "";
      return;
    }
    const rows = state.compare.rows || [];
    const colorA = cssVar("--text-muted");
    const colorB = cssVar("--series-1");
    const chartRows = rows
      .filter((r) => !r.removed || r.share_before > 0)
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
      .map((r) => ({
        label: r.name,
        sub: r.removed ? "已撤下" : r.entered ? "新增" : "",
        a: r.share_before,
        b: r.share_after,
        note: `变化 ${signedPct(r.delta)}`,
      }));
    // add "none" as its own row
    const baseNone = state.compare.baseline.none;
    const scenNone = state.compare.scenario.none;
    chartRows.push({ label: "都不选", sub: "未成交", a: baseNone, b: scenNone,
      note: `变化 ${signedPct(scenNone - baseNone)}` });
    Charts.groupedBars(document.getElementById("share-chart"), {
      rows: chartRows,
      colorA, colorB,
      nameA: "基准（当前菜单）", nameB: "情景菜单",
      ariaLabel: "基准与情景菜单的份额对比",
    });
    renderScenarioTable(rows);
  }

  function renderScenarioTable(rows) {
    const wrap = document.getElementById("scenario-table");
    const table = document.createElement("table");
    table.className = "data-table compact";
    table.innerHTML = `<thead><tr><th>套餐</th><th class="num">基准份额</th>
      <th class="num">情景份额</th><th class="num">变化</th><th>说明</th></tr></thead>`;
    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      const note = row.removed ? "已撤下" : row.entered ? "新增" : "";
      const cls = row.delta > 0 ? "flag" : "";
      tr.innerHTML = `<td>${escapeHtml(row.name)}</td>
        <td class="num">${pct(row.share_before)}</td>
        <td class="num">${pct(row.share_after)}</td>
        <td class="num ${cls}">${signedPct(row.delta)}</td>
        <td class="small">${escapeHtml(note)}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.innerHTML = "";
    wrap.appendChild(table);
  }

  function renderScenarioTiles() {
    const box = document.getElementById("scenario-tiles");
    if (!state.compare) { box.innerHTML = ""; return; }
    const base = state.compare.baseline;
    const scen = state.compare.scenario;
    const tiles = [
      { label: "成交（含都不选）", value: pct(base.total_plan_share + base.none),
        note: "整组概率", plain: true },
      { label: "预计利润 / 人", value: money(base.expected_profit),
        note: "基准", plain: true },
      { label: "预计利润 / 人", value: money(scen.expected_profit),
        note: `${signedPct(state.compare.profit_delta / (base.expected_profit || 1), 1)} 相对基准`,
        cls: state.compare.profit_delta >= 0 ? "pos" : "neg" },
      { label: "预计收入 / 人", value: money(scen.expected_revenue),
        note: `${signedPct(state.compare.revenue_delta / (base.expected_revenue || 1), 1)} 相对基准`,
        cls: state.compare.revenue_delta >= 0 ? "pos" : "neg" },
      { label: "成交率", value: pct(scen.total_plan_share),
        note: `“都不选” ${signedPct(state.compare.none_delta)}`, plain: true },
      { label: "利润变化（绝对值）", value: `${state.compare.profit_delta >= 0 ? "+" : "−"}¥${fmtNum(Math.abs(state.compare.profit_delta))}`,
        note: "整组计算", cls: state.compare.profit_delta >= 0 ? "pos" : "neg" },
    ];
    box.innerHTML = "";
    tiles.forEach((tile) => {
      const div = document.createElement("div");
      div.className = "tile";
      div.innerHTML = `<div class="label">${escapeHtml(tile.label)}</div>` +
        `<div class="value">${escapeHtml(tile.value)}</div>` +
        `<div class="note ${tile.cls || ""}">${escapeHtml(tile.note)}</div>`;
      box.appendChild(div);
    });
  }

  function renderDisplacement() {
    const block = document.getElementById("displacement-chart");
    const disp = state.compare && state.compare.displacement;
    if (!disp) { block.innerHTML = ""; return; }
    const rows = [
      ...disp.removed.map((r) => ({
        label: r.name, value: -r.displaced,
        name: r.name, note: r.removed_from_menu ? "已撤下" : "份额减少",
      })),
      ...disp.destinations.map((r) => ({
        label: r.name, value: r.gained, name: r.name, note: "承接份额",
      })),
    ];
    if (disp.to_none > 0) rows.push({ label: "都不选", value: disp.to_none, name: "都不选", note: "未成交" });
    Charts.divergingChart(block, {
      rows,
      positive: cssVar("--series-1"),
      negative: cssVar("--series-2"),
      namePositive: "获得份额", nameNegative: "失去份额",
    });
    const foot = document.createElement("p");
    foot.className = "muted small";
    foot.style.marginTop = "8px";
    foot.textContent = `释放 ${pct(disp.total_displaced)} = 承接 ${pct(disp.total_gained)}` +
      (disp.balanced ? "（自动平衡）" : "（存在舍入差）");
    block.appendChild(foot);
  }

  function renderScenarioPlanDetail() {
    const picker = document.getElementById("scenario-plan-picker");
    const summary = document.getElementById("scenario-plan-summary");
    if (!state.compare) {
      picker.innerHTML = "";
      summary.innerHTML = "";
      return;
    }
    const ids = [...state.compare.scenario.plans.map((p) => p.package_id)];
    if (!ids.length) { picker.innerHTML = ""; summary.innerHTML = ""; return; }
    if (!ids.includes(state.comparePlan)) state.comparePlan = ids[0];
    picker.innerHTML = "";
    ids.forEach((id) => {
      const btn = document.createElement("button");
      btn.className = "chip" + (state.comparePlan === id ? " active" : "");
      btn.type = "button";
      btn.textContent = planName(id);
      btn.addEventListener("click", () => {
        state.comparePlan = id;
        renderScenarioPlanDetail();
      });
      picker.appendChild(btn);
    });
    const id = state.comparePlan;
    const row = state.compare.rows.find((r) => r.package_id === id);
    if (!row) { summary.innerHTML = ""; return; }
    const isRemoved = row.removed;
    const isNew = row.entered;
    const action = isRemoved ? "remove" : isNew ? "add" : "compare";
    let text;
    if (isRemoved) {
      text = `把 ${escapeHtml(row.name)} 从基准菜单撤下后，它的 ${pct(row.share_before)} 份额会重新分配。点开查看每一份去了哪里。`;
    } else if (isNew) {
      text = `${escapeHtml(row.name)} 是本次新增的选项，它从基准菜单中拿走了 ${pct(row.share_after)} 的份额。点开查看来源。`;
    } else {
      text = `${escapeHtml(row.name)} 在本次对照中被保留：份额从 ${pct(row.share_before)} 变为 ${pct(row.share_after)}（${signedPct(row.delta)}）。`;
    }
    summary.innerHTML = `
      <div class="record">
        <div class="record-head"><span>${escapeHtml(tierLabel((planInfo(id) || {}).tier || ""))}</span>
          <span class="record-choice">${pct(row.share_after)}</span></div>
        <p style="margin-top:6px">${text}</p>
      </div>`;
    const link = document.createElement("button");
    link.className = "ghost small";
    link.type = "button";
    link.textContent = "在“套餐详情”打开完整转移分析";
    link.addEventListener("click", () => {
      state.activePlan = id;
      document.querySelector('.tab[data-view="plan"]').click();
      renderPlanDetail();
    });
    summary.appendChild(link);
  }

  // ==================================================================
  // PLAN DETAIL
  // ==================================================================
  async function renderPlanDetail() {
    if (!state.model || !state.menu) return;
    const select = document.getElementById("plan-select");
    const ids = state.menu.plans.map((p) => p.package_id);
    // include non-menu plans too so a retired/candidate plan can be inspected
    const allIds = [...new Set([...ids, ...state.catalog.map((p) => p.id)])];
    select.innerHTML = "";
    allIds.forEach((id) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = planName(id) + (ids.includes(id) ? "" : "（不在当前菜单）");
      select.appendChild(option);
    });
    if (!state.activePlan || !allIds.includes(state.activePlan)) state.activePlan = ids[0] || allIds[0];
    select.value = state.activePlan;
    select.onchange = () => {
      state.activePlan = select.value;
      loadPlanDetail(select.value);
    };
    await loadPlanDetail(state.activePlan);
  }

  async function loadPlanDetail(id) {
    const key = `${state.sample}|${id}`;
    if (key === state.planLoadedKey) return;
    const token = (state.planRequest = (state.planRequest || 0) + 1);
    state.planLoadedKey = null;
    const hero = document.getElementById("plan-hero");
    const info = planInfo(id);
    hero.innerHTML = "";
    if (state.model && state.model.blocked) {
      const base = state.catalogPlan(id);
      const parts = [
        `<div class="hero-item"><span class="label">套餐</span>
          <span class="hero-name">${escapeHtml(planName(id))}</span>
          ${statusTag(info)}</div>`,
      ];
      if (base) {
        parts.push(
          `<div class="hero-item"><span class="label">列价</span>
            <span class="hero-num">¥${fmtNum(base.price, 0)}</span></div>`,
          `<div class="hero-item"><span class="label">单位成本</span>
            <span class="hero-num">¥${fmtNum(base.unit_cost, 0)}</span></div>`,
          `<div class="hero-item"><span class="label">流量 / 语音</span>
            <span class="hero-num">${fmtNum(base.data_gb, 0)}G / ${fmtNum(base.voice_min, 0)}′</span></div>`,
        );
      }
      hero.innerHTML = parts.join("") +
        `<div class="chart-empty">模型诊断未通过，转移与份额计算已停用；这里只保留套餐属性，不给出经营建议。</div>`;
      document.getElementById("plan-removal-chart").innerHTML = "";
      document.getElementById("plan-addition-chart").innerHTML = "";
      renderPlanHistory(id);
      state.planLoadedKey = key;
      return;
    }
    try {
      const result = await api("/api/substitution?sample=" + encodeURIComponent(state.sample) + "&target=" + encodeURIComponent(id));
      if (token !== state.planRequest) return; // a later request superseded this one
      const plan = result.target;
      const inMenu = state.menu.plans.some((p) => p.package_id === id);
      const inSample = result.in_sample !== false;
      hero.innerHTML = `
        <div class="hero-item"><span class="label">套餐</span>
          <span class="hero-name">${escapeHtml(plan.name)}</span>
          ${statusTag(planInfo(id))} ${inMenu ? '<span class="tag entry">当前菜单</span>' : ""}</div>
        <div class="hero-item"><span class="label">情景价</span>
          <span class="hero-num">¥${fmtNum(plan.price, 0)}</span></div>
        <div class="hero-item"><span class="label">单位成本</span>
          <span class="hero-num">¥${fmtNum(plan.unit_cost, 0)}</span>
          <span class="note">毛利 ${money(plan.margin)}</span></div>
        <div class="hero-item"><span class="label">效用</span>
          <span class="hero-num">${fmtSigned(plan.utility, 3)}</span>
          <span class="note">相对“都不选” 0</span></div>
        <div class="hero-item"><span class="label">${inMenu ? "当前菜单份额" : "加入当前菜单后份额"}</span>
          <span class="hero-num">${pct(inMenu ? state.menu.metrics.shares[id] : result.addition.gained)}</span></div>`;
      if (!inSample) {
        const note = document.createElement("p");
        note.className = "muted small";
        note.textContent = "该套餐在历史样本中从未展示，下面的份额来自模型对属性的外推，不属于历史识别结果。";
        hero.appendChild(note);
      }
      renderPlanRemoval(result);
      renderPlanAddition(result, id);
      renderPlanSensitivity(result);
      renderPlanHistory(id);
      state.planLoadedKey = key;
    } catch (error) {
      hero.innerHTML = `<div class="chart-empty">${escapeHtml(error.message)}</div>`;
      document.getElementById("plan-removal-chart").innerHTML = "";
      document.getElementById("plan-addition-chart").innerHTML = "";
    }
  }

  function renderPlanRemoval(result) {
    const block = document.getElementById("plan-removal-chart");
    const removal = result.removal;
    const inMenu = state.menu.plans.some((p) => p.package_id === result.target.package_id);
    if (!removal) { block.innerHTML = ""; return; }
    document.getElementById("plan-removal-title").textContent =
      inMenu ? "撤下它：份额去向" : "若撤下它：份额去向";
    document.getElementById("plan-removal-sub").textContent = inMenu
      ? "括号内为占释放份额的比例。"
      : "该套餐不在当前菜单；这一栏把它加入当前菜单后再撤下，作为对照。";
    const rows = removal.receivers.map((r) => ({
      label: r.name, value: r.gain, name: r.name,
      tip: [`占释放份额 ${pct(r.absorption)}`],
    }));
    if (removal.to_none > 0) {
      rows.push({ label: "都不选", value: removal.to_none, name: "都不选",
        tip: [`占释放份额 ${pct(removal.to_none_absorption)}`] });
    }
    Charts.singleBars(block, {
      rows, color: cssVar("--series-2"),
      empty: "撤下后没有可承接的剩余选项（菜单为空）。",
      ariaLabel: "撤下该套餐后份额去向",
      valueLabel: (row) => pct(row.value),
    });
    const note = document.createElement("p");
    note.className = "muted small";
    note.style.marginTop = "8px";
    note.textContent = `释放 ${pct(removal.released)}；其中 ${pct(removal.to_none_absorption)} 流向“都不选”。`;
    block.appendChild(note);
  }

  function renderPlanAddition(result, id) {
    const block = document.getElementById("plan-addition-chart");
    const add = result.addition;
    const inMenu = state.menu.plans.some((p) => p.package_id === id);
    if (!add) { block.innerHTML = ""; return; }
    document.getElementById("plan-addition-title").textContent =
      inMenu ? "加入它：份额来源" : "若加入当前菜单：份额来源";
    document.getElementById("plan-addition-sub").textContent = inMenu
      ? "从当前菜单中新增该套餐时，原有选项与“都不选”各让出多少。"
      : "该套餐不在当前菜单；按当前价格加入后，原有选项与“都不选”各让出多少。";
    const rows = add.sources.map((s) => ({
      label: s.name, value: s.loss, name: s.name,
      tip: [`贡献 ${pct(s.contribution)}`],
    }));
    if (add.from_none > 0) {
      rows.push({ label: "都不选", value: add.from_none, name: "都不选",
        tip: [`贡献 ${pct(add.from_none_contribution)}`] });
    }
    Charts.singleBars(block, {
      rows, color: cssVar("--series-1"),
      empty: "该套餐已是基准菜单唯一选项，没有其他来源。",
      ariaLabel: "加入该套餐时的份额来源",
      valueLabel: (row) => pct(row.value),
    });
    const note = document.createElement("p");
    note.className = "muted small";
    note.style.marginTop = "8px";
    note.textContent = `新增后份额 ${pct(add.gained)}；其中 ${pct(add.from_none_contribution)} 来自“都不选”。`;
    block.appendChild(note);
  }

  function renderPlanSensitivity(result) {
    const block = document.getElementById("plan-sensitivity");
    const rows = result.price_sensitivity || [];
    if (!rows.length) {
      block.innerHTML = '<div class="chart-empty">没有可用的价格敏感度。</div>';
      return;
    }
    Charts.singleBars(block, {
      rows: rows.map((row) => ({
        label: `¥${fmtNum(row.price, 0)}`,
        value: row.share,
        name: `${result.target.name} · ¥${fmtNum(row.price, 0)}`,
        tip: [
          `相对当前价 ${signedPct(row.change, 0)}`,
          `“都不选” ${pct(row.none)}`,
          `整组利润 ${money(row.expected_profit)}`,
        ],
      })),
      color: cssVar("--series-3"),
      ariaLabel: "价格变化时该套餐的份额",
      valueLabel: (row) => pct(row.value),
    });
    const zero = rows.find((row) => Math.abs(row.change) < 1e-9);
    if (zero) {
      const note = document.createElement("p");
      note.className = "muted small";
      note.style.marginTop = "8px";
      note.textContent = `当前价 ¥${fmtNum(zero.price, 0)} 时份额 ${pct(zero.share)}；` +
        "每一行都是整份菜单重算的结果，不是对单款份额的线性外推。";
      block.appendChild(note);
    }
  }

  function renderPlanHistory(id) {
    const box = document.getElementById("plan-history");
    box.innerHTML = "";
    const info = planInfo(id);
    const shown = state.model.plans.find((p) => p.package_id === id);
    if (!shown) { box.innerHTML = '<div class="chart-empty">没有样本记录。</div>'; return; }
    if (!shown.ever_shown) {
      box.innerHTML = `<div class="diag warning"><span class="diag-icon">△</span><div>
        <div class="diag-title">该套餐在样本中从未展示</div>
        <div class="diag-detail">没有任何记录包含它，因此不能按“零次成交”估计其偏好；下面的份额只来自模型对本款属性的外推。</div>
      </div></div>`;
      return;
    }
    const facts = [
      ["展示次数", fmtInt(shown.shown)],
      ["被选次数", fmtInt(shown.chosen)],
      ["历史选择率", shown.rate === null ? "—" : pct(shown.rate)],
      ["价格区间", shown.price_range ? `¥${fmtNum(shown.price_range[0], 0)}–${fmtNum(shown.price_range[1], 0)}` : "—"],
      ["状态", info ? statusLabel(info.status) : "—"],
    ];
    const dl = document.createElement("dl");
    dl.className = "facts";
    facts.forEach(([k, v]) => {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      dl.appendChild(dt); dl.appendChild(dd);
    });
    box.appendChild(dl);
    const note = document.createElement("p");
    note.className = "muted small";
    note.textContent = "以上来自历史展示记录；未展示的场合不计入分母，所以选择率不是“成交/总人数”。";
    box.appendChild(note);
  }

  // ==================================================================
  // SEARCH
  // ==================================================================
  function obsPlanIds() {
    return state.catalog.filter((p) => p.status === "active").map((p) => p.id);
  }

  function renderSearch() {
    if (!state.model || !state.model) return;
    renderCandidateList();
    renderMustInclude();
    if (state.searchResult) renderSearchResults();
  }

  // Any edit to candidates, costs or constraints invalidates the previous run:
  // showing old profits against new costs would look like the search had used them.
  function markSearchStale() {
    if (!state.searchResult) return;
    state.searchResult = null;
    state.searchSelection = null;
    document.getElementById("search-summary").innerHTML = "";
    document.querySelector("#search-results tbody").innerHTML = "";
    document.getElementById("search-compare").innerHTML = "";
    document.getElementById("search-share-chart").innerHTML = "";
    document.getElementById("search-displacement").innerHTML = "";
    document.getElementById("search-status").textContent = "候选或成本已修改，请重新搜索。";
  }

  function renderCandidateList() {
    const box = document.getElementById("candidate-list");
    box.innerHTML = "";
    const allIds = [...new Set([...obsPlanIds(), ...state.catalog.map((p) => p.id)])];
    const selected = new Set(state.candidateIds);
    allIds.forEach((id) => {
      const info = planInfo(id);
      const base = state.menu.plans.find((p) => p.package_id === id) ||
        state.catalogPlan(id);
      if (!base) return;
      if (!state.searchPrices[id]) {
        state.searchPrices[id] = { price: base.price, unit_cost: base.unit_cost };
      }
      const row = document.createElement("div");
      row.className = "candidate";
      row.innerHTML = `
        <input type="checkbox" ${selected.has(id) ? "checked" : ""} aria-label="候选 ${escapeHtml(planName(id))}" />
        <span>${escapeHtml(planName(id))} <span class="muted small">${statusLabel(info ? info.status : "")}</span></span>
        <input type="number" min="1" step="1" value="${fmtNum(state.searchPrices[id].price, 0)}" aria-label="${escapeHtml(planName(id))} 价格" />
        <input type="number" min="0" step="1" value="${fmtNum(state.searchPrices[id].unit_cost, 0)}" aria-label="${escapeHtml(planName(id))} 成本" />`;
      const [ck, priceInput, costInput] = row.querySelectorAll("input");
      ck.addEventListener("change", () => {
        if (ck.checked) {
          if (selected.size >= 10) {
            ck.checked = false;
            setStatus("search-warning", "候选套餐最多 10 个。", "warning");
            return;
          }
          selected.add(id);
        } else {
          selected.delete(id);
        }
        state.candidateIds = [...selected];
        renderMustInclude();  // drop the constraint if its plan left the candidates
        markSearchStale();
      });
      priceInput.addEventListener("change", () => {
        const value = Number(priceInput.value);
        if (!Number.isFinite(value) || value <= 0) { priceInput.value = fmtNum(state.searchPrices[id].price, 0); return; }
        state.searchPrices[id].price = value;
        markSearchStale();
      });
      costInput.addEventListener("change", () => {
        const value = Number(costInput.value);
        if (!Number.isFinite(value) || value < 0) { costInput.value = fmtNum(state.searchPrices[id].unit_cost, 0); return; }
        state.searchPrices[id].unit_cost = value;
        markSearchStale();
      });
      box.appendChild(row);
    });
    state.candidateIds = [...selected];
  }

  function renderMustInclude() {
    const select = document.getElementById("must-include");
    const current = select.value;
    const candidates = new Set(state.candidateIds);
    select.innerHTML = '<option value="">不限定</option>';
    state.catalog.filter((p) => p.is_entry).forEach((plan) => {
      const option = document.createElement("option");
      option.value = plan.id;
      const suffix = plan.status === "active" ? "" : "（不在售）";
      option.textContent = plan.name + suffix + (candidates.has(plan.id) ? "" : "（不在候选）");
      option.disabled = !candidates.has(plan.id);
      select.appendChild(option);
    });
    // a plan that is no longer a candidate cannot be required in the menu
    select.value = current && candidates.has(current) ? current : "";
    if (current && select.value !== current) {
      setStatus("search-warning",
        `“${planName(current)}”不在当前候选中，必须保留的入门档已取消。`, "warning");
    }
  }

  async function runSearch() {
    if (!state.model) return;
    if (state.model.blocked) {
      setStatus("search-warning", "模型诊断未通过，不能搜索组合。", "critical");
      return;
    }
    const ids = state.candidateIds;
    if (!ids.length) { setStatus("search-warning", "请至少选择一个候选套餐。", "warning"); return; }
    if (ids.length > 10) { setStatus("search-warning", "候选套餐最多 10 个。", "warning"); return; }
    const candidates = ids.map((id) => {
      const prices = state.searchPrices[id] || state.catalogPlan(id) || {};
      return { package_id: id, price: Number(prices.price), unit_cost: Number(prices.unit_cost) };
    });
    const maxSize = Number(document.getElementById("max-size").value) || 4;
    const minSize = Number(document.getElementById("min-size").value) || 1;
    const must = document.getElementById("must-include").value;
    setStatus("search-warning", "", "");
    document.getElementById("search-status").textContent = "搜索中…";
    try {
      const result = await api("/api/search", {
        method: "POST",
        body: JSON.stringify({
          sample: state.sample,
          candidates,
          max_size: Math.min(10, Math.max(minSize, maxSize)),
          min_size: Math.min(minSize, ids.length),
          must_include: must ? [must] : [],
          top_k: 10,
        }),
      });
      state.searchResult = result;
      state.searchSelection = result.results.length ? 0 : null;
      document.getElementById("search-status").textContent =
        `已评估 ${result.searched} 个组合（候选 ${result.candidates.length} 个）`;
      renderSearchResults();
    } catch (error) {
      document.getElementById("search-status").textContent = "";
      setStatus("search-warning", error.message, "critical");
    }
  }

  function renderSearchResults() {
    const result = state.searchResult;
    if (!result) return;
    renderSearchSummary(result);
    renderSearchTable(result);
    renderSearchCompare(result);
    renderSelectedCombination(result);
  }

  function renderSearchSummary(result) {
    const box = document.getElementById("search-summary");
    const best = result.best;
    if (!best) { box.innerHTML = ""; return; }
    box.innerHTML = `<strong>最佳组合</strong>：${best.ids.map(planName).map(escapeHtml).join("、")}
      —— 预计利润 ${money(best.expected_profit)} / 人，成交率 ${pct(1 - best.none)}。`;
  }

  function renderSearchTable(result) {
    const tbody = document.querySelector("#search-results tbody");
    tbody.innerHTML = "";
    const fixedProfit = result.fixed.metrics.expected_profit;
    result.results.forEach((row, index) => {
      const tr = document.createElement("tr");
      tr.className = "clickable" + (state.searchSelection === index ? " selected" : "");
      const delta = row.expected_profit - fixedProfit;
      tr.innerHTML = `<td>${index + 1}</td>
        <td>${row.ids.map(planName).map(escapeHtml).join("、")}</td>
        <td class="num">${row.ids.length}</td>
        <td class="num">${money(row.expected_profit)}</td>
        <td class="num">${money(row.expected_revenue)}</td>
        <td class="num">${pct(1 - row.none)}</td>
        <td class="num">${delta >= 0 ? "+" : "−"}¥${fmtNum(Math.abs(delta))}</td>`;
      tr.addEventListener("click", () => {
        state.searchSelection = index;
        renderSearchResults();
      });
      tbody.appendChild(tr);
    });
  }

  function renderSearchCompare(result) {
    const box = document.getElementById("search-compare");
    box.innerHTML = "";
    const fixed = result.fixed.metrics;
    const best = result.results[state.searchSelection || 0];
    if (!best) return;
    const rows = [
      ["菜单", "当前在售组合", best.ids.map(planName).join("、")],
      ["预计利润 / 人", money(fixed.expected_profit), money(best.expected_profit)],
      ["预计收入 / 人", money(fixed.expected_revenue), money(best.expected_revenue)],
      ["成交率", pct(1 - fixed.none), pct(1 - best.none)],
      ["“都不选”", pct(fixed.none), pct(best.none)],
      ["菜单规模", String(result.fixed.plans.length), String(best.ids.length)],
    ];
    const table = document.createElement("table");
    table.className = "data-table compact";
    table.innerHTML = "<thead><tr><th>指标</th><th class=\"num\">固定组合</th><th class=\"num\">选中组合</th></tr></thead>";
    const tbody = document.createElement("tbody");
    rows.forEach(([label, a, b]) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(label)}</td><td class="num">${escapeHtml(String(a))}</td><td class="num">${escapeHtml(String(b))}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
    const note = document.createElement("p");
    note.className = "muted small";
    note.textContent = "两个组合各自按整份菜单计算，包含“都不选”；不是把单款套餐的预测值相加。";
    box.appendChild(note);
  }

  function renderSelectedCombination(result) {
    const best = result.results[state.searchSelection || 0];
    if (!best) return;
    const fixedShares = result.fixed.metrics.shares;
    const allIds = [...new Set([...Object.keys(fixedShares), ...Object.keys(best.shares)])];
    const rows = allIds.map((id) => ({
      label: planName(id),
      a: fixedShares[id] || 0,
      b: best.shares[id] || 0,
    }));
    rows.push({ label: "都不选", a: result.fixed.metrics.none, b: best.none });
    Charts.groupedBars(document.getElementById("search-share-chart"), {
      rows,
      colorA: cssVar("--text-muted"),
      colorB: cssVar("--series-1"),
      nameA: "固定组合", nameB: "选中组合",
      ariaLabel: "固定组合与搜索结果的份额对比",
    });
    // displacement relative to fixed menu
    const fixedMap = result.fixed.plans;
    const displaced = [];
    allIds.forEach((id) => {
      const before = fixedShares[id] || 0;
      const after = best.shares[id] || 0;
      const diff = after - before;
      if (Math.abs(diff) > 1e-9) {
        displaced.push({
          label: planName(id), value: diff, name: planName(id),
          note: fixedMap.some((p) => p.package_id === id) ? "原菜单套餐" : "新增",
        });
      }
    });
    const noneDiff = best.none - result.fixed.metrics.none;
    if (Math.abs(noneDiff) > 1e-9) displaced.push({ label: "都不选", value: noneDiff, name: "都不选" });
    Charts.divergingChart(document.getElementById("search-displacement"), {
      rows: displaced,
      positive: cssVar("--series-1"),
      negative: cssVar("--series-2"),
      namePositive: "获得份额", nameNegative: "失去份额",
    });
  }

  // ------------------------------------------------------------------
  // catalog helpers
  // ------------------------------------------------------------------
  state.catalogPlan = function (id) {
    const info = state.catalog.find((p) => p.id === id);
    if (!info) return null;
    return {
      package_id: info.id, name: info.name, price: info.price,
      unit_cost: info.unit_cost, data_gb: info.data_gb, voice_min: info.voice_min,
      streaming: info.streaming, family: info.family, device: info.device,
    };
  };

  // wire search controls once
  document.addEventListener("DOMContentLoaded", () => {
    const run = document.getElementById("run-search");
    if (run) run.addEventListener("click", runSearch);
    ["must-include", "min-size", "max-size"].forEach((id) => {
      const control = document.getElementById(id);
      if (control) control.addEventListener("change", markSearchStale);
    });
  });
})();

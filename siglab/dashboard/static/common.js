(() => {
  function formatNumber(value, decimals = 2) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "n/a";
    return numeric.toFixed(decimals);
  }

  function formatPercent(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "n/a";
    return `${(numeric * 100).toFixed(2)}%`;
  }

  function formatDateTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value ?? "");
    return date.toLocaleString();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function llmIdentity(provider, model) {
    return [provider, model].filter(Boolean).join(" / ") || "n/a";
  }

  function selectedMetricKey() {
    return document.getElementById("metricFilter")?.value || "aggregate_score";
  }

  function toggleAutoRefresh(stateHolder, refreshFn, intervalMs = 10000) {
    if (stateHolder.autoRefreshTimer) {
      clearTimeout(stateHolder.autoRefreshTimer);
      stateHolder.autoRefreshTimer = null;
    }
    if (stateHolder.isRefreshing) return;
    if (document.getElementById("autoRefresh")?.checked) {
      async function refreshLoop() {
        if (stateHolder.isRefreshing) return;
        stateHolder.isRefreshing = true;
        try {
          await refreshFn();
        } catch (e) {
          // Error already handled by refreshFn or shared showError
        } finally {
          stateHolder.isRefreshing = false;
          stateHolder.autoRefreshTimer = setTimeout(refreshLoop, intervalMs);
        }
      }
      stateHolder.autoRefreshTimer = setTimeout(refreshLoop, intervalMs);
    }
  }

  function apiFetch(url, options = {}) {
    if (apiFetch._lastController) {
      apiFetch._lastController.abort();
    }
    const controller = new AbortController();
    apiFetch._lastController = controller;
    return fetch(url, { ...options, signal: controller.signal, cache: "no-store" });
  }

  function setLoading(containerId, loading) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (loading) {
      container.setAttribute("data-loading", "true");
    } else {
      container.removeAttribute("data-loading");
    }
  }

  function populateFamilyFilter(families, selectedValue, escapeFn) {
    const select = document.getElementById("familyFilter");
    if (!select) return;
    const current = selectedValue && families.includes(selectedValue) ? selectedValue : "all";
    const esc = escapeFn || escapeHtml;
    select.innerHTML = [
      "<option value=\"all\">All Families</option>",
      ...families.map(
        (family) =>
          `<option value="${esc(family)}"${family === current ? " selected" : ""}>${esc(family)}</option>`
      ),
    ].join("");
    select.value = current;
  }

  function rectNode(x, y, width, height, fill) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    element.setAttribute("x", x);
    element.setAttribute("y", y);
    element.setAttribute("width", width);
    element.setAttribute("height", height);
    element.setAttribute("fill", fill);
    return element;
  }

  function lineNode(x1, y1, x2, y2, stroke, strokeWidth) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", "line");
    element.setAttribute("x1", x1);
    element.setAttribute("y1", y1);
    element.setAttribute("x2", x2);
    element.setAttribute("y2", y2);
    element.setAttribute("stroke", stroke);
    element.setAttribute("stroke-width", strokeWidth);
    return element;
  }

  function textNode(x, y, value, fill, size, weight) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", "text");
    element.setAttribute("x", x);
    element.setAttribute("y", y);
    element.setAttribute("fill", fill);
    element.setAttribute("font-size", size);
    element.setAttribute("font-family", "Inter, -apple-system, sans-serif");
    if (weight) element.setAttribute("font-weight", weight);
    element.textContent = value;
    return element;
  }

  function historyRange(start, end) {
    if (!start && !end) return "n/a";
    return `${start || "?"} to ${end || "?"}`;
  }

  function formatSweepMaybePercent(value) {
    return Number.isFinite(value) ? formatPercent(value) : "n/a";
  }

  function joinOrNone(values) {
    return Array.isArray(values) && values.length ? values.join(", ") : "none";
  }

  function renderPolicySweepBlock(summary, family, options) {
    options = options || {};
    const heading = options.heading || "Policy Sweep";
    const winnerLabel = options.winnerLabel || "Realized Winner";
    if (!summary?.policy_sweep_comparison_available) {
      const pairFamily = String(family || "").startsWith("perp_pair_trade_");
      const message = pairFamily
        ? "This pair artifact does not have a stored declared-vs-frozen policy comparison."
        : "This family does not use the local pair-policy sweep, so there is no declared-vs-frozen comparison.";
      return `
        <div class="detail-block">
          <h3>${escapeHtml(heading)}</h3>
          <p class="detail-copy">${escapeHtml(message)}</p>
        </div>
      `;
    }
    const declared = summary.policy_sweep_declared_evaluation || {};
    const frozen = summary.policy_sweep_frozen_evaluation || {};
    return `
      <div class="detail-block">
        <h3>${escapeHtml(heading)}</h3>
        <div class="kv">
          <div class="key">Declared Score</div><div>${escapeHtml(formatNumber(declared.selector_aggregate_score, 3))}</div>
          <div class="key">Frozen Score</div><div>${escapeHtml(formatNumber(frozen.selector_aggregate_score, 3))}</div>
          <div class="key">Declared Selector Return</div><div>${escapeHtml(formatPercent(declared.selector_median_total_return ?? 0))}</div>
          <div class="key">Frozen Selector Return</div><div>${escapeHtml(formatPercent(frozen.selector_median_total_return ?? 0))}</div>
          <div class="key">Declared Pre-Audit</div><div>${escapeHtml(formatPercent(declared.pre_audit_canonical_total_return ?? 0))}</div>
          <div class="key">Frozen Pre-Audit</div><div>${escapeHtml(formatPercent(frozen.pre_audit_canonical_total_return ?? 0))}</div>
          <div class="key">Declared Validation</div><div>${escapeHtml(formatSweepMaybePercent(declared.validation_total_return))}</div>
          <div class="key">Frozen Validation</div><div>${escapeHtml(formatSweepMaybePercent(frozen.validation_total_return))}</div>
          <div class="key">${escapeHtml(winnerLabel)}</div><div>${escapeHtml(summary.policy_sweep_realized_winner || "tie")}</div>
        </div>
        <p class="detail-copy">
          Declared-better metrics: ${escapeHtml(joinOrNone(summary.policy_sweep_declared_better_metrics))}. Frozen-better metrics: ${escapeHtml(joinOrNone(summary.policy_sweep_frozen_better_metrics))}.
        </p>
      </div>
    `;
  }

  function renderSummaryCards(container, cards) {
    container.innerHTML = cards
      .map(
        (card) => `
          <article class="panel summary-card">
            <div class="label">${escapeHtml(card.label)}</div>
            <div class="value ${card.valueClass || ""}">${escapeHtml(card.value)}</div>
            <div class="detail">${escapeHtml(card.detail)}</div>
          </article>
        `
      )
      .join("");
  }

  function safeParseJson(value) {
    if (typeof value !== "string") return value;
    try {
      return JSON.parse(value);
    } catch (_error) {
      return value;
    }
  }

  function emptyChartText(message) {
    return `<text x="48" y="56" fill="#6b7f70" font-family="Inter, sans-serif">${escapeHtml(message)}</text>`;
  }

  function showError(message) {
    const toast = document.getElementById("errorToast");
    if (toast) {
      toast.textContent = message;
      toast.classList.remove("hidden");
      toast.classList.add("visible");
      setTimeout(() => {
        toast.classList.remove("visible");
        toast.classList.add("hidden");
      }, 8000);
    }
  }

  function showSkeleton(containerId, skeletonType = "card", count = 3) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = Array.from({ length: count }, () =>
      `<div class="skeleton-${skeletonType}"></div>`
    ).join("");
  }

  function buildAxisTicks(index, maxTicks) {
    if (!index?.length) return [];
    if (index.length === 1) {
      return [{ position: 0, timestamp: index[0] }];
    }
    const count = Math.max(2, Math.min(maxTicks, index.length));
    const positions = [];
    for (let step = 0; step < count; step += 1) {
      positions.push(Math.round((step * (index.length - 1)) / (count - 1)));
    }
    return [...new Set(positions)].map((position) => ({
      position,
      timestamp: index[position],
    }));
  }

  function sampleSeries(index, values, maxPoints) {
    if (values.length <= maxPoints) {
      return values.map((value, idx) => ({ index: idx, timestamp: index[idx], value }));
    }
    const step = Math.ceil(values.length / maxPoints);
    const points = [];
    for (let idx = 0; idx < values.length; idx += step) {
      points.push({ index: idx, timestamp: index[idx], value: values[idx] });
    }
    return points;
  }

  function hasFiniteSeriesValues(series) {
    return (series?.values || []).some((value) => value !== null && Number.isFinite(Number(value)));
  }

  function metricSeries(frame, columnName) {
    const columns = frame.columns || [];
    const columnIndex = columns.indexOf(columnName);
    if (columnIndex === -1) {
      return { index: [], values: [] };
    }
    return {
      index: frame.index || [],
      values: (frame.rows || []).map((row) => row[columnIndex]),
    };
  }

  function seriesMinimum(series) {
    const values = (series.values || []).filter((value) => value !== null && Number.isFinite(Number(value)));
    if (!values.length) return null;
    return Math.min(...values);
  }

  function seriesMaximum(series) {
    const values = (series.values || []).filter((value) => value !== null && Number.isFinite(Number(value)));
    if (!values.length) return null;
    return Math.max(...values);
  }

  function renderChartLegend(container, items) {
    if (!container) return;
    container.innerHTML = items
      .map(
        (item) => `
          <span class="legend-item">
            <span class="legend-swatch" style="background:${escapeHtml(item.color)}"></span>
            <span>${escapeHtml(item.label)}</span>
          </span>
        `
      )
      .join("");
  }

  function formatAxisDateTime(value) {
    if (!value) return "n/a";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }

  function populateMetricFilter(containerId, metricMeta, selectedValue) {
    const select = document.getElementById(containerId || "metricFilter");
    if (!select) return;
    const entries = Object.entries(metricMeta || window.SigLabUi?.METRIC_META || {});
    select.innerHTML = entries.map(([key, meta]) =>
      `<option value="${key}"${key === (selectedValue || "aggregate_score") ? " selected" : ""}${meta.description ? ` title="${escapeHtml(meta.description)}"` : ""}>${window.SigLabUi?.escapeHtml ? window.SigLabUi.escapeHtml(meta.label) : meta.label}</option>`
    ).join("");
  }

  function responsiveSvg(svgElement, drawCallback) {
    if (!svgElement) return;
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = entry.contentRect.width;
        if (width > 0) {
          drawCallback(width);
        }
      }
    });
    resizeObserver.observe(svgElement.parentElement || svgElement);
    const parentWidth = (svgElement.parentElement || svgElement).clientWidth;
    if (parentWidth > 0) {
      drawCallback(parentWidth);
    }
    return resizeObserver;
  }

  function initThemeToggle() {
    const toggle = document.getElementById("themeToggle");
    if (!toggle) return;
    const saved = localStorage.getItem("siglab.theme");
    if (saved === "light") {
      document.documentElement.setAttribute("data-theme", "light");
      toggle.textContent = "☀️";
      toggle.setAttribute("aria-label", "Switch to dark mode");
    }
    toggle.addEventListener("click", () => {
      const isLight = document.documentElement.getAttribute("data-theme") === "light";
      if (isLight) {
        document.documentElement.removeAttribute("data-theme");
        localStorage.setItem("siglab.theme", "dark");
        toggle.textContent = "🌙";
        toggle.setAttribute("aria-label", "Switch to light mode");
      } else {
        document.documentElement.setAttribute("data-theme", "light");
        localStorage.setItem("siglab.theme", "light");
        toggle.textContent = "☀️";
        toggle.setAttribute("aria-label", "Switch to dark mode");
      }
    });
  }

  function showOnboarding() {
    if (sessionStorage.getItem("siglab.onboarding.seen")) return;

    const banner = document.createElement("div");
    banner.id = "onboardingBanner";
    banner.className = "onboarding-banner";
    banner.setAttribute("role", "dialog");
    banner.setAttribute("aria-label", "Welcome to SigLab");
    banner.innerHTML = `
      <div class="onboarding-content">
        <h2>Welcome to SigLab</h2>
        <div class="onboarding-step" data-step="1">
          <p><strong>Dashboard</strong> shows research experiments grouped by <strong>track</strong> (Directional Perps, Systematic Carry) and <strong>family</strong> (specific strategy templates).</p>
        </div>
        <div class="onboarding-step" data-step="2" style="display:none">
          <p>Click a <strong>run card</strong> to see its experiments, or a <strong>chart point</strong> to inspect an experiment's detail page.</p>
        </div>
        <div class="onboarding-step" data-step="3" style="display:none">
          <p>Use the <strong>filter controls</strong> to narrow by track, family, or metric. Enable <strong>Auto refresh</strong> for live updates.</p>
        </div>
        <div class="onboarding-nav">
          <button id="onboardingPrev" style="display:none">Back</button>
          <span id="onboardingStepIndicator">1 / 3</span>
          <button id="onboardingNext">Next</button>
          <button id="onboardingDismiss">Skip</button>
        </div>
      </div>
    `;
    document.body.appendChild(banner);

    let currentStep = 1;
    const totalSteps = 3;

    const updateStep = () => {
      document.querySelectorAll(".onboarding-step").forEach((el, i) => {
        el.style.display = (i + 1) === currentStep ? "block" : "none";
      });
      document.getElementById("onboardingPrev").style.display = currentStep === 1 ? "none" : "inline-block";
      document.getElementById("onboardingNext").textContent = currentStep === totalSteps ? "Done" : "Next";
      document.getElementById("onboardingStepIndicator").textContent = `${currentStep} / ${totalSteps}`;
    };

    document.getElementById("onboardingNext").addEventListener("click", () => {
      if (currentStep < totalSteps) { currentStep++; updateStep(); }
      else { banner.remove(); sessionStorage.setItem("siglab.onboarding.seen", "1"); }
    });
    document.getElementById("onboardingPrev").addEventListener("click", () => {
      if (currentStep > 1) { currentStep--; updateStep(); }
    });
    document.getElementById("onboardingDismiss").addEventListener("click", () => {
      banner.remove(); sessionStorage.setItem("siglab.onboarding.seen", "1");
    });
  }

  function initAriaLive() {
    const liveRegions = [
      "summaryCards", "runCards", "experimentsTable", "detailContent",
      "experimentSummary", "experimentSnapshot", "deploymentPanel",
      "assetActionCharts", "positionHeatmap", "tradesTable",
      "opsSummary", "artifactHealth", "waveState", "buildathonProof",
      "marketState", "sodexBoundary", "telemetryState", "blockers"
    ];
    liveRegions.forEach((id) => {
      const el = document.getElementById(id);
      if (el && !el.hasAttribute("aria-live")) {
        el.setAttribute("aria-live", "polite");
      }
    });
  }

  window.SigLabUi = {
    formatNumber,
    formatPercent,
    formatDateTime,
    escapeHtml,
    llmIdentity,
    selectedMetricKey,
    toggleAutoRefresh,
    apiFetch,
    setLoading,
    populateFamilyFilter,
    rectNode,
    lineNode,
    textNode,
    historyRange,
    formatSweepMaybePercent,
    joinOrNone,
    renderPolicySweepBlock,
    renderSummaryCards,
    safeParseJson,
    emptyChartText,
    showError,
    showSkeleton,
    buildAxisTicks,
    sampleSeries,
    hasFiniteSeriesValues,
    metricSeries,
    seriesMinimum,
    seriesMaximum,
    renderChartLegend,
    formatAxisDateTime,
    populateMetricFilter,
    responsiveSvg,
    showOnboarding,
    initAriaLive,
    initThemeToggle,
  };
})();

/* ─── Mobile Hamburger Toggle ─── */
document.addEventListener("click", (event) => {
  const toggle = document.getElementById("navbarToggle");
  const nav = document.querySelector(".navbar-nav");
  if (toggle && nav && (event.target === toggle || toggle.contains(event.target))) {
    nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", nav.classList.contains("open"));
  }
});

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

  function populateFamilyFilter(families, selectedValue, escapeFn, counts) {
    const select = document.getElementById("familyFilter");
    if (!select) return;
    const current = selectedValue && families.includes(selectedValue) ? selectedValue : "all";
    const esc = escapeFn || escapeHtml;
    select.innerHTML = [
      "<option value=\"all\">All Families</option>",
      ...families.map(
        (family) => {
          const count = counts?.[family] || 0;
          const label = count > 0 ? `${esc(family)} (${count})` : esc(family);
          return `<option value="${esc(family)}"${family === current ? " selected" : ""}>${label}</option>`;
        }
      ),
    ].join("");
    select.value = current;
  }

  // rectNode, lineNode, textNode moved to chart-engine.js

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
        (card) => {
          const description = card.description || "";
          const titleAttr = description ? ` title="${escapeHtml(description)}"` : "";
          return `
          <article class="panel summary-card">
            <div class="label"${titleAttr}>${escapeHtml(card.label)}</div>
            <div class="value ${card.valueClass || ""}">${escapeHtml(card.value)}</div>
            <div class="detail">${escapeHtml(card.detail)}</div>
          </article>
        `;
        }
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

  // emptyChartText moved to chart-engine.js

  function showError(message, retryFn) {
    const toast = document.getElementById("errorToast");
    if (toast) {
      const retryBtn = retryFn ? `<button class="toast-retry-btn" onclick="this.parentElement.classList.add('hidden')">Retry</button>` : "";
      toast.innerHTML = `<span>${escapeHtml(message)}</span>${retryBtn}`;
      toast.classList.remove("hidden");
      toast.classList.add("visible");
      if (retryFn) {
        toast.querySelector(".toast-retry-btn")?.addEventListener("click", () => {
          toast.classList.remove("visible");
          toast.classList.add("hidden");
          retryFn();
        });
      }
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

  // renderChartLegend moved to chart-engine.js

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

  // responsiveSvg moved to chart-engine.js

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

  Object.assign(window.SigLabUi, {
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
    // rectNode, lineNode, textNode moved to chart-engine.js
    historyRange,
    formatSweepMaybePercent,
    joinOrNone,
    renderPolicySweepBlock,
    renderSummaryCards,
    safeParseJson,
    // emptyChartText moved to chart-engine.js
    showError,
    showSkeleton,
    buildAxisTicks,
    sampleSeries,
    hasFiniteSeriesValues,
    metricSeries,
    seriesMinimum,
    seriesMaximum,
    // renderChartLegend moved to chart-engine.js
    formatAxisDateTime,
    populateMetricFilter,
    // responsiveSvg moved to chart-engine.js
    showOnboarding,
    initAriaLive,
    initThemeToggle,
  });
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

/* ─── Command Palette (Cmd+K) ─── */
(() => {
  const dialog = document.getElementById("commandPalette");
  const input = document.getElementById("commandPaletteInput");
  const results = document.getElementById("commandPaletteResults");
  if (!dialog || !input || !results) return;

  let activeIndex = -1;
  let searchTimeout = null;

  function openPalette() {
    dialog.showModal();
    input.value = "";
    results.innerHTML = '<div class="command-palette-empty">Type to search...</div>';
    activeIndex = -1;
    requestAnimationFrame(() => input.focus());
  }

  function closePalette() {
    dialog.close();
    input.value = "";
  }

  function renderResults(items) {
    if (!items.length) {
      results.innerHTML = '<div class="command-palette-empty">No results found</div>';
      return;
    }
    results.innerHTML = items.map((item, i) => `
      <div class="command-palette-item${i === activeIndex ? ' active' : ''}"
           data-url="${item.url}" data-type="${item.type}" data-index="${i}">
        <span class="command-palette-item-icon">${item.icon}</span>
        <div class="command-palette-item-content">
          <div class="command-palette-item-title">${escapeHtml(item.title)}</div>
          <div class="command-palette-item-subtitle">${escapeHtml(item.subtitle)}</div>
        </div>
        <span class="command-palette-item-type">${item.type}</span>
      </div>
    `).join("");
  }

  async function search(query) {
    if (!query.trim()) {
      results.innerHTML = '<div class="command-palette-empty">Type to search...</div>';
      return;
    }
    try {
      const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=10`);
      const data = await resp.json();
      activeIndex = data.results.length > 0 ? 0 : -1;
      renderResults(data.results);
    } catch {
      results.innerHTML = '<div class="command-palette-empty">Search failed</div>';
    }
  }

  function navigateResults(direction) {
    const items = results.querySelectorAll(".command-palette-item");
    if (!items.length) return;
    items[activeIndex]?.classList.remove("active");
    activeIndex = (activeIndex + direction + items.length) % items.length;
    items[activeIndex]?.classList.add("active");
    items[activeIndex]?.scrollIntoView({ block: "nearest" });
  }

  function selectResult() {
    const item = results.querySelectorAll(".command-palette-item")[activeIndex];
    if (!item) return;
    const url = item.dataset.url;
    const type = item.dataset.type;
    if (type === "action" && url === "#") {
      // Handle special actions
      const title = item.querySelector(".command-palette-item-title")?.textContent;
      if (title === "Refresh Data") {
        window.location.reload();
      } else if (title === "Toggle Theme") {
        document.getElementById("themeToggle")?.click();
      } else if (title === "Help & Documentation") {
        document.getElementById("helpModal")?.showModal();
      }
    } else if (url && url !== "#") {
      // Export actions open in new tab
      if (url.includes("/api/export/")) {
        window.open(url, "_blank");
      } else {
        window.location.href = url;
      }
    }
    closePalette();
  }

  // Keyboard shortcut: Cmd+K or Ctrl+K
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      if (dialog.open) {
        closePalette();
      } else {
        openPalette();
      }
    }
    if (e.key === "Escape" && dialog.open) {
      e.preventDefault();
      closePalette();
    }
  });

  // Additional keyboard shortcuts (only when command palette is closed)
  document.addEventListener("keydown", (e) => {
    // Don't handle shortcuts when palette is open or in an input
    if (dialog.open) return;
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

    // R = Refresh
    if (e.key === "r" && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      const refreshBtn = document.getElementById("refreshButton");
      if (refreshBtn) refreshBtn.click();
    }

    // / = Open command palette (alternative to Cmd+K)
    if (e.key === "/") {
      e.preventDefault();
      openPalette();
    }

    // 1-7 = Focus ops panels (only on ops page)
    if (e.key >= "1" && e.key <= "7" && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const panels = document.querySelectorAll(".ops-panel");
      const index = parseInt(e.key, 10) - 1;
      if (panels[index]) {
        e.preventDefault();
        panels[index].scrollIntoView({ behavior: "smooth", block: "start" });
        panels[index].focus();
      }
    }

    // Escape = Go back (if not in dialog)
    if (e.key === "Escape") {
      const backLink = document.getElementById("backLink");
      if (backLink) {
        e.preventDefault();
        backLink.click();
      }
    }
  });

  // Click on trigger button
  const trigger = document.getElementById("commandPaletteTrigger");
  if (trigger) {
    trigger.addEventListener("click", (e) => {
      e.preventDefault();
      if (dialog.open) {
        closePalette();
      } else {
        openPalette();
      }
    });
  }

  // Input handling
  input.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => search(input.value), 150);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      navigateResults(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      navigateResults(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectResult();
    }
  });

  // Click on result
  results.addEventListener("click", (e) => {
    const item = e.target.closest(".command-palette-item");
    if (item) {
      activeIndex = parseInt(item.dataset.index, 10);
      selectResult();
    }
  });

  // Click on backdrop to close
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog || e.target.classList.contains("command-palette-backdrop")) {
      closePalette();
    }
  });
})();

/* ─── Help Modal (?) ─── */
(() => {
  const helpDialog = document.getElementById("helpModal");
  const helpClose = document.getElementById("helpModalClose");
  if (!helpDialog || !helpClose) return;

  function openHelp() {
    helpDialog.showModal();
  }

  function closeHelp() {
    helpDialog.close();
  }

  // Keyboard shortcut: ? or H (when not in input)
  document.addEventListener("keydown", (e) => {
    if (helpDialog.open) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeHelp();
      }
      return;
    }
    // Don't handle shortcuts when in an input
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

    if (e.key === "?" || (e.key === "h" && !e.metaKey && !e.ctrlKey && !e.altKey)) {
      e.preventDefault();
      openHelp();
    }
  });

  // Close button
  helpClose.addEventListener("click", closeHelp);

  // Help trigger button in navbar
  const helpTrigger = document.getElementById("helpTrigger");
  if (helpTrigger) {
    helpTrigger.addEventListener("click", () => {
      if (helpDialog.open) {
        closeHelp();
      } else {
        openHelp();
      }
    });
  }

  // Click on backdrop to close
  helpDialog.addEventListener("click", (e) => {
    if (e.target === helpDialog || e.target.classList.contains("help-modal-backdrop")) {
      closeHelp();
    }
  });
})();

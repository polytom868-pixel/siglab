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

  window.SigLabUi = {
    TRACK_LABELS: {
      trend_signals: "Directional Perps",
      yield_flows: "Systematic Carry",
    },

    METRIC_META: {
      aggregate_score: {
        label: "Aggregate Score",
        formatter: (value) => formatNumber(value, 3),
        description: "Primary selector metric, computed from the evaluator's selector windows.",
      },
      median_sharpe: {
        label: "Median Sharpe",
        formatter: (value) => formatNumber(value, 3),
        description: "Median Sharpe across the selector windows.",
      },
      median_cagr: {
        label: "Median CAGR",
        formatter: (value) => formatPercent(value),
        description: "Median annualized return across the selector windows.",
      },
      median_total_return: {
        label: "Median Return",
        formatter: (value) => formatPercent(value),
        description: "Median total return across the selector windows.",
      },
      pre_audit_canonical_total_return: {
        label: "Pre-Audit Return",
        formatter: (value) => formatPercent(value),
        description: "Canonical total return measured only up to the audit boundary.",
      },
      median_calmar: {
        label: "Median Calmar",
        formatter: (value) => formatNumber(value, 3),
        description: "Median Calmar across the selector windows.",
      },
      validation_total_return: {
        label: "Validation Return",
        formatter: (value) => formatPercent(value),
        description: "Out-of-sample total return across the validation slices used during selection.",
      },
      validation_sharpe: {
        label: "Validation Sharpe",
        formatter: (value) => formatNumber(value, 3),
        description: "Out-of-sample Sharpe across the validation slices used during selection.",
      },
      validation_cagr: {
        label: "Validation CAGR",
        formatter: (value) => formatPercent(value),
        description: "Out-of-sample annualized return across the validation slices used during selection.",
      },
      audit_total_return: {
        label: "Audit Return",
        formatter: (value) => formatPercent(value),
        description: "Final untouched out-of-sample total return on the audit slice.",
      },
      audit_sharpe: {
        label: "Audit Sharpe",
        formatter: (value) => formatNumber(value, 3),
        description: "Final untouched out-of-sample Sharpe on the audit slice.",
      },
      audit_cagr: {
        label: "Audit CAGR",
        formatter: (value) => formatPercent(value),
        description: "Final untouched out-of-sample annualized return on the audit slice.",
      },
    },

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
    safeParseJson,
    emptyChartText,
    showError,
    showSkeleton,
  };
})();

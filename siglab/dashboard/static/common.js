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

  window.SigLabUi = {
    TRACK_LABELS: {
      trend_signals: "Directional Perps",
      yield_flows: "Systematic Carry",
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
  };
})();



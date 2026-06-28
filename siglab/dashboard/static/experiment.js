;(function experimentInit() {
const PAGE_STATE = {
  payload: null,
  displayCapitalUsd: null,
  isRefreshing: false,
  lastUpdatedTimestamp: null,
  tradePage: 1,
};

const DEFAULT_DISPLAY_CAPITAL_USD = 100000;
const DISPLAY_CAPITAL_STORAGE_KEY = "siglab.displayCapitalUsd";
const { TRACK_LABELS, formatDateTime, formatNumber, formatPercent, escapeHtml,
  historyRange, formatSweepMaybePercent,
  safeParseJson, apiFetch,
  renderPolicySweepBlock,
  hasFiniteSeriesValues, metricSeries, renderChartLegend, formatAxisDateTime,
  initThemeToggle, initAriaLive,
  // Chart functions (from chart-engine.js)
  COLORS,
  rectNode, lineNode, textNode, emptyChartText,
  drawLineChart, renderHeatmap, renderAssetActionCharts, renderTrades,
  pillRow, latestWeightMap, moveTooltip,
  assetActionSvg, renderAssetActionCard, tradeMarkerSvg,
  annotateTrades, groupTradesBySymbol, positionStateLabel, positionBandColor,
  weightSign, weightColor, buildWeightSegments, classifyTradeAction,
  canonicalOutcomeDecomposition, sumSeriesValues } = window.SigLabUi;

document.addEventListener("DOMContentLoaded", async () => {
  initAriaLive();
  initThemeToggle();
  PAGE_STATE.displayCapitalUsd = loadDisplayCapitalUsd();
  bindDisplayCapitalInput();
  const specHash = getSpecHash();
  if (!specHash) {
    renderMissing("No experiment hash was found in the URL.");
    return;
  }

  const response = await apiFetch(`/api/experiments/${encodeURIComponent(specHash)}/series`);
  if (!response.ok) {
    renderMissing("Unable to load experiment data.");
    return;
  }

  try {
    PAGE_STATE.payload = await response.json();
  } catch (error) {
    renderMissing(`Failed to parse experiment data: ${error.message}`);
    return;
  }
  PAGE_STATE.lastUpdatedTimestamp = Date.now();
  updateFreshnessIndicator();
  renderPage();
  setInterval(updateFreshnessIndicator, 1000);
});

function renderPage() {
  const payload = PAGE_STATE.payload || {};
  const experiment = payload.experiment || {};
  const run = payload.canonical_run || null;

  const backLink = document.getElementById("backLink");
  if (backLink) {
    if (experiment.run_session_id) {
      backLink.href = `/runs/${encodeURIComponent(experiment.run_session_id)}`;
      backLink.textContent = "Back to Run";
    } else {
      backLink.href = "/";
      backLink.textContent = "Back to Dashboard";
    }
  }
  document.title = (experiment.family || "Experiment").trim();
  document.getElementById("experimentTitle").textContent = (experiment.family || "Experiment").trim();
  document.getElementById("experimentSubtitle").textContent =
    `${TRACK_LABELS[experiment.track] || experiment.track || "Unknown Track"} • created ${formatDateTime(experiment.created_at)}`;

  renderSummary(experiment, run, payload.series_available);
  renderSnapshot(experiment, run, payload.series_available, payload.compiled_metadata || {});
  renderDeployment(experiment);

  if (!payload.series_available || !run) {
    renderUnavailable();
    return;
  }

  renderEquityChart(run);
  renderMetricsChart(run);
  renderAssetActionCharts(run);
  renderHeatmap(run);
  renderTrades(run.trades || []);
}

function renderScaledValues() {
  const payload = PAGE_STATE.payload || {};
  const run = payload.canonical_run || null;
  if (!run) return;
  const outcome = canonicalOutcomeDecomposition(run);
  const displayCapital = getDisplayCapitalUsd();
  const outcomeCards = document.querySelectorAll(".summary-card");
  if (outcomeCards.length >= 9) {
    outcomeCards[6].querySelector(".value").textContent = outcome ? formatPercent(outcome.priceOutcome) : "n/a";
    outcomeCards[6].querySelector(".detail").textContent = outcome
      ? `Mark-to-market outcome... At ${formatUsd(displayCapital)}, about ${formatSignedUsd(outcome.priceOutcome * displayCapital)}.`
      : "...";
    outcomeCards[7].querySelector(".value").textContent = outcome ? formatPercent(outcome.carryOutcome) : "n/a";
    outcomeCards[7].querySelector(".detail").textContent = outcome
      ? `Funding carry outcome... At ${formatUsd(displayCapital)}, about ${formatSignedUsd(outcome.carryOutcome * displayCapital)}.`
      : "...";
    outcomeCards[8].querySelector(".value").textContent = outcome ? formatSignedUsd(outcome.txCost) : "n/a";
    outcomeCards[8].querySelector(".detail").textContent = outcome
      ? `Total transaction cost... At ${formatUsd(displayCapital)}, about ${formatSignedUsd(outcome.txCost * displayCapital)}.`
      : "...";
  }
  renderTrades(run.trades || []);
}

function updateFreshnessIndicator() {
  const el = document.getElementById("freshnessIndicator");
  if (!el) return;
  if (!PAGE_STATE.lastUpdatedTimestamp) {
    el.textContent = "";
    return;
  }
  const seconds = Math.floor((Date.now() - PAGE_STATE.lastUpdatedTimestamp) / 1000);
  el.textContent = `Updated ${seconds}s ago`;
  el.className = "freshness-indicator" + (seconds > 30 ? " stale" : "");
}

function renderMissing(message) {
  document.getElementById("experimentSummary").innerHTML = `
    <article class="panel summary-card">
      <div class="label">Unavailable</div>
      <div class="value small">No Data</div>
      <div class="detail">${escapeHtml(message)}</div>
    </article>
  `;
  document.getElementById("experimentSnapshot").innerHTML = `<p class="empty-state">${escapeHtml(message)}</p>`;
  document.getElementById("positionHeatmap").innerHTML = `<p class="empty-state">${escapeHtml(message)}</p>`;
  document.getElementById("tradesTable").innerHTML = "";
}

function renderUnavailable() {
  const message =
    "This experiment predates full-series retention. Re-run the experiment to capture the equity curve and position timeline.";
  document.getElementById("equityChart").innerHTML = emptyChartText(message);
  document.getElementById("metricsChart").innerHTML = emptyChartText(message);
  document.getElementById("metricsLegend").innerHTML = "";
  document.getElementById("assetActionCharts").innerHTML = `<p class="empty-state">${escapeHtml(message)}</p>`;
  document.getElementById("positionHeatmap").innerHTML = `<p class="empty-state">${escapeHtml(message)}</p>`;
  document.getElementById("tradesTable").innerHTML = "";
  document.getElementById("tradeSubtitle").textContent = message;
}

function renderDeployment(experiment) {
  const panel = document.getElementById("deploymentPanel");
  const readiness = experiment.deployment_readiness || { supported: false, reasons: [], warnings: [] };
  const deployment = experiment.live_deployment || null;
  const reasons = readiness.reasons || [];
  const warnings = readiness.warnings || [];
  const deploydMarkup = deployment
    ? `
      <div class="detail-block">
        <h3>Latest Deployment</h3>
        <div class="kv">
          <div class="key">Strategy</div><div>${escapeHtml(deployment.strategy_name || "n/a")}</div>
          <div class="key">Scheduled</div><div>${deployment.scheduled ? "yes" : "no"}</div>
          <div class="key">Dry Run</div><div>${deployment.dry_run ? "yes" : "no"}</div>
          <div class="key">Job</div><div>${escapeHtml(deployment.job_name || "n/a")}</div>
          <div class="key">Wallet</div><div>${escapeHtml(deployment.wallet_label || "n/a")}</div>
          <div class="key">Spec</div><div class="mono">${escapeHtml((deployment.spec_path || '').split('/').pop() || "n/a")}</div>
      </div>
    `
    : "";

  const unsupportedMarkup = !readiness.supported
    ? `
      <div class="detail-block">
        <h3>Not Exportable Yet</h3>
        <p class="detail-copy">${reasons.map(escapeHtml).join(" ") || "This family is not live-supported yet."}</p>
      </div>
    `
    : `
      <form id="deploymentForm" class="deployment-form">
        <div class="filters deployment-grid">
          <label for="deploymentWalletLabel">Wallet Label</label>
          <input id="deploymentWalletLabel" type="text" placeholder="basis_trading_strategy" />
          <label for="deploymentJobName">Job Name</label>
          <input id="deploymentJobName" type="text" placeholder="siglab-job-name" />
          <label for="deploymentInterval">Interval Seconds</label>
          <input id="deploymentInterval" type="number" min="60" step="60" value="600" />
          <label for="deploymentConfigPath">Config Path</label>
          <input id="deploymentConfigPath" type="text" placeholder="use default config" />
          <label class="checkbox">
            <input id="deploymentSchedule" type="checkbox" />
            Schedule runner job
          </label>
          <label class="checkbox">
            <input id="deploymentLive" type="checkbox" />
            Live trading
          </label>
          <label class="checkbox">
            <input id="deploymentLlmFinalize" type="checkbox" />
            Claude finalize notes
          </label>
          <button type="submit" aria-label="Deploy experiment">Deploy</button>
        </div>
      </form>
      <div id="deploymentResult" class="detail-copy"></div>
    `;

  panel.innerHTML = `
    <div class="detail-grid">
      <div class="detail-block">
        <h3>Readiness</h3>
        <div class="kv">
          <div class="key">Supported</div><div>${readiness.supported ? "yes" : "no"}</div>
          <div class="key">Family</div><div>${escapeHtml(experiment.family || "n/a")}</div>
          <div class="key">Track</div><div>${escapeHtml(experiment.track_label || TRACK_LABELS[experiment.track] || experiment.track || "n/a")}</div>
        </div>
        ${warnings.length ? `<p class="detail-copy">${warnings.map(escapeHtml).join(" ")}</p>` : ""}
      </div>
      ${deploydMarkup}
      ${unsupportedMarkup}
    </div>
  `;

  const form = document.getElementById("deploymentForm");
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await submitDeployment(experiment.spec_hash);
    });
  }
}

function validateDeploymentInput() {
  /* Client-side validation for the deployment form.
     Returns an error string or null if all fields pass. */
  const walletLabel = document.getElementById("deploymentWalletLabel")?.value || "";
  const jobName = document.getElementById("deploymentJobName")?.value || "";
  const intervalRaw = document.getElementById("deploymentInterval")?.value || "";
  const configPath = document.getElementById("deploymentConfigPath")?.value || "";

  // wallet_label: max 64 chars, alphanumeric + underscores
  if (walletLabel.length > 64) {
    return "Wallet label must be at most 64 characters.";
  }
  if (walletLabel && !/^[a-zA-Z0-9_]+$/.test(walletLabel)) {
    return "Wallet label must contain only letters, digits, and underscores.";
  }

  if (jobName.length > 64) {
    return "Job name must be at most 64 characters.";
  }
  if (jobName && !/^[a-zA-Z0-9-]+$/.test(jobName)) {
    return "Job name must contain only letters, digits, and hyphens.";
  }

  // interval_seconds: 60 to 86400
  const interval = parseInt(intervalRaw, 10);
  if (intervalRaw && (isNaN(interval) || interval < 60 || interval > 86400)) {
    return "Interval must be between 60 and 86400 seconds.";
  }

  // config_path: max 256 chars, no path traversal
  if (configPath.length > 256) {
    return "Config path must be at most 256 characters.";
  }
  if (configPath && (configPath.includes("..") || configPath.startsWith("/"))) {
    return "Config path must not contain path traversal sequences.";
  }

  return null;
}

async function submitDeployment(specHash) {
  const resultNode = document.getElementById("deploymentResult");
  if (resultNode) {
    resultNode.setAttribute("aria-live", "polite");
    resultNode.textContent = "Promoting...";
  }

  const validationError = validateDeploymentInput();
  if (validationError) {
    if (resultNode) {
      resultNode.textContent = validationError;
    }
    return;
  }

  const payload = {
    wallet_label: document.getElementById("deploymentWalletLabel")?.value || null,
    job_name: document.getElementById("deploymentJobName")?.value || null,
    interval_seconds: parseOptionalNumber(document.getElementById("deploymentInterval")?.value),
    config_path: document.getElementById("deploymentConfigPath")?.value || null,
    schedule: Boolean(document.getElementById("deploymentSchedule")?.checked),
    llm_finalize: Boolean(document.getElementById("deploymentLlmFinalize")?.checked),
    dry_run: !Boolean(document.getElementById("deploymentLive")?.checked),
  };
  try {
    const response = await apiFetch(`/api/experiments/${encodeURIComponent(specHash)}/deploy`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const data = await response.json();
    if (resultNode) {
      resultNode.textContent = `Deployed as ${data.deployment?.strategy_name || "generated strategy"}.`;
    }
    const refreshed = await apiFetch(`/api/experiments/${encodeURIComponent(specHash)}/series`);
    if (refreshed.ok) {
      PAGE_STATE.payload = await refreshed.json();
      renderPage();
    } else {
      if (window.SigLabUi?.showError) {
        window.SigLabUi.showError("Deploy succeeded but page refresh failed. Reload to see latest data.");
      }
    }
  } catch (error) {
    if (resultNode) {
      resultNode.textContent = "Deployment failed. Please try again.";
    }
  }
}

function renderSummary(experiment, run, seriesAvailable) {
  const summary = experiment.summary || {};
  const tradeCount = run?.trade_count ?? 0;
  const cards = [
    {
      label: "Aggregate Score",
      value: formatNumber(summary.aggregate_score, 3),
      detail: "Primary selection metric used for deployment.",
    },
    {
      label: "Median Sharpe",
      value: formatNumber(summary.median_sharpe, 3),
      detail: "Median walk-forward Sharpe across evaluated windows.",
    },
    {
      label: "Median CAGR",
      value: formatPercent(summary.median_cagr ?? 0),
      detail: "Annualized median return across evaluated windows.",
    },
    {
      label: "Pre-Audit Return",
      value: formatPercent(summary.pre_audit_canonical_total_return ?? 0),
      detail: "Total return measured only up to the audit boundary.",
    },
    {
      label: "Audit Return",
      value: summary.audit_available ? formatPercent(summary.audit_total_return ?? 0) : "n/a",
      detail: "Final untouched out-of-sample total return on the audit slice.",
    },
    {
      label: "Total Trades",
      value: `${tradeCount}`,
      detail: seriesAvailable
        ? "Trade count on the full-run timeline available for this experiment."
        : "Full-run trade tape not available for this experiment.",
    },
  ];

  let html = cards
    .map(
      (card) => `
        <article class="panel summary-card">
          <div class="label">${escapeHtml(card.label)}</div>
          <div class="value">${escapeHtml(card.value)}</div>
          <div class="detail">${escapeHtml(card.detail)}</div>
        </article>
      `
    )
    .join("");

  if (summary.policy_sweep_comparison_available) {
    const declared = formatNumber(summary.policy_sweep_declared_evaluation?.selector_aggregate_score, 3);
    const frozen = formatNumber(summary.policy_sweep_frozen_evaluation?.selector_aggregate_score, 3);
    html += `
      <div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:11px;color:var(--muted);">
        <span class="pill slate">Declared: ${escapeHtml(declared)}</span>
        <span class="pill slate">Frozen: ${escapeHtml(frozen)}</span>
        <span>Policy sweep — unswept vs swept score</span>
      </div>
    `;
  }

  document.getElementById("experimentSummary").innerHTML = html;
}

function renderSnapshot(experiment, run, seriesAvailable, compiledMetadata) {
  const snapshot = document.getElementById("experimentSnapshot");
  const summary = experiment.summary || {};
  const latestWeights = seriesAvailable
    ? Object.fromEntries(
        Object.entries(latestWeightMap(run.target_weights) || {}).filter(([asset]) => asset !== "GLOBAL")
      )
    : {};
  const visualSplit = run?.visual_split || { ranges: [], note: "" };
  const latestLongs = Object.entries(latestWeights)
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const latestShorts = Object.entries(latestWeights)
    .filter(([, value]) => value < 0)
    .sort((a, b) => a[1] - b[1])
    .slice(0, 6);

  snapshot.innerHTML = `
    <div class="detail-grid">
      <div class="detail-block">
        <h3>Run</h3>
        <div class="kv">
          <div class="key">Track</div><div>${escapeHtml(experiment.track_label || TRACK_LABELS[experiment.track] || experiment.track || "unknown")}</div>
          <div class="key">Source</div><div>${escapeHtml(experiment.source || compiledMetadata.source || "unknown")}</div>
          <div class="key">Series</div><div>${seriesAvailable ? "available" : "missing"}</div>
          <div class="key">File</div><div class="mono">${escapeHtml((experiment.artifact_path || '').split('/').pop() || "n/a")}</div>
      </div>
      <div class="detail-block">
        <h3>Evaluation Split</h3>
        <div class="kv">
          <div class="key">Strict Audit</div><div>${visualSplit.strict_holdout ? "yes" : "no"}</div>
          ${(visualSplit.ranges || []).map((range) => `
            <div class="key">${escapeHtml(range.label)}</div><div>${escapeHtml(historyRange(range.start_timestamp, range.end_timestamp))}</div>
          `).join("")}
          <div class="key">Validation</div><div>${escapeHtml(outOfSampleMetric(experiment.summary || {}, "validation"))}</div>
          <div class="key">Audit</div><div>${escapeHtml(outOfSampleMetric(experiment.summary || {}, "audit"))}</div>
        </div>
        <p class="detail-copy">${escapeHtml(visualSplit.note || "No split metadata recorded.")}</p>
      </div>
      ${renderPolicySweepBlock(summary, experiment.family, { heading: "Policy Sweep", winnerLabel: "Realized Winner" })}
      <div class="detail-block">
        <h3>Latest Longs</h3>
        ${latestLongs.length ? pillRow(latestLongs, "long") : '<p class="empty-state">No long exposure at the latest retained timestamp.</p>'}
      </div>
      <div class="detail-block">
        <h3>Latest Shorts</h3>
        ${latestShorts.length ? pillRow(latestShorts, "short") : '<p class="empty-state">No short exposure at the latest retained timestamp.</p>'}
      </div>
    </div>
  `;
}



function renderEquityChart(run) {
  const svg = document.getElementById("equityChart");
  const tooltip = document.getElementById("pageTooltip");
  const equity = run.equity_curve || { index: [], values: [] };
  if (!hasFiniteSeriesValues(equity)) {
    document.getElementById("equitySubtitle").textContent =
      "The full run for this experiment does not contain valid equity data.";
    svg.innerHTML = emptyChartText("No equity data available for this run.");
    return;
  }
  const visualSplit = run.visual_split || { ranges: [], note: "" };
  document.getElementById("equitySubtitle").textContent =
    visualSplit.ranges?.length
      ? "Shaded green marks the selector zone, amber marks validation-only ranges when present, and rose marks the final audit slice."
      : "Full-run equity curve.";
  drawLineChart(svg, tooltip, [
    {
      label: "Equity",
      color: COLORS.equity,
      index: equity.index,
      values: equity.values,
      formatter: (value) => formatNumber(value, 4),
    },
  ], {
    title: "Portfolio Equity",
    yFormatter: (value) => formatNumber(value, 3),
    bands: (visualSplit.ranges || []).map((range) => ({
      label: range.label,
      startIndex: range.start_idx,
      endIndex: range.end_idx,
      color:
        range.kind === "audit_holdout"
          ? "rgba(251, 113, 133, 0.07)"
          : range.kind === "validation_holdout" || range.kind === "holdout_view"
            ? "rgba(240, 180, 86, 0.06)"
            : "rgba(74, 222, 128, 0.06)",
      textColor:
        range.kind === "audit_holdout"
          ? "#fb7185"
          : range.kind === "validation_holdout" || range.kind === "holdout_view"
            ? "#f0b456"
            : "#4ade80",
    })),
  });
}

function renderMetricsChart(run) {
  const frame = run.metrics_by_period || { index: [], columns: [], rows: [] };
  const tooltip = document.getElementById("pageTooltip");
  const grossExposure = metricSeries(frame, "gross_exposure");
  const cashBalance = metricSeries(frame, "cash_balance");
  const marginHeadroom = metricSeries(frame, "margin_headroom");
  if (
    !hasFiniteSeriesValues(grossExposure) &&
    !hasFiniteSeriesValues(cashBalance) &&
    !hasFiniteSeriesValues(marginHeadroom)
  ) {
    document.getElementById("metricsChart").innerHTML = emptyChartText(
      "No run-metric data available for this run."
    );
    renderChartLegend(document.getElementById("metricsLegend"), []);
    return;
  }
  drawLineChart(
    document.getElementById("metricsChart"),
    tooltip,
    [
      {
        label: "Gross Exposure",
        color: COLORS.grossExposure,
        index: grossExposure.index,
        values: grossExposure.values,
        formatter: (value) => formatNumber(value, 3),
      },
      {
        label: "Cash Balance",
        color: COLORS.cashBalance,
        index: cashBalance.index,
        values: cashBalance.values,
        formatter: (value) => formatPercent(value),
      },
      {
        label: "Margin Headroom",
        color: COLORS.marginHeadroom,
        index: marginHeadroom.index,
        values: marginHeadroom.values,
        formatter: (value) => formatPercent(value),
      },
    ],
    {
      title: "Exposure And Budget",
      yFormatter: (value) => formatNumber(value, 3),
    }
  );
  renderChartLegend(document.getElementById("metricsLegend"), [
    { label: "Gross Exposure", color: COLORS.grossExposure },
    { label: "Cash Balance", color: COLORS.cashBalance },
    { label: "Margin Headroom", color: COLORS.marginHeadroom },
  ]);
}

function getSpecHash() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const experimentsIndex = parts.indexOf("experiments");
  if (experimentsIndex >= 0 && parts[experimentsIndex + 1]) {
    return decodeURIComponent(parts[experimentsIndex + 1]);
  }
  return new URLSearchParams(window.location.search).get("spec");
}

function formatPrice(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "n/a";
  const digits = Math.abs(numeric) >= 1000 ? 2 : Math.abs(numeric) >= 1 ? 4 : 6;
  return formatUsd(numeric, digits);
}

function formatUsd(value, digits = 2) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "n/a";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function formatSignedUsd(value, digits = 2) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "n/a";
  const formatted = formatUsd(Math.abs(numeric), digits);
  if (numeric < 0) return `-${formatted}`;
  if (numeric > 0) return `+${formatted}`;
  return formatted;
}

function formatAssetUnits(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "n/a";
  const absolute = Math.abs(numeric);
  if (absolute === 0) return "0";
  if (absolute < 0.001) return numeric.toExponential(4);
  if (absolute < 1) return numeric.toFixed(6);
  if (absolute < 1000) return numeric.toFixed(4);
  return numeric.toFixed(2);
}

function parseOptionalNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function loadDisplayCapitalUsd() {
  try {
    const stored = window.localStorage.getItem(DISPLAY_CAPITAL_STORAGE_KEY);
    const numeric = Number(stored);
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric;
    }
  } catch (_error) {
    // Ignore storage failures and fall back to the default.
  }
  return DEFAULT_DISPLAY_CAPITAL_USD;
}

function saveDisplayCapitalUsd(value) {
  try {
    window.localStorage.setItem(DISPLAY_CAPITAL_STORAGE_KEY, String(value));
  } catch (_error) {
    // Ignore storage failures.
  }
}

function getDisplayCapitalUsd() {
  const numeric = Number(PAGE_STATE.displayCapitalUsd);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric;
  }
  return DEFAULT_DISPLAY_CAPITAL_USD;
}

function bindDisplayCapitalInput() {
  const input = document.getElementById("displayCapitalInput");
  if (!input) return;
  input.value = String(getDisplayCapitalUsd());
  input.addEventListener("change", () => {
    const numeric = Number(input.value);
    PAGE_STATE.displayCapitalUsd =
      Number.isFinite(numeric) && numeric > 0 ? numeric : DEFAULT_DISPLAY_CAPITAL_USD;
    input.value = String(getDisplayCapitalUsd());
    saveDisplayCapitalUsd(getDisplayCapitalUsd());
    if (PAGE_STATE.payload) {
      renderScaledValues();
    }
  });
}

function outOfSampleMetric(summary, prefix) {
  const available = Boolean(summary?.[`${prefix}_available`]);
  if (!available) {
    return "n/a";
  }
  return `${formatPercent(summary?.[`${prefix}_total_return`])} / ${formatNumber(summary?.[`${prefix}_sharpe`], 2)}`;
}


// ─── Section State Persistence ─────────────────────────────────────
const SECTION_STATE_KEY = "siglab.experimentSections";

function loadSectionState() {
  try {
    const saved = localStorage.getItem(SECTION_STATE_KEY);
    return saved ? JSON.parse(saved) : {};
  } catch { return {}; }
}

function saveSectionState(state) {
  try { localStorage.setItem(SECTION_STATE_KEY, JSON.stringify(state)); } catch {}
}

function initSectionPersistence() {
  const sections = document.querySelectorAll("details.experiment-section");
  if (!sections.length) return;

  const saved = loadSectionState();

  sections.forEach((section, i) => {
    const key = `section_${i}`;
    if (key in saved) {
      section.open = saved[key];
    }
  });

  sections.forEach((section, i) => {
    section.addEventListener("toggle", () => {
      const state = loadSectionState();
      state[`section_${i}`] = section.open;
      saveSectionState(state);
    });
  });
}

document.addEventListener("DOMContentLoaded", initSectionPersistence);

// ─── Keyboard Shortcut: E to toggle all sections ──────────────────
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
  if (e.key === "e" && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    const sections = document.querySelectorAll("details.experiment-section");
    if (!sections.length) return;
    const allOpen = Array.from(sections).every(s => s.open);
    sections.forEach(s => { s.open = !allOpen; });
    const state = {};
    sections.forEach((s, i) => { state[`section_${i}`] = s.open; });
    saveSectionState(state);
  }
});


})();

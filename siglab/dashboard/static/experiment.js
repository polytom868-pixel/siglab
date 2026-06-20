const PAGE_STATE = {
  payload: null,
  displayCapitalUsd: null,
  isRefreshing: false,
};

const DEFAULT_DISPLAY_CAPITAL_USD = 100000;
const DISPLAY_CAPITAL_STORAGE_KEY = "siglab.displayCapitalUsd";
const { TRACK_LABELS, formatDateTime, formatNumber, formatPercent, escapeHtml,
  rectNode, lineNode, textNode, historyRange, formatSweepMaybePercent,
  safeParseJson, emptyChartText, apiFetch, setLoading } = window.SigLabUi;

const COLORS = {
  equity: "#4ade80",
  grossExposure: "#f0b456",
  cashBalance: "#60a5fa",
  marginHeadroom: "#a3e635",
  turnover: "#a3b5a8",
};

const ACTION_META = {
  buy: { label: "Buy", color: "#4ade80", tone: "" },
  sell: { label: "Sell", color: "#9fb4a5", tone: "slate" },
  short: { label: "Short", color: "#f0b456", tone: "amber" },
  cover: { label: "Cover", color: "#60a5fa", tone: "" },
  flip_long: { label: "Flip to Long", color: "#4ade80", tone: "" },
  flip_short: { label: "Flip to Short", color: "#f0b456", tone: "amber" },
  rebalance: { label: "Rebalance", color: "#a3b5a8", tone: "slate" },
};

document.addEventListener("DOMContentLoaded", async () => {
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
  renderPage();
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
  document.title = `${experiment.family || "Experiment"} ${experiment.spec_hash || ""}`.trim();
  document.getElementById("experimentTitle").textContent =
    `${experiment.family || "Experiment"} ${experiment.spec_hash || ""}`.trim();
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
    "This artifact predates full-series retention. Re-run the experiment to capture the equity curve and position timeline.";
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
          <div class="key">Spec</div><div class="mono">${escapeHtml(deployment.spec_path || "n/a")}</div>
        </div>
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
          <label>
            Wallet Label
            <input id="deploymentWalletLabel" type="text" placeholder="basis_trading_strategy" />
          </label>
          <label>
            Job Name
            <input id="deploymentJobName" type="text" placeholder="siglab-job-name" />
          </label>
          <label>
            Interval Seconds
            <input id="deploymentInterval" type="number" min="60" step="60" value="600" />
          </label>
          <label>
            Config Path
            <input id="deploymentConfigPath" type="text" placeholder="use default config" />
          </label>
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
          <button type="submit">Deploy</button>
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

  // job_name: max 64 chars, alphanumeric + hyphens
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
    resultNode.textContent = "Promoting...";
  }

  // Client-side input validation before building the payload
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
  const frame = run?.metrics_by_period || { index: [], columns: [], rows: [] };
  const cashBalance = metricSeries(frame, "cash_balance");
  const marginHeadroom = metricSeries(frame, "margin_headroom");
  const grossExposure = metricSeries(frame, "gross_exposure");
  const outcome = canonicalOutcomeDecomposition(run);
  const displayCapital = getDisplayCapitalUsd();
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
      label: "Median Return",
      value: formatPercent(summary.median_total_return ?? 0),
      detail: "Raw median total return across evaluated windows.",
    },
    {
      label: "Pre-Audit Return",
      value: formatPercent(summary.pre_audit_canonical_total_return ?? 0),
      detail: "Canonical total return measured only up to the audit boundary.",
    },
    {
      label: "Audit Return",
      value: summary.audit_available ? formatPercent(summary.audit_total_return ?? 0) : "n/a",
      detail: "Final untouched out-of-sample total return on the audit slice.",
    },
    {
      label: "Price Outcome",
      value: outcome ? formatPercent(outcome.priceOutcome) : "n/a",
      detail: outcome
        ? `Mark-to-market outcome on the canonical run. At ${formatUsd(displayCapital)}, about ${formatSignedUsd(
            outcome.priceOutcome * displayCapital
          )}.`
        : "Requires retained fee and funding components in the canonical artifact.",
    },
    {
      label: "Carry Outcome",
      value: outcome ? formatPercent(outcome.carryOutcome) : "n/a",
      detail: outcome
        ? `Funding/carry contribution on the canonical run. Positive means carry income. At ${formatUsd(
            displayCapital
          )}, about ${formatSignedUsd(outcome.carryOutcome * displayCapital)}.`
        : "Requires retained fee and funding components in the canonical artifact.",
    },
    {
      label: "Tx Cost",
      value: outcome ? formatPercent(outcome.txCost) : "n/a",
      detail: outcome
        ? `Fee plus slippage drag on the canonical run. At ${formatUsd(displayCapital)}, about ${formatSignedUsd(
            outcome.txCost * displayCapital
          )}.`
        : "Requires retained fee and funding components in the canonical artifact.",
    },
    {
      label: "Canonical Trades",
      value: `${tradeCount}`,
      detail: seriesAvailable
        ? "Trade count on the full-run timeline retained for this experiment."
        : "Full-run trade tape not retained for this historical artifact.",
    },
    {
      label: "Min Cash Balance",
      value: formatPercent(seriesMinimum(cashBalance)),
      detail: "Lowest retained cash balance during the canonical run, as a share of starting capital.",
    },
    {
      label: "Min Margin Headroom",
      value: formatPercent(seriesMinimum(marginHeadroom)),
      detail: "Lowest equity minus maintenance-margin requirement during the canonical run.",
    },
    {
      label: "Max Gross Exposure",
      value: formatNumber(seriesMaximum(grossExposure), 3),
      detail: "Highest retained gross notional exposure divided by equity.",
    },
  ];
  if (summary.policy_sweep_comparison_available) {
    cards.splice(1, 0,
      {
        label: "Declared Score",
        value: formatNumber(summary.policy_sweep_declared_evaluation?.selector_aggregate_score, 3),
        detail: "Unswept declared policy score on the selector objective before local tuning.",
      },
      {
        label: "Frozen Score",
        value: formatNumber(summary.policy_sweep_frozen_evaluation?.selector_aggregate_score, 3),
        detail: "Swept policy score on the selector objective after local tuning.",
      }
    );
  }

  document.getElementById("experimentSummary").innerHTML = cards
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
        <h3>Artifact</h3>
        <div class="kv">
          <div class="key">Track</div><div>${escapeHtml(experiment.track_label || TRACK_LABELS[experiment.track] || experiment.track || "unknown")}</div>
          <div class="key">Source</div><div>${escapeHtml(experiment.source || compiledMetadata.source || "unknown")}</div>
          <div class="key">Series</div><div>${seriesAvailable ? "retained" : "missing"}</div>
          <div class="key">Timing</div><div>${escapeHtml(experiment.timing?.signal_timing || "unknown")}</div>
          <div class="key">Bundle As Of</div><div>${escapeHtml(experiment.timing?.bundle_as_of || "n/a")}</div>
          <div class="key">Artifact Path</div><div class="mono">${escapeHtml(experiment.artifact_path || "n/a")}</div>
        </div>
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
      ${renderPolicySweepBlock(summary, experiment.family)}
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

function renderPolicySweepBlock(summary, family) {
  if (!summary?.policy_sweep_comparison_available) {
    const pairFamily = String(family || "").startsWith("perp_pair_trade_");
    const message = pairFamily
      ? "This pair artifact does not have a stored declared-vs-frozen policy comparison."
      : "This family does not use the local pair-policy sweep, so there is no declared-vs-frozen comparison.";
    return `
      <div class="detail-block">
        <h3>Policy Sweep</h3>
        <p class="detail-copy">${escapeHtml(message)}</p>
      </div>
    `;
  }
  const declared = summary.policy_sweep_declared_evaluation || {};
  const frozen = summary.policy_sweep_frozen_evaluation || {};
  const winner = summary.policy_sweep_realized_winner || "tie";
  return `
    <div class="detail-block">
      <h3>Policy Sweep</h3>
      <div class="kv">
        <div class="key">Declared Score</div><div>${escapeHtml(formatNumber(declared.selector_aggregate_score, 3))}</div>
        <div class="key">Frozen Score</div><div>${escapeHtml(formatNumber(frozen.selector_aggregate_score, 3))}</div>
        <div class="key">Declared Selector Return</div><div>${escapeHtml(formatPercent(declared.selector_median_total_return ?? 0))}</div>
        <div class="key">Frozen Selector Return</div><div>${escapeHtml(formatPercent(frozen.selector_median_total_return ?? 0))}</div>
        <div class="key">Declared Pre-Audit</div><div>${escapeHtml(formatPercent(declared.pre_audit_canonical_total_return ?? 0))}</div>
        <div class="key">Frozen Pre-Audit</div><div>${escapeHtml(formatPercent(frozen.pre_audit_canonical_total_return ?? 0))}</div>
        <div class="key">Declared Validation</div><div>${escapeHtml(formatSweepMaybePercent(declared.validation_total_return))}</div>
        <div class="key">Frozen Validation</div><div>${escapeHtml(formatSweepMaybePercent(frozen.validation_total_return))}</div>
        <div class="key">Realized Winner</div><div>${escapeHtml(winner)}</div>
      </div>
      <p class="detail-copy">
        Declared-better metrics: ${escapeHtml(joinOrNone(summary.policy_sweep_declared_better_metrics))}. Frozen-better metrics: ${escapeHtml(joinOrNone(summary.policy_sweep_frozen_better_metrics))}.
      </p>
    </div>
  `;
}

function renderEquityChart(run) {
  const svg = document.getElementById("equityChart");
  const tooltip = document.getElementById("pageTooltip");
  const equity = run.equity_curve || { index: [], values: [] };
  if (!hasFiniteSeriesValues(equity)) {
    document.getElementById("equitySubtitle").textContent =
      "The retained canonical run for this experiment does not contain finite equity values.";
    svg.innerHTML = emptyChartText("No finite equity values were retained for this run.");
    return;
  }
  const visualSplit = run.visual_split || { ranges: [], note: "" };
  document.getElementById("equitySubtitle").textContent =
    visualSplit.ranges?.length
      ? "Shaded green marks the selector zone, amber marks validation-only ranges when present, and rose marks the final audit slice."
      : "Canonical full-run equity curve.";
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
      "No finite run-metric values were retained for this run."
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

function renderHeatmap(run) {
  const container = document.getElementById("positionHeatmap");
  const timeline = run.target_weights;
  if (!timeline || !timeline.columns?.length || !timeline.index?.length) {
    container.innerHTML = `<p class="empty-state">No retained weight timeline for this experiment.</p>`;
    return;
  }
  const tradableColumns = timeline.columns.filter((asset) => asset !== "GLOBAL");
  if (!tradableColumns.length) {
    container.innerHTML = `<p class="empty-state">No retained tradable weight timeline for this experiment.</p>`;
    return;
  }

  const segments = buildWeightSegments(timeline);
  const width = 1180;
  const rowHeight = 26;
  const labelWidth = 108;
  const axisHeight = 38;
  const height = Math.max(140, rowHeight * tradableColumns.length + axisHeight + 28);
  const plotWidth = width - labelWidth - 16;

  const pieces = [
    `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" class="heatmap-svg">`,
    `<rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>`,
  ];

  const maxIndex = Math.max(timeline.index.length - 1, 1);
  const xScale = (position) => labelWidth + (Math.max(0, Math.min(maxIndex, position)) / maxIndex) * plotWidth;

  tradableColumns.forEach((asset, rowIndex) => {
    const y = 24 + rowIndex * rowHeight;
    pieces.push(
      `<text x="6" y="${y + 16}" fill="#a3b5a8" font-size="12" font-family="Inter, sans-serif">${escapeHtml(asset)}</text>`
    );

    segments.forEach((segment) => {
      const value = Number(segment.weights?.[asset] || 0);
      const x1 = xScale(segment.startPosition);
      const x2 = xScale(segment.endPosition);
      const widthPx = Math.max(2, x2 - x1);
      pieces.push(
        `<rect x="${x1}" y="${y}" width="${widthPx}" height="18" fill="${weightColor(value)}" rx="2" ry="2">
          <title>${escapeHtml(asset)}\n${escapeHtml(segment.startTimestamp)} → ${escapeHtml(segment.endTimestamp)}\nweight ${formatNumber(value, 3)}</title>
        </rect>`
      );
    });
  });

  const axisY = height - 24;
  const tickY = axisY - 8;
  buildAxisTicks(timeline.index, 6).forEach((tick, tickIndex, ticks) => {
    const x = xScale(tick.position);
    pieces.push(
      `<line x1="${x}" y1="${axisY - 10}" x2="${x}" y2="${axisY - 4}" stroke="rgba(255,255,255,0.10)" stroke-width="1"></line>`
    );
    const anchor =
      tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle";
    pieces.push(
      `<text x="${x}" y="${axisY + 12}" fill="#6b7f70" font-size="11" text-anchor="${anchor}" font-family="Inter, sans-serif">${escapeHtml(formatAxisDateTime(tick.timestamp))}</text>`
    );
  });

  pieces.push(`</svg>`);
  container.innerHTML = pieces.join("");
}

function renderAssetActionCharts(run) {
  const container = document.getElementById("assetActionCharts");
  if (!container) return;
  const annotatedTrades = annotateTrades(run?.trades || []);
  if (!annotatedTrades.length) {
    container.innerHTML = `<p class="empty-state">No trade actions recorded.</p>`;
    return;
  }

  const bySymbol = groupTradesBySymbol(annotatedTrades);
  const cards = Object.entries(bySymbol)
    .sort((left, right) => {
      const leftLatest = left[1][left[1].length - 1];
      const rightLatest = right[1][right[1].length - 1];
      return Math.abs(Number(rightLatest?.target_weight || 0)) - Math.abs(Number(leftLatest?.target_weight || 0));
    })
    .map(([symbol, trades]) => renderAssetActionCard(symbol, trades));
  container.innerHTML = cards.join("");
}

function renderAssetActionCard(symbol, trades) {
  const latest = trades[trades.length - 1] || {};
  const latestState = positionStateLabel(latest.target_weight);
  return `
    <article class="asset-action-card">
      <div class="asset-action-head">
        <div>
          <h3>${escapeHtml(symbol)}</h3>
          <div class="asset-action-meta">
            ${escapeHtml(formatDateTime(trades[0]?.timestamp))} → ${escapeHtml(formatDateTime(trades[trades.length - 1]?.timestamp))}
          </div>
        </div>
        <div class="asset-action-meta">
          ${escapeHtml(String(trades.length))} trades • latest ${escapeHtml(latestState)}
        </div>
      </div>
      ${assetActionSvg(symbol, trades)}
      <div class="asset-action-legend">
        <span class="legend-marker"><span class="legend-dot"></span>Buy</span>
        <span class="legend-marker"><span class="legend-dot sell"></span>Sell</span>
        <span class="legend-marker"><span class="legend-dot short"></span>Short</span>
        <span class="legend-marker"><span class="legend-dot cover"></span>Cover</span>
      </div>
    </article>
  `;
}

function assetActionSvg(symbol, trades) {
  const width = 520;
  const height = 180;
  const margin = { top: 18, right: 14, bottom: 34, left: 46 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const timestamps = trades
    .map((trade) => new Date(trade.timestamp).getTime())
    .filter((value) => Number.isFinite(value));
  const prices = trades
    .map((trade) => Number(trade.price))
    .filter((value) => Number.isFinite(value));
  if (!timestamps.length || !prices.length) {
    return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" class="asset-action-svg"><text x="16" y="24" fill="#6b7f70" font-family="Inter, sans-serif" font-size="11">No retained prices for ${escapeHtml(symbol)}</text></svg>`;
  }
  let yMin = Math.min(...prices);
  let yMax = Math.max(...prices);
  if (yMin === yMax) {
    yMin *= 0.98;
    yMax *= 1.02;
  }
  const yPadding = (yMax - yMin) * 0.12;
  yMin -= yPadding;
  yMax += yPadding;
  const start = timestamps[0];
  const end = timestamps[timestamps.length - 1];
  const xScale = (timestamp) =>
    margin.left + ((timestamp - start) / Math.max(end - start, 1)) * plotWidth;
  const yScale = (price) =>
    margin.top + plotHeight - ((price - yMin) / (yMax - yMin)) * plotHeight;

  const bands = trades
    .map((trade, index) => {
      const x1 = xScale(new Date(trade.timestamp).getTime());
      const next = trades[index + 1];
      const x2 = next ? xScale(new Date(next.timestamp).getTime()) : width - margin.right;
      const tone = positionBandColor(trade.target_weight);
      return `<rect x="${x1}" y="${margin.top}" width="${Math.max(2, x2 - x1)}" height="${plotHeight}" fill="${tone}" rx="2" ry="2"></rect>`;
    })
    .join("");
  const linePoints = trades
    .map((trade) => `${xScale(new Date(trade.timestamp).getTime())},${yScale(Number(trade.price))}`)
    .join(" ");
  const markers = trades
    .map((trade) => tradeMarkerSvg(trade, xScale(new Date(trade.timestamp).getTime()), yScale(Number(trade.price))))
    .join("");

  return `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" class="asset-action-svg">
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
      ${bands}
      <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="rgba(255,255,255,0.08)" stroke-width="1"></line>
      <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="rgba(255,255,255,0.08)" stroke-width="1"></line>
      <polyline fill="none" stroke="#e2ebe5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" points="${linePoints}"></polyline>
      ${markers}
      <text x="${margin.left}" y="12" fill="#6b7f70" font-size="11" font-family="Inter, sans-serif">${escapeHtml(formatPrice(yMax))}</text>
      <text x="${margin.left}" y="${height - margin.bottom + 16}" fill="#6b7f70" font-size="11" font-family="Inter, sans-serif">${escapeHtml(formatAxisDateTime(trades[0]?.timestamp))}</text>
      <text x="${width - margin.right}" y="${height - margin.bottom + 16}" text-anchor="end" fill="#6b7f70" font-size="11" font-family="Inter, sans-serif">${escapeHtml(formatAxisDateTime(trades[trades.length - 1]?.timestamp))}</text>
    </svg>
  `;
}

function renderTrades(trades) {
  const tbody = document.getElementById("tradesTable");
  const subtitle = document.getElementById("tradeSubtitle");
  tbody.innerHTML = "";
  if (!trades.length) {
    subtitle.textContent = "No trades recorded for this run.";
    tbody.innerHTML = '<tr><td colspan="9" class="empty-state">No trades recorded for this run.</td></tr>';
    return;
  }

  const annotatedTrades = annotateTrades(trades);
  const visible = annotatedTrades.slice(-400).reverse();
  const displayCapital = getDisplayCapitalUsd();
  subtitle.textContent =
    trades.length > visible.length
      ? `Showing the latest ${visible.length} of ${trades.length} trades from the canonical run. Display notionals and units assume ${formatUsd(displayCapital)} starting capital. Actions reflect the post-trade position state.`
      : `Showing ${trades.length} trades from the canonical run. Display notionals and units assume ${formatUsd(displayCapital)} starting capital. Actions reflect the post-trade position state.`;

  visible.forEach((trade) => {
    const row = document.createElement("tr");
    const scaledUnits = Number(trade.units) * displayCapital;
    const scaledNotional = Number(trade.notional) * displayCapital;
    const scaledCost = Number(trade.cost) * displayCapital;
    const actionMeta = ACTION_META[trade.action] || ACTION_META.rebalance;
    row.innerHTML = `
      <td>${escapeHtml(formatDateTime(trade.timestamp))}</td>
      <td>${escapeHtml(trade.symbol || "")}</td>
      <td><span class="pill ${escapeHtml(actionMeta.tone || "")}">${escapeHtml(actionMeta.label)}</span></td>
      <td>${escapeHtml(trade.post_trade_label || "Flat")}</td>
      <td>${escapeHtml(formatPrice(trade.price))}</td>
      <td>${escapeHtml(formatAssetUnits(scaledUnits))}</td>
      <td>${escapeHtml(formatNumber(trade.target_weight, 4))}</td>
      <td>${escapeHtml(formatSignedUsd(scaledNotional))}</td>
      <td>${escapeHtml(formatSignedUsd(-Math.abs(scaledCost)))}</td>
    `;
    tbody.appendChild(row);
  });
}

function drawLineChart(svg, tooltip, seriesList, options) {
  svg.innerHTML = "";
  const validSeries = seriesList.filter((series) => (series.index || []).length && (series.values || []).length);
  if (!validSeries.length) {
    svg.innerHTML = emptyChartText("No time-series data retained for this chart.");
    return;
  }
  const allValues = validSeries.flatMap((series) => (series.values || []).filter((value) => value !== null));
  if (!allValues.length) {
    svg.innerHTML = emptyChartText("No finite values retained for this chart.");
    return;
  }

  const width = 1200;
  const height = Number(svg.getAttribute("viewBox")?.split(" ")[3] || 420);
  const margin = { top: 28, right: 24, bottom: 72, left: 58 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const referenceIndex = validSeries.reduce(
    (longest, series) => ((series.index || []).length > longest.length ? (series.index || []) : longest),
    validSeries[0].index || []
  );
  let yMin = Math.min(...allValues);
  let yMax = Math.max(...allValues);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const padding = (yMax - yMin) * 0.12;
  yMin -= padding;
  yMax += padding;
  const maxIndex = Math.max(...validSeries.map((series) => series.values.length), 1);
  const xScale = (index) => margin.left + (index / Math.max(maxIndex - 1, 1)) * plotWidth;
  const yScale = (value) => margin.top + plotHeight - ((value - yMin) / (yMax - yMin)) * plotHeight;

  svg.appendChild(rectNode(0, 0, width, height, "transparent"));

  for (let step = 0; step <= 5; step += 1) {
    const value = yMin + ((yMax - yMin) * step) / 5;
    const y = yScale(value);
    svg.appendChild(lineNode(margin.left, y, width - margin.right, y, "rgba(255,255,255,0.04)", 1));
    svg.appendChild(textNode(12, y + 4, options.yFormatter(value), "#6b7f70", "12"));
  }

  svg.appendChild(lineNode(margin.left, height - margin.bottom, width - margin.right, height - margin.bottom, "rgba(255,255,255,0.08)", 1));
  svg.appendChild(lineNode(margin.left, margin.top, margin.left, height - margin.bottom, "rgba(255,255,255,0.08)", 1));
  svg.appendChild(textNode(margin.left, 18, options.title, "#e2ebe5", "14", "600"));
  const boundaryScale = (boundary) =>
    boundary >= maxIndex
      ? width - margin.right
      : margin.left + (boundary / Math.max(maxIndex - 1, 1)) * plotWidth;
  (options.bands || []).forEach((band) => {
    const startIndex = Math.max(0, Number(band.startIndex || 0));
    const endIndex = Math.max(startIndex + 1, Number(band.endIndex || maxIndex));
    const x1 = boundaryScale(startIndex);
    const x2 = boundaryScale(Math.min(endIndex, maxIndex));
    const rect = rectNode(x1, margin.top, Math.max(1, x2 - x1), plotHeight, band.color || "rgba(255,255,255,0.02)");
    rect.setAttribute("stroke", "none");
    svg.appendChild(rect);
    const label = textNode(x1 + 8, margin.top + 18, band.label || "", band.textColor || "#6b7f70", "11", "600");
    label.setAttribute("text-anchor", "start");
    svg.appendChild(label);
  });
  buildAxisTicks(referenceIndex, 6).forEach((tick, tickIndex, ticks) => {
    const x = xScale(tick.position);
    svg.appendChild(lineNode(x, height - margin.bottom, x, height - margin.bottom + 6, "rgba(255,255,255,0.08)", 1));
    const label = textNode(x, height - 28, formatAxisDateTime(tick.timestamp), "#6b7f70", "11");
    if (tickIndex === 0) {
      label.setAttribute("text-anchor", "start");
    } else if (tickIndex === ticks.length - 1) {
      label.setAttribute("text-anchor", "end");
    } else {
      label.setAttribute("text-anchor", "middle");
    }
    svg.appendChild(label);
  });
  const xAxisLabel = textNode(width / 2, height - 10, "Time", "#6b7f70", "11");
  xAxisLabel.setAttribute("text-anchor", "middle");
  svg.appendChild(xAxisLabel);

  validSeries.forEach((series) => {
    const points = series.values
      .map((value, index) => (value === null ? null : `${xScale(index)},${yScale(value)}`))
      .filter(Boolean);
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", series.color);
    polyline.setAttribute("stroke-width", "2.4");
    polyline.setAttribute("stroke-linecap", "round");
    polyline.setAttribute("stroke-linejoin", "round");
    polyline.setAttribute("points", points.join(" "));
    svg.appendChild(polyline);

    sampleSeries(series.index, series.values, 120).forEach((point) => {
      if (point.value === null) return;
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", `${xScale(point.index)}`);
      dot.setAttribute("cy", `${yScale(point.value)}`);
      dot.setAttribute("r", "4");
      dot.setAttribute("fill", series.color);
      dot.setAttribute("stroke", "rgba(8, 12, 10, 0.6)");
      dot.setAttribute("stroke-width", "1.2");
      dot.addEventListener("mouseenter", (event) => {
        tooltip.innerHTML = `
          <div class="meta">${escapeHtml(point.timestamp)}</div>
          <strong>${escapeHtml(series.label)}</strong>
          <div>${escapeHtml(series.formatter(point.value))}</div>
        `;
        tooltip.classList.remove("hidden");
        moveTooltip(event, tooltip);
      });
      dot.addEventListener("mousemove", (event) => moveTooltip(event, tooltip));
      dot.addEventListener("mouseleave", () => tooltip.classList.add("hidden"));
      svg.appendChild(dot);
    });
  });
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

function hasFiniteSeriesValues(series) {
  return (series?.values || []).some((value) => value !== null && Number.isFinite(Number(value)));
}

function latestWeightMap(timeline) {
  const changes = timeline?.changes || [];
  if (!changes.length) return {};
  return changes[changes.length - 1].weights || {};
}

function buildWeightSegments(timeline) {
  const index = timeline?.index || [];
  const changes = timeline.changes || [];
  if (!index.length || !changes.length) {
    return [];
  }

  const positionByTimestamp = new Map(index.map((timestamp, position) => [timestamp, position]));
  const positions = changes
    .map((change) => ({
      timestamp: change.timestamp,
      weights: change.weights || {},
      position: positionByTimestamp.get(change.timestamp),
    }))
    .filter((change) => Number.isFinite(change.position));

  if (!positions.length) {
    return [];
  }

  return positions.map((change, idx) => {
    const next = positions[idx + 1];
    const startPosition = Number(change.position);
    const endPosition = next ? Math.max(startPosition + 1, Number(next.position)) : index.length - 1;
    const endTimestamp = next ? next.timestamp : index[index.length - 1];
    return {
      startTimestamp: change.timestamp,
      endTimestamp,
      startPosition,
      endPosition,
      weights: change.weights,
    };
  });
}

function annotateTrades(trades) {
  const previousWeightBySymbol = new Map();
  return [...trades].map((trade) => {
    const symbol = String(trade.symbol || "");
    const previousWeight = Number(previousWeightBySymbol.get(symbol) || 0);
    const nextWeight = Number(trade.target_weight || 0);
    const classification = classifyTradeAction(previousWeight, nextWeight, Number(trade.units || 0));
    previousWeightBySymbol.set(symbol, nextWeight);
    return {
      ...trade,
      previous_target_weight: previousWeight,
      action: classification.action,
      action_label: classification.label,
      post_trade_label: positionStateLabel(nextWeight),
    };
  });
}

function classifyTradeAction(previousWeight, nextWeight, units) {
  const previousState = weightSign(previousWeight);
  const nextState = weightSign(nextWeight);
  if (previousState === 0 && nextState > 0) return { action: "buy", label: "Buy" };
  if (previousState === 0 && nextState < 0) return { action: "short", label: "Short" };
  if (previousState > 0 && nextState === 0) return { action: "sell", label: "Sell" };
  if (previousState < 0 && nextState === 0) return { action: "cover", label: "Cover" };
  if (previousState > 0 && nextState < 0) return { action: "flip_short", label: "Flip to Short" };
  if (previousState < 0 && nextState > 0) return { action: "flip_long", label: "Flip to Long" };
  if (previousState > 0 && nextState > 0) {
    return nextWeight >= previousWeight ? { action: "buy", label: "Buy" } : { action: "sell", label: "Sell" };
  }
  if (previousState < 0 && nextState < 0) {
    return nextWeight <= previousWeight ? { action: "short", label: "Short" } : { action: "cover", label: "Cover" };
  }
  if (Number.isFinite(units) && units > 0) return { action: "buy", label: "Buy" };
  if (Number.isFinite(units) && units < 0) return { action: "sell", label: "Sell" };
  return { action: "rebalance", label: "Rebalance" };
}

function weightSign(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || Math.abs(numeric) < 1e-9) return 0;
  return numeric > 0 ? 1 : -1;
}

function positionStateLabel(weight) {
  const state = weightSign(weight);
  if (state > 0) return "Long";
  if (state < 0) return "Short";
  return "Flat";
}

function groupTradesBySymbol(trades) {
  return trades.reduce((acc, trade) => {
    const symbol = String(trade.symbol || "UNKNOWN");
    if (!acc[symbol]) acc[symbol] = [];
    acc[symbol].push(trade);
    return acc;
  }, {});
}

function positionBandColor(weight) {
  const state = weightSign(weight);
  if (state > 0) return "rgba(74, 222, 128, 0.08)";
  if (state < 0) return "rgba(240, 180, 86, 0.08)";
  return "rgba(255, 255, 255, 0.03)";
}

function tradeMarkerSvg(trade, x, y) {
  const meta = ACTION_META[trade.action] || ACTION_META.rebalance;
  const title = `${trade.symbol || ""}\n${meta.label}\n${formatDateTime(trade.timestamp)}\n${formatPrice(trade.price)}\nPost-trade ${trade.post_trade_label || "Flat"}`;
  if (trade.action === "short" || trade.action === "flip_short") {
    return `<polygon points="${x},${y - 6} ${x - 5.2},${y + 4.6} ${x + 5.2},${y + 4.6}" fill="${meta.color}" stroke="rgba(8,12,10,0.65)" stroke-width="1.2"><title>${escapeHtml(title)}</title></polygon>`;
  }
  if (trade.action === "cover") {
    return `<rect x="${x - 4.4}" y="${y - 4.4}" width="8.8" height="8.8" rx="1.8" ry="1.8" transform="rotate(45 ${x} ${y})" fill="${meta.color}" stroke="rgba(8,12,10,0.65)" stroke-width="1.2"><title>${escapeHtml(title)}</title></rect>`;
  }
  if (trade.action === "sell") {
    return `<circle cx="${x}" cy="${y}" r="4.3" fill="${meta.color}" stroke="rgba(8,12,10,0.65)" stroke-width="1.2"><title>${escapeHtml(title)}</title></circle>`;
  }
  return `<circle cx="${x}" cy="${y}" r="4.5" fill="${meta.color}" stroke="rgba(8,12,10,0.65)" stroke-width="1.2"><title>${escapeHtml(title)}</title></circle>`;
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

function pillRow(entries, mode) {
  const tone = mode === "short" ? "amber" : "";
  return `<div class="pill-row">${entries
    .map(([asset, value]) => `<span class="pill ${tone}">${escapeHtml(asset)} ${escapeHtml(formatNumber(value, 3))}</span>`)
    .join("")}</div>`;
}

function weightColor(value) {
  const clipped = Math.max(-1, Math.min(1, Number(value || 0)));
  if (Math.abs(clipped) < 1e-9) return "rgba(255, 255, 255, 0.04)";
  if (clipped > 0) {
    return `rgba(74, 222, 128, ${0.12 + Math.abs(clipped) * 0.65})`;
  }
  return `rgba(240, 180, 86, ${0.12 + Math.abs(clipped) * 0.65})`;
}

function moveTooltip(event, tooltip) {
  tooltip.style.left = `${event.clientX + 14}px`;
  tooltip.style.top = `${event.clientY + 12}px`;
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
      renderPage();
    }
  });
}

function sumSeriesValues(series) {
  return (series.values || []).reduce((total, value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? total + numeric : total;
  }, 0);
}

function canonicalOutcomeDecomposition(run) {
  const frame = run?.metrics_by_period || { columns: [], rows: [] };
  const feeSeries = metricSeries(frame, "fee_amount");
  const fundingSeries = metricSeries(frame, "funding_amount");
  if (!feeSeries.values.length || !fundingSeries.values.length) {
    return null;
  }
  const equityValues = (run?.equity_curve?.values || []).filter((value) => Number.isFinite(Number(value)));
  if (!equityValues.length) {
    return null;
  }
  const totalReturn = Number(equityValues[equityValues.length - 1]) / Number(equityValues[0]) - 1.0;
  const totalFees = sumSeriesValues(feeSeries);
  const totalFunding = sumSeriesValues(fundingSeries);
  return {
    totalReturn,
    totalFees,
    totalFunding,
    priceOutcome: totalReturn + totalFees + totalFunding,
    carryOutcome: -totalFunding,
    txCost: -totalFees,
  };
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

function outOfSampleMetric(summary, prefix) {
  const available = Boolean(summary?.[`${prefix}_available`]);
  if (!available) {
    return "n/a";
  }
  return `${formatPercent(summary?.[`${prefix}_total_return`])} / ${formatNumber(summary?.[`${prefix}_sharpe`], 2)}`;
}

function joinOrNone(values) {
  return Array.isArray(values) && values.length ? values.join(", ") : "none";
}




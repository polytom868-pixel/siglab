const HOME_STATE = {
  payload: null,
  autoRefreshTimer: null,
};

const {
  TRACK_LABELS,
  METRIC_META,
  formatDateTime,
  formatNumber,
  formatPercent,
  escapeHtml,
  llmIdentity,
} = window.SigLabUi;

document.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("refreshButton")?.addEventListener("click", () => refresh());
  document.getElementById("trackFilter")?.addEventListener("change", () => refresh());
  document.getElementById("familyFilter")?.addEventListener("change", () => refresh());
  document.getElementById("metricFilter")?.addEventListener("change", () => render());
  document.getElementById("autoRefresh")?.addEventListener("change", toggleAutoRefresh);
  await refresh();
  toggleAutoRefresh();
});

async function refresh() {
  const track = document.getElementById("trackFilter")?.value || "all";
  const family = document.getElementById("familyFilter")?.value || "all";
  const query = new URLSearchParams();
  if (track !== "all") query.set("track", track);
  if (family !== "all") query.set("family", family);
  const url = query.toString() ? `/api/runs?${query}` : "/api/runs";
  const response = await fetch(url, { cache: "no-store" });
  HOME_STATE.payload = await response.json();
  populateFamilyFilter(HOME_STATE.payload?.summary?.families || [], family);
  render();
}

function toggleAutoRefresh() {
  if (HOME_STATE.autoRefreshTimer) {
    clearInterval(HOME_STATE.autoRefreshTimer);
    HOME_STATE.autoRefreshTimer = null;
  }
  if (document.getElementById("autoRefresh")?.checked) {
    HOME_STATE.autoRefreshTimer = setInterval(refresh, 10000);
  }
}

function render() {
  if (!HOME_STATE.payload) return;
  const runs = HOME_STATE.payload.runs || [];
  renderScope(runs);
  renderSummary(runs);
  renderRunCards(runs);
}

function populateFamilyFilter(families, selectedValue) {
  const select = document.getElementById("familyFilter");
  if (!select) return;
  const current = selectedValue && families.includes(selectedValue) ? selectedValue : "all";
  select.innerHTML = [
    "<option value=\"all\">All Families</option>",
    ...families.map(
      (family) =>
        `<option value="${escapeHtml(family)}"${family === current ? " selected" : ""}>${escapeHtml(family)}</option>`
    ),
  ].join("");
  select.value = current;
}

function renderScope(runs) {
  const track = document.getElementById("trackFilter")?.value || "all";
  const family = document.getElementById("familyFilter")?.value || "all";
  const scope = [
    track === "all" ? "all tracks" : TRACK_LABELS[track] || track,
    family === "all" ? "all families" : family,
  ].join(" / ");
  const scopeSummary = document.getElementById("scopeSummary");
  if (scopeSummary) {
    scopeSummary.textContent = `Viewing ${scope}. ${runs.length} run${runs.length === 1 ? "" : "s"} in scope.`;
  }
}

function renderSummary(runs) {
  const container = document.getElementById("summaryCards");
  if (!container) return;
  const summary = HOME_STATE.payload?.summary || {};
  const metricKey = selectedMetricKey();
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  const points = runs.flatMap((run) => run.series_points || []);
  const bestPoint = points.reduce((best, point) => {
    if (!Number.isFinite(metricValue(point, metricKey))) return best;
    if (!best) return point;
    return metricValue(point, metricKey) > metricValue(best, metricKey) ? point : best;
  }, null);
  const cards = [
    {
      label: "Runs",
      value: `${summary.run_count || runs.length || 0}`,
      detail: "Visible run sessions in the current scope.",
    },
    {
      label: "Experiments",
      value: `${summary.experiment_count || 0}`,
      detail: "Total recorded experiments across the visible runs.",
    },
    {
      label: "Harness / Benchmark",
      value: `${summary.harness_run_count || 0} / ${summary.benchmark_run_count || 0}`,
      detail: "Visible harness runs versus external benchmark runs.",
    },
    {
      label: "Deployd Experiments",
      value: `${summary.deployd_count || 0}`,
      detail: "Deployments recorded inside the visible run set.",
    },
    {
      label: "Best Run",
      value: summary.best_aggregate_score != null ? formatNumber(summary.best_aggregate_score, 3) : "n/a",
      detail: summary.best_run_label
        ? `${summary.best_run_label} is the current strongest run by aggregate score.`
        : "No scored runs are visible.",
    },
    {
      label: `Best ${metricMeta.label}`,
      value: bestPoint ? metricMeta.formatter(metricValue(bestPoint, metricKey)) : "n/a",
      detail: bestPoint
        ? `${bestPoint.family || "experiment"} at ${bestPoint.run_iteration_label || `run #${bestPoint.run_position || "n/a"}`}`
        : "No finite experiment points are available for the selected metric.",
    },
  ];
  container.innerHTML = cards
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

function renderRunCards(runs) {
  const container = document.getElementById("runCards");
  if (!container) return;
  const metricKey = selectedMetricKey();
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  if (!runs.length) {
    container.innerHTML = `
      <article class="waiting-card">
        <div class="waiting-card-title">Hold tight</div>
        <p class="waiting-card-copy">No runs are visible for the current scope yet.</p>
        <p class="waiting-card-copy">If you just started one, SigLab may still be loading market data before the first experiment lands.</p>
      </article>
    `;
    return;
  }

  container.innerHTML = runs
    .map((run) => {
      const seriesSvg = sparklineSvg(run.series_points || [], metricKey);
      const llmLabel = llmIdentity(run.llm_provider, run.llm_model);
      const statusClass =
        run.status === "deployd" || run.status === "pass" ? "status-pass" : "status-fail";
      return `
        <article class="panel run-card">
          <div class="run-card-header">
            <div>
              <div class="run-card-track">${escapeHtml(TRACK_LABELS[run.track] || run.track || "Unknown Track")}</div>
              <h3>${escapeHtml(run.run_label || run.run_session_id)}</h3>
              <p class="run-card-meta">
                ${escapeHtml(run.runner_label || "unknown")} • ${escapeHtml(run.run_kind || "harness")}
                ${run.benchmark_deck ? ` • ${escapeHtml(run.benchmark_deck)}` : ""}
              </p>
            </div>
            <div class="run-card-badges">
              <span class="pill ${statusClass === "status-pass" ? "" : "amber"}">${escapeHtml(run.status || "unknown")}</span>
              <span class="pill slate">${escapeHtml(String(run.experiment_count || 0))} exp</span>
            </div>
          </div>
          <div class="run-card-chart">
            <div class="run-card-chart-header">
              <span>${escapeHtml(metricMeta.label)} in run order</span>
              <span class="mono">${escapeHtml(run.best_spec_hash || "n/a")}</span>
            </div>
            ${seriesSvg}
          </div>
          <div class="run-card-stats">
            <div><span class="key">Pass / Deployd</span><span>${escapeHtml(`${run.passed_count || 0} / ${run.deployd_count || 0}`)}</span></div>
            <div><span class="key">LLM / Burn-In</span><span>${escapeHtml(`${run.llm_experiment_count || 0} / ${run.deterministic_experiment_count || 0}`)}</span></div>
            <div><span class="key">LLM</span><span>${escapeHtml(llmLabel)}</span></div>
            <div><span class="key">Best Score</span><span>${escapeHtml(formatNumber(run.best_aggregate_score, 3))}</span></div>
            <div><span class="key">Best Validation</span><span>${escapeHtml(formatPercent(run.best_validation_total_return))}</span></div>
            <div><span class="key">Best Pre-Audit</span><span>${escapeHtml(formatPercent(run.best_pre_audit_canonical_total_return))}</span></div>
            <div><span class="key">Updated</span><span>${escapeHtml(formatDateTime(run.last_created_at))}</span></div>
          </div>
          <div class="run-card-links">
            <a class="button-link" href="/runs/${encodeURIComponent(run.run_session_id)}">Open Run</a>
            ${run.best_spec_hash ? `<a class="table-link" href="/experiments/${encodeURIComponent(run.best_spec_hash)}">Best Experiment</a>` : ""}
          </div>
        </article>
      `;
    })
    .join("");
}

function sparklineSvg(points, metricKey) {
  if (!points.length) {
    return `
      <div class="waiting-card waiting-card-compact">
        <div class="waiting-card-title">Hold tight</div>
        <p class="waiting-card-copy">This run has started, but no experiment points have been recorded yet.</p>
        <p class="waiting-card-copy">SigLab is likely still loading market data or finishing the first evaluation.</p>
      </div>
    `;
  }
  const values = points
    .map((point) => ({ ...point, metric: metricValue(point, metricKey) }))
    .filter((point) => Number.isFinite(point.metric));
  if (!values.length) {
    return `<svg viewBox="0 0 360 110" preserveAspectRatio="none" class="run-sparkline"><text x="14" y="24" fill="#6b7f70" font-family="Inter, sans-serif" font-size="11">No finite values retained</text></svg>`;
  }
  const width = 360;
  const height = 110;
  const margin = { top: 10, right: 10, bottom: 18, left: 10 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  let yMin = Math.min(...values.map((point) => point.metric));
  let yMax = Math.max(...values.map((point) => point.metric));
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const padding = (yMax - yMin) * 0.16;
  yMin -= padding;
  yMax += padding;
  const xScale = (index) => margin.left + (index / Math.max(values.length - 1, 1)) * plotWidth;
  const yScale = (value) => margin.top + plotHeight - ((value - yMin) / (yMax - yMin)) * plotHeight;
  const best = values.reduce((top, point) => (point.metric > top.metric ? point : top), values[0]);
  const polyline = values
    .map((point, index) => `${xScale(index)},${yScale(point.metric)}`)
    .join(" ");
  const markers = values
    .map((point, index) => {
      const cx = xScale(index);
      const cy = yScale(point.metric);
      const fill = point.deployd ? "#ffffff" : point.passed ? "#4ade80" : "rgba(255,255,255,0.22)";
      const stroke = point.deployd ? "#4ade80" : "rgba(255,255,255,0.18)";
      const radius = point.deployd ? 4.2 : 3.2;
      return `<circle cx="${cx}" cy="${cy}" r="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="1.4"></circle>`;
    })
    .join("");
  const bestX = xScale(values.indexOf(best));
  const bestY = yScale(best.metric);
  return `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" class="run-sparkline">
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
      <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="rgba(255,255,255,0.08)" stroke-width="1"></line>
      <polyline fill="none" stroke="#4ade80" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" points="${polyline}"></polyline>
      ${markers}
      <circle cx="${bestX}" cy="${bestY}" r="5.4" fill="none" stroke="#f0b456" stroke-width="1.6"></circle>
    </svg>
  `;
}

function selectedMetricKey() {
  return document.getElementById("metricFilter")?.value || "aggregate_score";
}

function metricValue(point, metricKey) {
  const numeric = Number(point?.[metricKey]);
  return Number.isFinite(numeric) ? numeric : Number.NEGATIVE_INFINITY;
}




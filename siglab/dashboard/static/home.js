;(function homeInit() {
const HOME_STATE = {
  payload: null,
  autoRefreshTimer: null,
  isRefreshing: false,
  lastUpdatedTimestamp: null,
  _firstLoad: true,
};

const {
  TRACK_LABELS,
  METRIC_META,
  formatDateTime,
  formatNumber,
  formatPercent,
  escapeHtml,
  llmIdentity,
  selectedMetricKey,
  toggleAutoRefresh,
  initAriaLive,
  populateFamilyFilter,
  populateMetricFilter,
  showError,
  apiFetch,
  setLoading,
  renderSummaryCards,
  initThemeToggle,
  showOnboarding,
  // Chart functions (from chart-engine.js)
  sparklineSvg,
} = window.SigLabUi;

document.addEventListener("DOMContentLoaded", async () => {
  initAriaLive();
  initThemeToggle();
  showOnboarding();
  document.getElementById("refreshButton")?.addEventListener("click", () => refresh());
  document.getElementById("trackFilter")?.addEventListener("change", () => refresh());
  document.getElementById("familyFilter")?.addEventListener("change", () => refresh());
  document.getElementById("metricFilter")?.addEventListener("change", () => render());
  document.getElementById("autoRefresh")?.addEventListener("change", () => toggleAutoRefresh(HOME_STATE, refresh));
  populateMetricFilter("metricFilter", null, document.getElementById("metricFilter")?.value);
  await refresh();
  toggleAutoRefresh(HOME_STATE, refresh);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !HOME_STATE.isRefreshing) {
      refresh();
    }
  });

  setInterval(updateFreshnessIndicator, 1000);
});

async function refresh() {
  setLoading("summaryCards", true);
  try {
    const track = document.getElementById("trackFilter")?.value || "all";
    const family = document.getElementById("familyFilter")?.value || "all";
    const query = new URLSearchParams();
    if (track !== "all") query.set("track", track);
    if (family !== "all") query.set("family", family);
    const url = query.toString() ? `/api/runs?${query}` : "/api/runs";
    const response = await apiFetch(url);
    if (!response.ok) {
      showError(`Failed to load data (${response.status})`);
      return;
    }
    const data = await response.json();
    HOME_STATE.payload = data;
    HOME_STATE.lastUpdatedTimestamp = Date.now();
    updateFreshnessIndicator();
    const runs = data.runs || [];
    const familyCounts = {};
    for (const run of runs) {
      const fam = run.family || "";
      if (fam) familyCounts[fam] = (familyCounts[fam] || 0) + 1;
    }
    populateFamilyFilter(data.summary?.families || [], family, escapeHtml, familyCounts);

    // Pre-populate filters from most recent run on first load
    if (HOME_STATE._firstLoad && runs.length > 0 && track === "all" && family === "all") {
      const mostRecent = runs[0];
      const trackFilter = document.getElementById("trackFilter");
      const familyFilter = document.getElementById("familyFilter");
      if (trackFilter && mostRecent.track) {
        trackFilter.value = mostRecent.track;
        window.Alpine?.store?.("filter") && (window.Alpine.store("filter").track = mostRecent.track);
      }
      if (familyFilter && mostRecent.family) {
        familyFilter.value = mostRecent.family;
        window.Alpine?.store?.("filter") && (window.Alpine.store("filter").family = mostRecent.family);
      }
      HOME_STATE._firstLoad = false;
    }

    render();
  } catch (error) {
    if (error.name !== "AbortError") {
      showError(`Connection error: ${error.message}`);
    }
  } finally {
    setLoading("summaryCards", false);
  }
}

function render() {
  if (!HOME_STATE.payload) return;
  const runs = HOME_STATE.payload.runs || [];
  renderScope(runs);
  renderSummary(runs);
  renderRunCards(runs);
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
    if (!Number.isFinite(pointMetricValue(point, metricKey))) return best;
    if (!best) return point;
    return pointMetricValue(point, metricKey) > pointMetricValue(best, metricKey) ? point : best;
  }, null);
  const bestExperimentDetail = bestPoint
    ? `${bestPoint.family || "experiment"} at ${bestPoint.run_iteration_label || `run #${bestPoint.run_position || "n/a"}`} — ${metricMeta.label} ${metricMeta.formatter(pointMetricValue(bestPoint, metricKey))}`
    : "";
  const cards = [
    {
      label: "Visible Runs",
      value: `${summary.run_count || runs.length || 0}`,
      detail: "Visible run sessions in the current scope.",
    },
    {
      label: "Total Experiments",
      value: `${summary.experiment_count || 0}`,
      detail: "Total recorded experiments across the visible runs.",
    },
    {
      label: "Deployed",
      value: `${summary.deployd_count || 0}`,
      detail: "Deployments recorded inside the visible run set — shows adoption.",
    },
    {
      label: "Best Run + Score",
      value: summary.best_run_label
        ? `${summary.best_run_label} (${summary.best_aggregate_score != null ? formatNumber(summary.best_aggregate_score, 3) : "n/a"})`
        : "n/a",
      detail: summary.best_run_label
        ? `${summary.best_run_label} is the current strongest run by aggregate score. ${bestExperimentDetail}`
        : bestExperimentDetail || "No scored runs are visible.",
    },
  ];
  renderSummaryCards(container, cards);
}

function renderRunCards(runs) {
  const container = document.getElementById("runCards");
  if (!container) return;
  const metricKey = selectedMetricKey();
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  if (!runs.length) {
    container.innerHTML = `
      <article class="waiting-card waiting-card-empty-state">
        <div class="waiting-card-title">No runs recorded yet</div>
        <p class="waiting-card-copy">Start an experiment run and it will appear here.</p>
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
              <h3>${escapeHtml(run.run_label || "Unnamed run")}</h3>
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
          </div>
            ${seriesSvg}
          </div>
          <div class="run-card-stats">
            <div><span class="key">LLM / Burn-In</span><span>${escapeHtml(`${run.llm_experiment_count || 0} / ${run.deterministic_experiment_count || 0}`)}</span></div>
            <div><span class="key">Best Score</span><span>${escapeHtml(formatNumber(run.best_aggregate_score, 3))}</span></div>
            <div><span class="key">Best Validation</span><span>${escapeHtml(formatPercent(run.best_validation_total_return))}</span></div>
            <div><span class="key">Best Pre-Audit</span><span>${escapeHtml(formatPercent(run.best_pre_audit_canonical_total_return))}</span></div>
            <div><span class="key">Updated</span><span>${escapeHtml(formatDateTime(run.last_created_at))}</span></div>
          </div>
          <div class="run-card-links">
            <a class="button-link" aria-label="Open run page for ${escapeHtml(run.run_label || run.run_session_id)}" href="/runs/${encodeURIComponent(run.run_session_id)}">Open Run</a>
            ${run.best_spec_hash ? `<a class="table-link" aria-label="View best experiment for ${escapeHtml(run.run_label || run.run_session_id)}" href="/experiments/${encodeURIComponent(run.best_spec_hash)}">Best Experiment</a>` : ""}
          </div>
        </article>
      `;
    })
    .join("");
}

// sparklineSvg, pointMetricValue moved to chart-engine.js

function updateFreshnessIndicator() {
  const el = document.getElementById("freshnessIndicator");
  if (!el) return;
  if (!HOME_STATE.lastUpdatedTimestamp) {
    el.textContent = "";
    return;
  }
  const seconds = Math.floor((Date.now() - HOME_STATE.lastUpdatedTimestamp) / 1000);
  el.textContent = `Updated ${seconds}s ago`;
  el.className = "freshness-indicator" + (seconds > 30 ? " stale" : "");
}

})();

const state = {
  payload: null,
  selectedHash: null,
  selectedRunId: null,
  lockedRunId: null,
  runFilterTouched: false,
  autoRefreshTimer: null,
  isRefreshing: false,
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
  populateFamilyFilter,
  populateMetricFilter,
  rectNode,
  lineNode,
  textNode,
  historyRange,
  safeParseJson,
  showError,
  apiFetch,
  initAriaLive,
  setLoading,
  renderPolicySweepBlock,
  renderSummaryCards,
  initThemeToggle,
} = window.SigLabUi;

const TRACK_COLORS = {
  trend_signals: "#4ade80",
  yield_flows: "#f0b456",
};

const FAMILY_GUIDE = {
  perp_multi_asset_decision: {
    track: "trend_signals",
    title: "Multi-Asset Decision",
    summary: "Independent per-asset perp decisions instead of forced relative ranking.",
    execution: "Each asset can be long, short, or flat based on its own score and threshold.",
    hedge: "Perps only; can be net long, net short, mixed, or flat.",
  },
  perp_pair_trade_unlevered: {
    track: "trend_signals",
    title: "Pair Trade 1x",
    summary: "Relative-value perp spread between two assets with gross exposure capped at 1x.",
    execution: "Can be long the first leg, short the second, reverse the spread, or sit flat.",
    hedge: "The second leg is the hedge leg; leverage stays capped.",
  },
  perp_pair_trade_levered: {
    track: "trend_signals",
    title: "Pair Trade 3x",
    summary: "Relative-value perp spread between two assets with signal-scaled gross exposure up to 3x.",
    execution: "Can be long the first leg, short the second, reverse the spread, or sit flat.",
    hedge: "The second leg is the hedge leg; stronger signals can scale gross exposure higher.",
  },
  perp_basket_neutral_unlevered: {
    track: "trend_signals",
    title: "Basket Neutral 1x",
    summary: "Cross-sectional long/short perp basket that ranks assets and holds offsetting long and short books.",
    execution: "Buys the strongest-ranked names and shorts the weakest-ranked names with unlevered gross exposure.",
    hedge: "Long and short baskets offset each other; net exposure stays near neutral.",
  },
  perp_basket_neutral_levered: {
    track: "trend_signals",
    title: "Basket Neutral 3x",
    summary: "Cross-sectional long/short perp basket with signal-scaled leverage on both sides of the book.",
    execution: "Buys the strongest-ranked names and shorts the weakest-ranked names with higher gross target capacity.",
    hedge: "Long and short baskets offset each other, but stronger signals can scale gross exposure higher.",
  },
  perp_multi_asset_carry: {
    track: "trend_signals",
    title: "Multi-Asset Carry",
    summary: "Cross-sectional perp carry rotation that buys cheap carry and shorts rich carry across a perp basket.",
    execution: "Ranks assets by funding carry state, then builds long and short books from the best and worst carry profiles.",
    hedge: "Cross-sectional long/short basket; hedge quality varies with the selected long and short mix.",
  },
  basis_spread: {
    track: "yield_flows",
    title: "Basis Spread",
    summary: "Classic spot-versus-perp funding capture.",
    execution: "Long synthetic spot and short perp on the same asset.",
    hedge: "Underlying-neutral by construction.",
  },
  stable_pt_ladder: {
    track: "yield_flows",
    title: "Stable PT Ladder",
    summary: "Fixed-income style rotation among stable PT markets.",
    execution: "Buys discounted stable PTs and rolls forward before expiry.",
    hedge: "Usually unhedged because the basis is already USD-like.",
  },
  pt_yield_rotation: {
    track: "yield_flows",
    title: "PT Yield Rotation",
    summary: "Rotates across PT markets with attractive carry and discount dynamics.",
    execution: "Buys the highest-ranked PTs and can hedge beta with perps.",
    hedge: "Optional perp hedge on the PT underlying.",
  },
  lending_carry_rotation: {
    track: "yield_flows",
    title: "Lending Carry Rotation",
    summary: "Ranks lending markets by observed carry, rewards, and liquidity.",
    execution: "Allocates to the best lending opportunities and can hedge beta.",
    hedge: "Optional perp hedge on the basis root.",
  },
};

document.addEventListener("DOMContentLoaded", async () => {
  initAriaLive();
  initThemeToggle();
  state.lockedRunId = getRunId();
  if (state.lockedRunId) {
    state.selectedRunId = state.lockedRunId;
    state.runFilterTouched = true;
  }
  document.getElementById("refreshButton")?.addEventListener("click", () => refresh());
  document.getElementById("trackFilter")?.addEventListener("change", () => {
    if (state.lockedRunId) return;
    state.selectedRunId = null;
    state.runFilterTouched = false;
    refresh();
  });
  document.getElementById("familyFilter")?.addEventListener("change", () => {
    if (!state.lockedRunId) {
      state.selectedRunId = null;
      state.runFilterTouched = false;
    }
    refresh();
  });
  document.getElementById("metricFilter")?.addEventListener("change", () => render());
  document.getElementById("autoRefresh")?.addEventListener("change", () => toggleAutoRefresh(state, refresh));
  document.getElementById("clearFamilyFilter")?.addEventListener("click", () => {
    const familyFilter = document.getElementById("familyFilter");
    if (familyFilter) {
      familyFilter.value = "all";
    }
    if (!state.lockedRunId) {
      state.selectedRunId = null;
      state.runFilterTouched = false;
    }
    refresh();
  });

  populateMetricFilter("metricFilter", null, document.getElementById("metricFilter")?.value);
  await refresh();
  toggleAutoRefresh(state, refresh);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !state.isRefreshing) {
      refresh();
    }
  });
});

async function refresh() {
  setLoading("summaryCards", true);
  try {
    const track = document.getElementById("trackFilter")?.value || "all";
    const family = document.getElementById("familyFilter")?.value || "all";
    const query = new URLSearchParams();
    if (track !== "all") query.set("track", track);
    if (family && family !== "all") query.set("family", family);
    const url = query.toString() ? `/api/experiments?${query}` : "/api/experiments";
    const response = await apiFetch(url);
    if (!response.ok) {
      showError(`Failed to load data (${response.status})`);
      return;
    }
    const data = await response.json();
    state.payload = data;
    populateFamilyFilter(collectFamilies(state.payload), family, escapeHtml);
    if (state.lockedRunId) {
      state.selectedRunId = state.lockedRunId;
    }
    const runs = state.payload?.runs || [];
    if (!runs.some((row) => row.run_session_id === state.selectedRunId)) {
      state.selectedRunId = state.lockedRunId ? state.lockedRunId : null;
    }
    if (!state.lockedRunId && !state.runFilterTouched && !state.selectedRunId && runs.length) {
      state.selectedRunId = runs[0].run_session_id;
    }

    const experiments = filteredExperiments(state.payload.experiments || []);
    if (!experiments.some((row) => row.spec_hash === state.selectedHash)) {
      const deployd = [...experiments].reverse().find((row) => row.deployd);
      state.selectedHash = deployd?.spec_hash || experiments.at(-1)?.spec_hash || null;
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
  if (!state.payload) return;

  const runs = filteredRuns(state.payload.runs || []);
  const experiments = filteredExperiments(state.payload.experiments || []);
  const metricKey = selectedMetricKey();
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  if (!experiments.some((row) => row.spec_hash === state.selectedHash)) {
    state.selectedHash = experiments.at(-1)?.spec_hash || null;
  }
  renderScope(experiments);
  renderSummary(experiments, runs);

  renderFamilyGuide();
  renderChart(experiments);
  renderTable(experiments);
  renderDetail(state.selectedHash);
  const selectorMeta = state.payload.selector_metric || {};
  const metricDescription = document.getElementById("metricDescription");
  if (metricDescription) {
    metricDescription.textContent = metricKey === "aggregate_score"
      ? (selectorMeta.description || metricMeta.description || "Best-so-far and all recorded experiments.")
      : `${metricMeta.description} Selector remains ${selectorMeta.label || "Aggregate Score"}.`;
  }

}

function selectedRunRow() {
  return (state.payload?.runs || []).find((row) => row.run_session_id === state.selectedRunId) || null;
}

function runWaitingMessage(run) {
  const label = run?.run_label || run?.run_session_id || "this run";
  return {
    title: "Hold tight",
    summary: `${label} has started, but no experiment rows have landed yet.`,
    detail: "SigLab is likely fetching market data, compiling the first spec, or finishing the first evaluation.",
  };
}

function runStatusClass(status) {
  if (status === "deployd" || status === "pass") {
    return "status-pass";
  }
  if (status === "running" || status === "starting") {
    return "status-pending";
  }
  return "status-fail";
}

function collectFamilies(payload) {
  const familySet = new Set(payload?.summary?.families || []);
  (payload?.experiments || [])
    .filter((row) => !state.lockedRunId || row.run_session_id === state.lockedRunId)
    .forEach((row) => {
    if (row.family) familySet.add(row.family);
    });
  return [...familySet].sort();
}


function filteredExperiments(experiments) {
  const family = document.getElementById("familyFilter")?.value || "all";
  let filtered = family === "all" ? experiments : experiments.filter((row) => row.family === family);
  if (state.selectedRunId) {
    filtered = filtered.filter((row) => row.run_session_id === state.selectedRunId);
  }
  return filtered;
}

function filteredRuns(runs) {
  if (state.lockedRunId) {
    return runs.filter((row) => row.run_session_id === state.lockedRunId);
  }
  const family = document.getElementById("familyFilter")?.value || "all";
  if (family === "all") {
    return runs;
  }
  return runs.filter((row) => (row.families || []).includes(family));
}

function renderScope(experiments) {
  const selectedRun = selectedRunRow();
  const track = document.getElementById("trackFilter")?.value || selectedRun?.track || "all";
  const family = document.getElementById("familyFilter")?.value || "all";
  const scopeLabel = [
    track === "all" ? "all tracks" : TRACK_LABELS[track] || track,
    family === "all" ? "all families" : family,
    selectedRun ? `run ${selectedRun.run_label || selectedRun.run_session_id}` : "all runs",
  ].join(" / ");
  const scopeSummary = document.getElementById("scopeSummary");
  if (scopeSummary) {
    scopeSummary.textContent =
      `Viewing ${scopeLabel}. ${experiments.length} experiment${experiments.length === 1 ? "" : "s"} in scope.`;
  }
  const experimentsSubtitle = document.getElementById("experimentsSubtitle");
  if (experimentsSubtitle) {
    experimentsSubtitle.textContent =
      selectedRun
        ? `Showing experiments for ${selectedRun.runner_label} / ${selectedRun.run_label}.`
        : family === "all"
          ? "Every recorded generation in the current track scope. Click a row or chart point for full detail."
          : `Every recorded generation for ${family}. Click a row or chart point for full detail.`;
  }

  const runTitle = document.getElementById("runTitle");
  if (runTitle && selectedRun) {
    runTitle.textContent = selectedRun.run_label || selectedRun.run_session_id;
  }
  const runSubtitle = document.getElementById("runSubtitle");
  if (runSubtitle && selectedRun) {
    const llmLabel = llmIdentity(selectedRun.llm_provider, selectedRun.llm_model);
    runSubtitle.textContent =
      `${TRACK_LABELS[selectedRun.track] || selectedRun.track || "Unknown Track"} • ${selectedRun.runner_label || "unknown"} • ${selectedRun.run_kind || "harness"}${llmLabel !== "n/a" ? ` • ${llmLabel}` : ""}`;
  }
}

function renderSummary(experiments, runs) {
  const container = document.getElementById("summaryCards");
  if (!container) return;
  const metricKey = selectedMetricKey();
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  const bestDirectional = bestExperiment(experiments, "trend_signals", metricKey);
  const bestCarry = bestExperiment(experiments, "yield_flows", metricKey);
  const benchmarkRuns = runs.filter((row) => row.benchmark_mode);
  const selectedRun = selectedRunRow();

  const cards = [
    {
      label: "Runs",
      value: `${runs.length}`,
      detail: "Visible run sessions under the current track and family filters.",
    },
    {
      label: "Benchmark Runs",
      value: `${benchmarkRuns.length}`,
      detail: "Visible external-agent benchmark sessions in the current scope.",
    },
    {
      label: "Experiments",
      value: `${experiments.length}`,
      detail: "Visible experiments inside the current run, track, and family filters.",
    },
    {
      label: "Deployed",
      value: `${experiments.filter((row) => row.deployd).length}`,
      detail: "Visible experiments that currently lead or previously led a track.",
    },
    {
      label: "Tool Traces",
      value: `${experiments.filter((row) => Number(row.tool_call_count || 0) > 0).length}`,
      detail: "Experiments whose planner or writer stage issued at least one recorded tool call.",
    },
    {
      label: "Best Directional",
      value: bestDirectional ? metricMeta.formatter(metricValue(bestDirectional, metricKey)) : "n/a",
      valueClass: bestDirectional ? "" : "small",
      detail: bestDirectional
        ? `${bestDirectional.generation} generations, ${metricMeta.label.toLowerCase()} ${metricMeta.formatter(metricValue(bestDirectional, metricKey))}`
        : "No directional experiments in this view.",
    },
    {
      label: "Best Carry",
      value: bestCarry ? metricMeta.formatter(metricValue(bestCarry, metricKey)) : "n/a",
      valueClass: bestCarry ? "" : "small",
      detail: bestCarry
        ? `${bestCarry.generation} generations, ${metricMeta.label.toLowerCase()} ${metricMeta.formatter(metricValue(bestCarry, metricKey))}`
        : "No carry experiments in this view.",
    },
  ];
  if (selectedRun && experiments.length === 0) {
    const waiting = runWaitingMessage(selectedRun);
    cards.unshift({
      label: waiting.title,
      value: "Run warming up",
      valueClass: "small",
      detail: `${waiting.summary} ${waiting.detail}`,
    });
  }

  renderSummaryCards(container, cards);
}



function renderFamilyGuide() {
  const container = document.getElementById("familyGuideCards");
  const subtitle = document.getElementById("familyGuideSubtitle");
  if (!container || !subtitle) return;
  const track = document.getElementById("trackFilter")?.value || "all";
  const selectedFamily = document.getElementById("familyFilter")?.value || "all";
  const visibleFamilies = collectFamilies(state.payload).filter((family) => {
    const meta = FAMILY_GUIDE[family];
    if (!meta) return true;
    return track === "all" || meta.track === track;
  });

  subtitle.textContent =
    selectedFamily === "all"
      ? "Families"
      : `Filtered to ${selectedFamily}`;

  if (!visibleFamilies.length) {
    container.innerHTML = `<span class="pill">No families in scope</span>`;
    return;
  }

  container.innerHTML = visibleFamilies
    .map((family) => {
      const meta = FAMILY_GUIDE[family];
      const label = meta?.title || family;
      const active = selectedFamily === family ? " active" : "";
      return `<span class="pill${active}" data-family="${escapeHtml(family)}">${escapeHtml(label)}</span>`;
    })
    .join("");

  container.querySelectorAll(".pill").forEach((pill) => {
    pill.setAttribute("tabindex", "0");
    pill.setAttribute("role", "button");
    pill.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        pill.click();
      }
    });
    pill.addEventListener("click", () => {
      const familyFilter = document.getElementById("familyFilter");
      if (familyFilter) {
        familyFilter.value = pill.dataset.family || "all";
      }
      refresh();
    });
  });
}

function renderChart(experiments) {
  const svg = document.getElementById("chart");
  const tooltip = document.getElementById("tooltip");
  tooltip.classList.add("hidden");
  svg.innerHTML = "";

  if (experiments.length === 0) {
    const selectedRun = selectedRunRow();
    const message = selectedRun
      ? "Hold tight. This run has started, but the first experiment has not finished yet."
      : "No experiments recorded yet.";
    svg.innerHTML = `<text x="48" y="56" fill="#6b7f70" font-family="Inter, sans-serif">${escapeHtml(message)}</text>`;
    return;
  }

  const metricKey = selectedMetricKey();
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  const container = svg.parentElement;
  const width = Math.max(400, container?.clientWidth || 1200);
  const height = 460;
  const margin = { top: 24, right: 24, bottom: 48, left: 62 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;

  const tracks = groupByTrack(experiments);
  const values = experiments
    .map((exp) => metricValue(exp, metricKey))
    .filter((value) => Number.isFinite(value));
  if (!values.length) {
    svg.innerHTML = `<text x="48" y="56" fill="#6b7f70" font-family="Inter, sans-serif">No finite values are available for ${escapeHtml(metricMeta.label)}.</text>`;
    return;
  }
  let yMin = Math.min(...values);
  let yMax = Math.max(...values);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const yPadding = (yMax - yMin) * 0.12;
  yMin -= yPadding;
  yMax += yPadding;

  const xMax = Math.max(...experiments.map((exp) => chartXValue(exp)), 1);
  const xScale = (generation) =>
    margin.left + ((generation - 1) / Math.max(xMax - 1, 1)) * plotWidth;
  const yScale = (value) =>
    margin.top + plotHeight - ((value - yMin) / (yMax - yMin)) * plotHeight;

  svg.appendChild(rectNode(0, 0, width, height, "transparent"));

  for (let i = 0; i <= 5; i += 1) {
    const value = yMin + ((yMax - yMin) * i) / 5;
    const y = yScale(value);
    svg.appendChild(lineNode(margin.left, y, width - margin.right, y, "rgba(255,255,255,0.04)", 1));
    svg.appendChild(textNode(14, y + 4, metricMeta.formatter(value), "#6b7f70", "12"));
  }

  for (let i = 0; i <= Math.min(xMax - 1, 5); i += 1) {
    const generation = 1 + Math.round((xMax - 1) * (i / Math.max(Math.min(xMax - 1, 5), 1)));
    const x = xScale(generation);
    svg.appendChild(lineNode(x, margin.top, x, height - margin.bottom, "rgba(255,255,255,0.02)", 1));
    svg.appendChild(textNode(x - 8, height - 16, `${generation}`, "#6b7f70", "12"));
  }

  svg.appendChild(lineNode(margin.left, height - margin.bottom, width - margin.right, height - margin.bottom, "rgba(255,255,255,0.08)", 1));
  svg.appendChild(lineNode(margin.left, margin.top, margin.left, height - margin.bottom, "rgba(255,255,255,0.08)", 1));

  Object.entries(tracks).forEach(([track, rows]) => {
    const color = TRACK_COLORS[track] || "#4ade80";
    let best = -Infinity;
    const points = rows
      .map((row) => {
        const value = metricValue(row, metricKey);
        if (!Number.isFinite(value)) return null;
        best = Math.max(best, value);
        return `${xScale(chartXValue(row))},${yScale(best)}`;
      })
      .filter(Boolean);
    if (!points.length) {
      return;
    }
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", color);
    polyline.setAttribute("stroke-width", "2.5");
    polyline.setAttribute("stroke-linecap", "round");
    polyline.setAttribute("stroke-linejoin", "round");
    polyline.setAttribute("points", points.join(" "));
    svg.appendChild(polyline);

    rows.forEach((row) => {
      const value = metricValue(row, metricKey);
      if (!Number.isFinite(value)) {
        return;
      }
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", `${xScale(chartXValue(row))}`);
      circle.setAttribute("cy", `${yScale(value)}`);
      circle.setAttribute("r", row.deployd ? "9" : "5.5");
      circle.setAttribute("fill", row.passed ? color : "rgba(255,255,255,0.12)");
      circle.setAttribute("stroke", row.deployd ? "#fff" : "rgba(255,255,255,0.2)");
      circle.setAttribute("stroke-width", row.deployd ? "2" : "1.2");
      circle.setAttribute("aria-label", `${row.family} - ${metricMeta.formatter(metricValue(row, metricKey))}`);
      circle.style.cursor = "pointer";
      circle.setAttribute("tabindex", "0");
      circle.setAttribute("role", "button");
      circle.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          circle.click();
        }
      });
      circle.addEventListener("mouseenter", (event) => showTooltip(event, row, metricKey));
      circle.addEventListener("mousemove", (event) => moveTooltip(event));
      circle.addEventListener("mouseleave", () => tooltip.classList.add("hidden"));
      circle.addEventListener("click", () => {
        state.selectedHash = row.spec_hash;
        renderTable(experiments);
        renderDetail(state.selectedHash);
      });
      svg.appendChild(circle);
    });
  });

  svg.appendChild(
    textNode(
      margin.left,
      18,
      `${metricMeta.label} by ${state.selectedRunId ? "run order" : "generation"}`,
      "#e2ebe5",
      "14",
      "600"
    )
  );
}

function renderTable(experiments) {
  const tbody = document.getElementById("experimentsTable");
  tbody.innerHTML = "";
  const metricKey = selectedMetricKey();
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  if (!experiments.length) {
    const selectedRun = selectedRunRow();
    const waiting = selectedRun ? runWaitingMessage(selectedRun) : null;
    const message = waiting
      ? `${waiting.title}: ${waiting.summary} ${waiting.detail}`
      : "No experiments recorded for the current scope.";
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state">${escapeHtml(message)}</td></tr>`;
    return;
  }

  [...experiments]
    .sort((a, b) => compareByMetricThenTime(a, b, metricKey))
    .forEach((row) => {
      const tr = document.createElement("tr");
      tr.setAttribute("aria-label", `View experiment detail for ${row.family} - ${row.spec_hash}`);
      if (row.spec_hash === state.selectedHash) {
        tr.classList.add("selected");
      }
      tr.setAttribute("tabindex", "0");
      tr.setAttribute("role", "button");
      // Store hidden column data as data attributes for future expansion
      tr.dataset.runIter = runIterationLabel(row);
      tr.dataset.lineage = String(row.global_index || row.generation || "n/a");
      tr.dataset.mode = modeCellLabel(row);
      tr.dataset.tools = String(row.tool_call_count || 0);
      tr.dataset.validationAudit = outOfSampleLabel(row.summary || {});
      tr.dataset.specHash = row.spec_hash;
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          state.selectedHash = row.spec_hash;
          renderTable(experiments);
          renderDetail(state.selectedHash);
        }
      });
      tr.innerHTML = `
        <td>${escapeHtml(TRACK_LABELS[row.track] || row.track)}</td>
        <td>${escapeHtml(row.family)}</td>
        <td>${escapeHtml(METRIC_META.aggregate_score.formatter(row.summary?.aggregate_score ?? 0))}</td>
        <td>${escapeHtml(METRIC_META.median_sharpe.formatter(row.summary?.median_sharpe ?? 0))}</td>
        <td>${escapeHtml(METRIC_META.median_cagr.formatter(row.summary?.median_cagr ?? 0))}</td>
        <td>${escapeHtml(METRIC_META.median_total_return.formatter(row.summary?.median_total_return ?? 0))}</td>
        <td class="${row.passed ? "status-pass" : "status-fail"}">${row.passed ? "pass" : "fail"}${row.deployd ? " / deployed" : ""}</td>
        <td>${escapeHtml(formatDateTime(row.created_at))}</td>
      `;
      tr.addEventListener("click", () => {
        state.selectedHash = row.spec_hash;
        renderTable(experiments);
        renderDetail(state.selectedHash);
      });
      tbody.appendChild(tr);
    });
}

function focusFirstHeading(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const heading = container.querySelector("h3, h2, h1");
  if (heading) {
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }
}

async function renderDetail(specHash) {
  const container = document.getElementById("detailContent");
  if (!specHash) {
    const selectedRun = selectedRunRow();
    const experiments = filteredExperiments(state.payload?.experiments || []);
    if (selectedRun && experiments.length === 0) {
      const waiting = runWaitingMessage(selectedRun);
      container.innerHTML = `
        <article class="waiting-card">
          <div class="waiting-card-title">${escapeHtml(waiting.title)}</div>
          <p class="waiting-card-copy">${escapeHtml(waiting.summary)}</p>
          <p class="waiting-card-copy">${escapeHtml(waiting.detail)}</p>
        </article>
      `;
      return;
    }
    container.innerHTML = `<p class="empty-state">Select an experiment to inspect it.</p>`;
    return;
  }

  const response = await apiFetch(`/api/experiments/${specHash}`);
  if (!response.ok) {
    container.innerHTML = `<p class="empty-state">Unable to load experiment detail.</p>`;
    return;
  }
  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    container.innerHTML = `<p class="empty-state">Failed to parse experiment detail: ${error.message}</p>`;
    return;
  }
  const experiment = payload.experiment;
  const artifact = experiment.artifact || {};
  const spec = experiment.spec || {};
  const summary = experiment.summary || {};
  const compiledMetadata = artifact.compiled_metadata || artifact.compiledMetadata || {};
  const windows = artifact.windows || [];
  const timing = experiment.timing || {};
  const biasControls = timing.bias_controls || {};
  const toolTrace = experiment.tool_trace || {};
  const toolCalls = toolTrace.tool_calls || [];
  const toolTraceStages = (experiment.tool_trace_stages || [])
    .filter((stage) => stage && (stage.tool_calls || stage.error || stage.model || stage.trace_path));
  const researchSummaryView = { ...(experiment.research_summary || {}) };
  const rollLifecycle = experiment.roll_lifecycle || {};
  const rollEvents = rollLifecycle.roll_events || [];
  delete researchSummaryView.llm_tool_trace;
  const policySweepBlock = renderPolicySweepBlock(summary, experiment.family || spec.family, { heading: "Policy Sweep Comparison", winnerLabel: "Winner" });

  container.innerHTML = `
    <div class="detail-grid">
      <div class="detail-block full-width detail-hero-block">
        <div class="detail-hero-top">
          <div>
            <h3>${escapeHtml(spec.family || experiment.family)} <span class="mono">${escapeHtml(experiment.spec_hash)}</span></h3>
            <div class="detail-actions">
              <a class="table-link" href="/experiments/${encodeURIComponent(experiment.spec_hash)}">Open Full Experiment Page &rarr;</a>
            </div>
          </div>
          <div class="detail-status-badge ${experiment.passed ? "status-pass" : "status-fail"}">${experiment.passed ? "Passed" : "Failed"}${experiment.deployd ? " / Deployed" : ""}</div>
        </div>
        <div class="detail-hero-stats">
          <div class="detail-stat"><span class="detail-stat-label">Score</span><span class="detail-stat-value">${escapeHtml(formatNumber(summary.aggregate_score, 3))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Sharpe</span><span class="detail-stat-value">${escapeHtml(formatNumber(summary.median_sharpe, 3))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">CAGR</span><span class="detail-stat-value">${escapeHtml(formatPercent(summary.median_cagr ?? 0))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Selector Return</span><span class="detail-stat-value">${escapeHtml(formatPercent(summary.median_total_return))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Pre-Audit Return</span><span class="detail-stat-value">${escapeHtml(formatPercent(summary.pre_audit_canonical_total_return))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Calmar</span><span class="detail-stat-value">${escapeHtml(formatNumber(summary.median_calmar, 3))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Worst DD</span><span class="detail-stat-value">${escapeHtml(formatPercent(summary.worst_max_drawdown))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Track</span><span class="detail-stat-value">${escapeHtml(TRACK_LABELS[experiment.track] || experiment.track)}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Agent</span><span class="detail-stat-value">${escapeHtml(experiment.runner_label || "unknown")}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Run</span><span class="detail-stat-value">${escapeHtml(experiment.run_label || experiment.run_session_id || "n/a")}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Run Iter</span><span class="detail-stat-value">${escapeHtml(runIterationLabel(experiment))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Lineage Row</span><span class="detail-stat-value">${escapeHtml(String(experiment.global_index || experiment.generation || "n/a"))}</span></div>
          <div class="detail-stat"><span class="detail-stat-label">Created</span><span class="detail-stat-value">${escapeHtml(formatDateTime(experiment.created_at))}</span></div>
        </div>
      </div>

      <div class="detail-block full-width">
        <h3>Research Surface</h3>
        <p class="detail-copy">${escapeHtml(spec.hypothesis || "No hypothesis recorded.")}</p>
        <div class="pill-row">
          ${(spec.features || []).map((feature) => `<span class="pill">${escapeHtml(feature)}</span>`).join("")}
        </div>
        <div class="pill-row meta-row">
          <span class="pill amber">feature hash ${escapeHtml(experiment.feature_hash || compiledMetadata.feature_hash || "n/a")}</span>
          <span class="pill amber">${escapeHtml(modeLabel(experiment.mode_flags || {}))}</span>
          <span class="pill amber">${escapeHtml(compiledMetadata.source || experiment.source || "unknown source")}</span>
          <span class="pill amber">tool calls ${escapeHtml(String(toolCalls.length))}</span>
          ${(rollLifecycle.badges || []).map((badge) => `<span class="pill slate">${escapeHtml(badge)}</span>`).join("")}
        </div>
      </div>

      <div class="detail-block">
        <h3>LLM Research Trace</h3>
        <div class="kv">
          <div class="key">Primary Model</div><div>${escapeHtml(toolTrace.model || "n/a")}</div>
          <div class="key">Thinking</div><div>${escapeHtml(toolTrace.thinking_mode || "default")}</div>
          <div class="key">Stages</div><div>${escapeHtml(String(toolTraceStages.length || 0))}</div>
          <div class="key">Tool Rounds</div><div>${escapeHtml(String(toolTrace.tool_rounds_used ?? 0))}</div>
          <div class="key">Tool Calls</div><div>${escapeHtml(String(toolCalls.length))}</div>
          <div class="key">Parent</div><div>${escapeHtml(toolTrace.parent_family || "n/a")} ${toolTrace.parent_hash ? `<span class="mono">${escapeHtml(toolTrace.parent_hash)}</span>` : ""}</div>
          <div class="key">Response</div><div>${escapeHtml(toolTrace.response_finish_reason || "n/a")}</div>
        </div>
        ${toolTrace.error ? `<p class="detail-copy">Trace error: ${escapeHtml(toolTrace.error)}</p>` : ""}
        ${toolTraceStages.length > 0 ? `
          <div class="trace-list">
            ${toolTraceStages.map((stage, stageIndex) => `
              <article class="trace-call">
                <div class="trace-call-head">
                  <strong>${escapeHtml(`${stageIndex + 1}. ${stage.stage || "stage"}`)}</strong>
                  <span class="mono">${escapeHtml(stage.trace_path || stage.model || "")}</span>
                </div>
                <div class="kv">
                  <div class="key">Model</div><div>${escapeHtml(stage.model || "n/a")}</div>
                  <div class="key">Thinking</div><div>${escapeHtml(stage.thinking_mode || "default")}</div>
                  <div class="key">Tool Rounds</div><div>${escapeHtml(String(stage.tool_rounds_used ?? 0))}</div>
                  <div class="key">Tool Calls</div><div>${escapeHtml(String((stage.tool_calls || []).length))}</div>
                  <div class="key">Response</div><div>${escapeHtml(stage.response_finish_reason || "n/a")}</div>
                </div>
                ${stage.error ? `<p class="detail-copy">Trace error: ${escapeHtml(stage.error)}</p>` : ""}
                ${(stage.tool_calls || []).length > 0 ? `
                  <div class="trace-list">
                    ${(stage.tool_calls || []).map((call, index) => `
                      <article class="trace-call trace-call-nested">
                        <div class="trace-call-head">
                          <strong>${escapeHtml(`${index + 1}. ${call.name || "tool"}`)}</strong>
                          <span class="mono">${escapeHtml(call.id || "")}</span>
                        </div>
                        <pre>${escapeHtml(JSON.stringify({
                          arguments: safeParseJson(call.arguments),
                          result: call.result,
                        }, null, 2))}</pre>
                      </article>
                    `).join("")}
                  </div>
                ` : `<p class="empty-state">No tool calls were recorded for this stage.</p>`}
                ${stage.final_content_preview ? `
                  <div class="trace-preview">
                    <h4>${escapeHtml((stage.stage || "stage") + " response preview")}</h4>
                    <pre>${escapeHtml(stage.final_content_preview)}</pre>
                  </div>
                ` : ""}
              </article>
            `).join("")}
          </div>
        ` : `<p class="empty-state">No trace metadata was recorded for this experiment.</p>`}
        ${toolTrace.final_content_preview ? `
          <div class="trace-preview">
            <h4>Primary Response Preview</h4>
            <pre>${escapeHtml(toolTrace.final_content_preview)}</pre>
          </div>
        ` : ""}
      </div>

      <div class="detail-block">
        <h3>Bias Audit</h3>
        <div class="kv">
          <div class="key">Timing</div><div>${escapeHtml(timing.signal_timing || "unknown")}</div>
          <div class="key">Bundle As Of</div><div>${escapeHtml(timing.bundle_as_of || "n/a")}</div>
          <div class="key">History Range</div><div>${escapeHtml(historyRange(timing.history_start, timing.history_end))}</div>
          <div class="key">Shift Bars</div><div>${escapeHtml(String(biasControls.position_shift_bars ?? "n/a"))}</div>
          <div class="key">Dropped Last Bar</div><div>${escapeHtml(String(Boolean(biasControls.dropped_last_bar)))}</div>
          <div class="key">Leak Checks</div><div class="${biasControls.leak_checks_passed ? "status-pass" : "status-fail"}">${biasControls.leak_checks_passed ? "passed" : "review"}</div>
        </div>
      </div>

      ${policySweepBlock}

      <div class="detail-block">
        <h3>PT Roll Forward</h3>
        <div class="kv">
          <div class="key">Open End</div><div>${escapeHtml(rollLifecycle.policy?.open_ended_policy || "n/a")}</div>
          <div class="key">Universe</div><div>${escapeHtml(rollLifecycle.policy?.pt_universe_policy || "n/a")}</div>
          <div class="key">Roll Target</div><div>${escapeHtml(rollLifecycle.policy?.roll_target_policy || "n/a")}</div>
          <div class="key">Roll Cost</div><div>${escapeHtml(rollLifecycle.policy?.roll_cost_model || "n/a")}</div>
          <div class="key">Roll Window</div><div>${escapeHtml(String(rollLifecycle.policy?.roll_days_before_expiry ?? "n/a"))}</div>
          <div class="key">Roll Events</div><div>${escapeHtml(String(rollLifecycle.roll_event_count || 0))}</div>
          <div class="key">Eligible Markets</div><div>${escapeHtml(eligibleRangeLabel(rollLifecycle))}</div>
          <div class="key">New Listings</div><div>${escapeHtml(String((rollLifecycle.markets_entered_during_backtest || []).length))}</div>
        </div>
        ${(rollLifecycle.markets_entered_during_backtest || []).length > 0 ? `
          <div class="pill-row meta-row">
            ${(rollLifecycle.markets_entered_during_backtest || []).map((label) => `<span class="pill slate">${escapeHtml(label)}</span>`).join("")}
          </div>
        ` : ""}
        ${rollEvents.length > 0 ? `
          <div class="trace-list">
            ${rollEvents.map((event, index) => `
              <article class="trace-call">
                <div class="trace-call-head">
                  <strong>${escapeHtml(`${index + 1}. ${event.reason || "roll"}`)}</strong>
                  <span class="mono">${escapeHtml(event.timestamp || "")}</span>
                </div>
                <pre>${escapeHtml(JSON.stringify({
                  from_markets: event.from_markets,
                  to_markets: event.to_markets,
                  from_days_to_expiry: event.from_days_to_expiry,
                  to_days_to_expiry: event.to_days_to_expiry,
                  eligible_market_count: event.eligible_market_count,
                  selected_market_count: event.selected_market_count,
                }, null, 2))}</pre>
              </article>
            `).join("")}
          </div>
        ` : `<p class="empty-state">No PT roll events were recorded for this experiment.</p>`}
      </div>

      <div class="detail-block">
        <h3>Spec Config</h3>
        <pre>${escapeHtml(JSON.stringify({
          neutrality_basis: spec.neutrality_basis,
          universe: spec.universe,
          risk: spec.risk,
          params: spec.params,
          research_summary: researchSummaryView,
        }, null, 2))}</pre>
      </div>

      <div class="detail-block">
        <h3>Compiled Metadata</h3>
        <pre>${escapeHtml(JSON.stringify(compiledMetadata, null, 2))}</pre>
      </div>

      <div class="detail-block">
        <h3>Windows</h3>
        <pre>${escapeHtml(JSON.stringify(windows, null, 2))}</pre>
      </div>

      <div class="detail-block full-width">
        <h3>Artifact</h3>
        <div class="mono" style="font-size: 12px; color: var(--muted);">${escapeHtml(experiment.artifact_path || "No artifact path")}</div>
      </div>
    </div>
  `;
  focusFirstHeading("detailContent");
}

function showTooltip(event, row, metricKey) {
  const tooltip = document.getElementById("tooltip");
  const metricMeta = METRIC_META[metricKey] || METRIC_META.aggregate_score;
  tooltip.innerHTML = `
    <div class="meta">${escapeHtml(TRACK_LABELS[row.track] || row.track)} &middot; ${escapeHtml(runIterationLabel(row))} &middot; row ${escapeHtml(String(row.global_index || row.generation || "n/a"))}</div>
    <strong>${escapeHtml(row.family)}</strong>
    <div>${metricMeta.label}: ${escapeHtml(metricMeta.formatter(metricValue(row, metricKey)))}</div>
    <div>Sharpe: ${escapeHtml(formatNumber(row.summary?.median_sharpe ?? 0, 3))}</div>
    <div>CAGR: ${escapeHtml(formatPercent(row.summary?.median_cagr ?? 0))}</div>
    <div>Selector Return: ${escapeHtml(formatPercent(row.summary?.median_total_return ?? 0))}</div>
    <div>Pre-Audit Return: ${escapeHtml(formatPercent(row.summary?.pre_audit_canonical_total_return ?? 0))}</div>
    <div>Validation / Audit: ${escapeHtml(outOfSampleLabel(row.summary || {}))}</div>
    <div>Tools: ${escapeHtml(String(row.tool_call_count || 0))}</div>
    <div>Rolls: ${escapeHtml(String(row.roll_lifecycle?.roll_event_count || 0))}</div>
    <div>Mode: ${escapeHtml(modeCellLabel(row))}</div>
    <div>Source: ${escapeHtml(row.source || "unknown")}</div>
    <div class="${row.passed ? "status-pass" : "status-fail"}">${row.passed ? "Passed" : "Failed"}${row.deployd ? " / Deployed" : ""}</div>
  `;
  tooltip.classList.remove("hidden");
  moveTooltip(event);
}

function moveTooltip(event) {
  const tooltip = document.getElementById("tooltip");
  const bounds = document.querySelector(".chart-wrap").getBoundingClientRect();
  const tooltipWidth = 280;
  const tooltipHeight = 300;

  let left = event.clientX - bounds.left + 14;
  let top = event.clientY - bounds.top + 12;

  if (left + tooltipWidth > bounds.width) {
    left = bounds.width - tooltipWidth - 8;
  }
  if (left < 0) left = 8;
  if (top + tooltipHeight > bounds.height) {
    top = bounds.height - tooltipHeight - 8;
  }
  if (top < 0) top = 8;

  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function bestExperiment(experiments, track, metricKey) {
  const rows = experiments.filter(
    (row) => row.track === track && Number.isFinite(metricValue(row, metricKey))
  );
  if (rows.length === 0) return null;
  return rows.reduce((best, row) => {
    if (!best) return row;
    return metricValue(row, metricKey) > metricValue(best, metricKey)
      ? row
      : best;
  }, null);
}

function groupByTrack(experiments) {
  return experiments.reduce((acc, experiment) => {
    if (!acc[experiment.track]) acc[experiment.track] = [];
    acc[experiment.track].push(experiment);
    return acc;
  }, {});
}

function runIterationLabel(row) {
  if (row.run_iteration_label) return String(row.run_iteration_label);
  if (row.run_iteration_number != null) return `iter ${row.run_iteration_number}`;
  if (row.run_position != null) return `run #${row.run_position}`;
  return "n/a";
}

function chartXValue(row) {
  if (state.selectedRunId || state.lockedRunId) {
    return Number(row.run_position || row.run_iteration_number || row.generation || 1);
  }
  return Number(row.generation || 1);
}

function getRunId() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const runsIndex = parts.indexOf("runs");
  if (runsIndex >= 0 && parts[runsIndex + 1]) {
    return decodeURIComponent(parts[runsIndex + 1]);
  }
  return null;
}

function modeLabel(flags) {
  const parts = [];
  if (flags.long_enabled !== undefined || flags.short_enabled !== undefined) {
    parts.push(`L ${flags.long_enabled === false ? "off" : "on"}`);
    parts.push(`S ${flags.short_enabled ? "on" : "off"}`);
  }
  if (flags.hedge_mode && flags.hedge_mode !== "none") {
    parts.push(`hedge ${flags.hedge_mode} ${formatNumber(flags.hedge_ratio ?? 0, 2)}`);
  }
  return parts.length > 0 ? parts.join(" / ") : "unhedged";
}

function modeCellLabel(row) {
  const base = modeLabel(row.mode_flags || {});
  const badges = row.roll_lifecycle?.badges || [];
  if (!badges.length) return base;
  return `${base} / ${badges.join(", ")}`;
}

function metricValue(row, metricKey) {
  let value = row?.summary?.[metricKey];
  if (value === undefined && metricKey.startsWith("validation_")) {
    value = row?.summary?.[metricKey.replace("validation_", "holdout_")];
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : Number.NEGATIVE_INFINITY;
}

function compareByMetricThenTime(a, b, metricKey) {
  const metricDiff = metricValue(b, metricKey) - metricValue(a, metricKey);
  if (Number.isFinite(metricDiff) && Math.abs(metricDiff) > 1e-12) {
    return metricDiff;
  }
  return new Date(b.created_at) - new Date(a.created_at);
}

function outOfSampleLabel(summary) {
  const validationAvailable = Boolean(summary?.validation_available ?? summary?.holdout_available);
  const auditAvailable = Boolean(summary?.audit_available);
  if (!validationAvailable && !auditAvailable) {
    return "n/a";
  }
  const validationReturn = summary?.validation_total_return ?? summary?.holdout_total_return;
  const validationSharpe = summary?.validation_sharpe ?? summary?.holdout_sharpe;
  const auditReturn = summary?.audit_total_return;
  const auditSharpe = summary?.audit_sharpe;
  const validationLabel = validationAvailable
    ? `V ${formatPercent(validationReturn)} / ${formatNumber(validationSharpe, 2)}`
    : "V n/a";
  const auditLabel = auditAvailable
    ? `A ${formatPercent(auditReturn)} / ${formatNumber(auditSharpe, 2)}`
    : "A n/a";
  return `${validationLabel} | ${auditLabel}`;
}



function eligibleRangeLabel(rollLifecycle) {
  const minimum = rollLifecycle.eligible_market_count_min;
  const maximum = rollLifecycle.eligible_market_count_max;
  const latest = rollLifecycle.eligible_market_count_latest;
  if (minimum === undefined && maximum === undefined && latest === undefined) return "n/a";
  return `${minimum ?? "?"}-${maximum ?? "?"} (latest ${latest ?? "?"})`;
}



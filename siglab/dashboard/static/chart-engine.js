// chart-engine.js — Shared chart rendering engine
// Depends on: constants.js, common.js (formatters, escapeHtml)

window.SigLabUi = window.SigLabUi || {};

// Pull in dependencies from common.js / constants.js
const { formatNumber, formatPercent, escapeHtml, formatDateTime, TRACK_LABELS, METRIC_META, TRACK_COLORS, ACTION_META, buildAxisTicks, sampleSeries, hasFiniteSeriesValues, metricSeries, formatAxisDateTime } = window.SigLabUi;

/* ─── SVG Helpers (moved from common.js) ─── */

function emptyChartText(message) {
  return `<text x="48" y="56" fill="#6b7f70" font-family="Inter, sans-serif">${escapeHtml(message)}</text>`;
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



/* ─── Sparkline (moved from home.js) ─── */

function sparklineSvg(points, metricKey) {
  if (!points.length) {
    return `
      <div class="waiting-card waiting-card-compact">
        <div class="waiting-card-title">No experiments recorded yet</div>
        <p class="waiting-card-copy">Experiments will appear once the first evaluation finishes.</p>
      </div>
    `;
  }
  const values = points
    .map((point) => ({ ...point, metric: pointMetricValue(point, metricKey) }))
    .filter((point) => Number.isFinite(point.metric));
  if (!values.length) {
    return `<svg viewBox="0 0 360 110" role="img" aria-label="Sparkline chart" class="run-sparkline"><text x="14" y="24" fill="#6b7f70" font-family="Inter, sans-serif" font-size="11">No finite values retained</text></svg>`;
  }
  const container = svg.parentElement;
  const width = Math.max(100, container?.clientWidth || 360);
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
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Sparkline chart" class="run-sparkline">
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
      <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="rgba(255,255,255,0.08)" stroke-width="1"></line>
      <polyline fill="none" stroke="#4ade80" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" points="${polyline}"></polyline>
      ${markers}
      <circle cx="${bestX}" cy="${bestY}" r="5.4" fill="none" stroke="#f0b456" stroke-width="1.6"></circle>
    </svg>
  `;
}

function pointMetricValue(point, metricKey) {
  const numeric = Number(point?.[metricKey]);
  return Number.isFinite(numeric) ? numeric : Number.NEGATIVE_INFINITY;
}



/* ─── Dashboard Chart (moved from app.js) ─── */

function renderChart(experiments) {
  const svg = document.getElementById("chart");
  const tooltip = document.getElementById("tooltip");
  tooltip.classList.add("hidden");
  svg.innerHTML = "";

  if (experiments.length === 0) {
    const selectedRun = selectedRunRow();
    const message = selectedRun
      ? "Awaiting first experiment."
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

function moveTooltip(event, tooltipEl) {
  const tooltip = tooltipEl || document.getElementById("tooltip");
  if (!tooltip) return;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const tooltipWidth = 280;
  const tooltipHeight = 300;
  let left = event.clientX + 14;
  let top = event.clientY + 12;
  if (left + tooltipWidth > viewportWidth) { left = viewportWidth - tooltipWidth - 8; }
  if (left < 0) left = 8;
  if (top + tooltipHeight > viewportHeight) { top = viewportHeight - tooltipHeight - 8; }
  if (top < 0) top = 8;
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function groupByTrack(experiments) {
  return experiments.reduce((acc, experiment) => {
    if (!acc[experiment.track]) acc[experiment.track] = [];
    acc[experiment.track].push(experiment);
    return acc;
  }, {});
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

function chartXValue(row) {
  if (state.selectedRunId || state.lockedRunId) {
    return Number(row.run_position || row.run_iteration_number || row.generation || 1);
  }
  return Number(row.generation || 1);
}



/* ─── Experiment Charts (moved from experiment.js) ─── */

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

  const container = svg.parentElement;
  const width = Math.max(400, container?.clientWidth || 1200);
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
      dot.setAttribute("r", "5.5");
      dot.setAttribute("fill", series.color);
      dot.setAttribute("stroke", "rgba(8, 12, 10, 0.6)");
      dot.setAttribute("stroke-width", "1.2");
      dot.setAttribute("tabindex", "0");
      dot.setAttribute("role", "button");
      dot.addEventListener("focus", (event) => {
        tooltip.innerHTML = `
          <div class="meta">${escapeHtml(point.timestamp)}</div>
          <strong>${escapeHtml(series.label)}</strong>
          <div>${escapeHtml(series.formatter(point.value))}</div>
        `;
        tooltip.classList.remove("hidden");
        const rect = dot.getBoundingClientRect();
        const chartRect = svg.getBoundingClientRect();
        moveTooltip({ clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 }, tooltip);
      });
      dot.addEventListener("blur", () => tooltip.classList.add("hidden"));
      dot.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          dot.focus();
        }
      });
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
      dot.addEventListener("touchstart", (event) => {
        event.preventDefault();
        const touch = event.touches[0];
        tooltip.innerHTML = `
          <div class="meta">${escapeHtml(point.timestamp)}</div>
          <strong>${escapeHtml(series.label)}</strong>
          <div>${escapeHtml(series.formatter(point.value))}</div>
        `;
        tooltip.classList.remove("hidden");
        moveTooltip({ clientX: touch.clientX, clientY: touch.clientY }, tooltip);
      }, { passive: false });
      dot.addEventListener("touchmove", (event) => {
        event.preventDefault();
        const touch = event.touches[0];
        moveTooltip({ clientX: touch.clientX, clientY: touch.clientY }, tooltip);
      }, { passive: false });
      dot.addEventListener("touchend", () => tooltip.classList.add("hidden"));
      svg.appendChild(dot);
    });
  });
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
  const width = Math.max(400, container?.clientWidth || 1180);
  const rowHeight = 26;
  const labelWidth = 108;
  const axisHeight = 38;
  const height = Math.max(140, rowHeight * tradableColumns.length + axisHeight + 28);
  const plotWidth = width - labelWidth - 16;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "heatmap-svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Position weight heatmap");
  svg.appendChild(rectNode(0, 0, width, height, "transparent"));

  const maxIndex = Math.max(timeline.index.length - 1, 1);
  const xScale = (position) => labelWidth + (Math.max(0, Math.min(maxIndex, position)) / maxIndex) * plotWidth;

  tradableColumns.forEach((asset, rowIndex) => {
    const y = 24 + rowIndex * rowHeight;
    const label = textNode(6, y + 16, asset, "#a3b5a8", "12");
    label.setAttribute("font-family", "Inter, sans-serif");
    svg.appendChild(label);

    segments.forEach((segment) => {
      const value = Number(segment.weights?.[asset] || 0);
      const x1 = xScale(segment.startPosition);
      const x2 = xScale(segment.endPosition);
      const widthPx = Math.max(2, x2 - x1);
      const rect = rectNode(x1, y, widthPx, 18, weightColor(value));
      rect.setAttribute("rx", "2");
      rect.setAttribute("ry", "2");
      rect.setAttribute("data-asset", asset);
      rect.setAttribute("data-start", segment.startTimestamp);
      rect.setAttribute("data-end", segment.endTimestamp);
      rect.setAttribute("data-weight", formatNumber(value, 3));
      svg.appendChild(rect);
    });
  });

  const axisY = height - 24;
  buildAxisTicks(timeline.index, 6).forEach((tick, tickIndex, ticks) => {
    const x = xScale(tick.position);
    svg.appendChild(lineNode(x, axisY - 10, x, axisY - 4, "rgba(255,255,255,0.10)", 1));
    const anchor =
      tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle";
    const label = textNode(x, axisY + 12, formatAxisDateTime(tick.timestamp), "#6b7f70", "11");
    label.setAttribute("text-anchor", anchor);
    svg.appendChild(label);
  });

  container.innerHTML = "";
  container.appendChild(svg);

  const heatmapTooltip = document.getElementById("pageTooltip") || document.getElementById("tooltip");
  if (heatmapTooltip) {
    container.querySelectorAll("rect[data-asset]").forEach((rect) => {
      rect.addEventListener("mouseenter", (event) => {
        const asset = rect.getAttribute("data-asset");
        const start = rect.getAttribute("data-start");
        const end = rect.getAttribute("data-end");
        const weight = rect.getAttribute("data-weight");
        heatmapTooltip.innerHTML = `<div class="meta">${escapeHtml(start)} → ${escapeHtml(end)}</div><strong>${escapeHtml(asset)}</strong><div>Weight: ${weight}</div>`;
        heatmapTooltip.classList.remove("hidden");
        moveTooltip(event, heatmapTooltip);
      });
      rect.addEventListener("mousemove", (event) => moveTooltip(event, heatmapTooltip));
      rect.addEventListener("mouseleave", () => heatmapTooltip.classList.add("hidden"));
      rect.addEventListener("touchstart", (event) => {
        event.preventDefault();
        const touch = event.touches[0];
        const asset = rect.getAttribute("data-asset");
        const start = rect.getAttribute("data-start");
        const end = rect.getAttribute("data-end");
        const weight = rect.getAttribute("data-weight");
        heatmapTooltip.innerHTML = `<div class="meta">${escapeHtml(start)} → ${escapeHtml(end)}</div><strong>${escapeHtml(asset)}</strong><div>Weight: ${weight}</div>`;
        heatmapTooltip.classList.remove("hidden");
        moveTooltip({ clientX: touch.clientX, clientY: touch.clientY }, heatmapTooltip);
      }, { passive: false });
      rect.addEventListener("touchend", () => heatmapTooltip.classList.add("hidden"));
    });
  }
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
  const cardWidth = Math.max(200, Math.round(container.clientWidth / 2) || 520);
  container.innerHTML = "";
  Object.entries(bySymbol)
    .sort((left, right) => {
      const leftLatest = left[1][left[1].length - 1];
      const rightLatest = right[1][right[1].length - 1];
      return Math.abs(Number(rightLatest?.target_weight || 0)) - Math.abs(Number(leftLatest?.target_weight || 0));
    })
    .forEach(([symbol, trades]) => {
      container.appendChild(renderAssetActionCard(symbol, trades, cardWidth));
    });
}

function renderAssetActionCard(symbol, trades, cardWidth) {
  const latest = trades[trades.length - 1] || {};
  const latestState = positionStateLabel(latest.target_weight);
  const article = document.createElement("article");
  article.className = "asset-action-card";

  const head = document.createElement("div");
  head.className = "asset-action-head";
  head.innerHTML = `
    <div>
      <h3>${escapeHtml(symbol)}</h3>
      <div class="asset-action-meta">
        ${escapeHtml(formatDateTime(trades[0]?.timestamp))} → ${escapeHtml(formatDateTime(trades[trades.length - 1]?.timestamp))}
      </div>
    </div>
    <div class="asset-action-meta">
      ${escapeHtml(String(trades.length))} trades • latest ${escapeHtml(latestState)}
    </div>
  `;
  article.appendChild(head);

  assetActionSvg(symbol, trades, cardWidth, article);

  const legend = document.createElement("div");
  legend.className = "asset-action-legend";
  legend.innerHTML = `
    <span class="legend-marker"><span class="legend-dot"></span>Buy</span>
    <span class="legend-marker"><span class="legend-dot sell"></span>Sell</span>
    <span class="legend-marker"><span class="legend-dot short"></span>Short</span>
    <span class="legend-marker"><span class="legend-dot cover"></span>Cover</span>
  `;
  article.appendChild(legend);

  return article;
}

function assetActionSvg(symbol, trades, widthOverride, container) {
  const width = Math.max(200, widthOverride || 520);
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

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Price chart for ${symbol}`);
  svg.setAttribute("class", "asset-action-svg");

  if (!timestamps.length || !prices.length) {
    svg.appendChild(textNode(16, 24, `No retained prices for ${symbol}`, "#6b7f70", "11"));
    container.appendChild(svg);
    return;
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

  svg.appendChild(rectNode(0, 0, width, height, "transparent"));

  trades.forEach((trade, index) => {
    const x1 = xScale(new Date(trade.timestamp).getTime());
    const next = trades[index + 1];
    const x2 = next ? xScale(new Date(next.timestamp).getTime()) : width - margin.right;
    const tone = positionBandColor(trade.target_weight);
    const rect = rectNode(x1, margin.top, Math.max(2, x2 - x1), plotHeight, tone);
    rect.setAttribute("rx", "2");
    rect.setAttribute("ry", "2");
    svg.appendChild(rect);
  });

  svg.appendChild(lineNode(margin.left, height - margin.bottom, width - margin.right, height - margin.bottom, "rgba(255,255,255,0.08)", 1));
  svg.appendChild(lineNode(margin.left, margin.top, margin.left, height - margin.bottom, "rgba(255,255,255,0.08)", 1));

  const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  polyline.setAttribute("fill", "none");
  polyline.setAttribute("stroke", "#e2ebe5");
  polyline.setAttribute("stroke-width", "2.2");
  polyline.setAttribute("stroke-linecap", "round");
  polyline.setAttribute("stroke-linejoin", "round");
  const linePoints = trades
    .map((trade) => `${xScale(new Date(trade.timestamp).getTime())},${yScale(Number(trade.price))}`)
    .join(" ");
  polyline.setAttribute("points", linePoints);
  svg.appendChild(polyline);

  trades.forEach((trade) => {
    svg.appendChild(tradeMarkerSvg(trade, xScale(new Date(trade.timestamp).getTime()), yScale(Number(trade.price))));
  });

  svg.appendChild(textNode(margin.left, 12, formatPrice(yMax), "#6b7f70", "11"));

  const startLabel = textNode(margin.left, height - margin.bottom + 16, formatAxisDateTime(trades[0]?.timestamp), "#6b7f70", "11");
  svg.appendChild(startLabel);

  const endLabel = textNode(width - margin.right, height - margin.bottom + 16, formatAxisDateTime(trades[trades.length - 1]?.timestamp), "#6b7f70", "11");
  endLabel.setAttribute("text-anchor", "end");
  svg.appendChild(endLabel);

  container.appendChild(svg);
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
  const pageSize = 50;
  const totalPages = Math.ceil(annotatedTrades.length / pageSize);
  if (PAGE_STATE.tradePage > totalPages) PAGE_STATE.tradePage = totalPages;
  if (PAGE_STATE.tradePage < 1) PAGE_STATE.tradePage = 1;
  const currentPage = PAGE_STATE.tradePage;
  const start = Math.max(0, annotatedTrades.length - currentPage * pageSize);
  const end = Math.max(0, annotatedTrades.length - (currentPage - 1) * pageSize);
  const visible = annotatedTrades.slice(start, end).reverse();
  const displayCapital = getDisplayCapitalUsd();
  subtitle.textContent =
    annotatedTrades.length > visible.length
      ? `Showing page ${currentPage} of ${totalPages} (${annotatedTrades.length} trades) from the canonical run. Display notionals and units assume ${formatUsd(displayCapital)} starting capital. Actions reflect the post-trade position state.`
      : `Showing ${annotatedTrades.length} trades from the canonical run. Display notionals and units assume ${formatUsd(displayCapital)} starting capital. Actions reflect the post-trade position state.`;

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

  // Add pagination controls
  if (totalPages > 1) {
    const paginationRow = document.createElement("tr");
    paginationRow.innerHTML = `<td colspan="9" class="pagination-controls">
      <button class="page-btn" data-page="${currentPage - 1}" ${currentPage <= 1 ? "disabled" : ""}>‹ Prev</button>
      <span class="page-info">Page ${currentPage} of ${totalPages} (${annotatedTrades.length} total)</span>
      <button class="page-btn" data-page="${currentPage + 1}" ${currentPage >= totalPages ? "disabled" : ""}>Next ›</button>
    </td>`;
    tbody.appendChild(paginationRow);

    paginationRow.querySelectorAll(".page-btn:not([disabled])").forEach((btn) => {
      btn.addEventListener("click", () => {
        PAGE_STATE.tradePage = parseInt(btn.dataset.page);
        renderTrades(trades);
      });
    });
  }
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
  const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "title");
  titleEl.textContent = title;
  if (trade.action === "short" || trade.action === "flip_short") {
    const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    polygon.setAttribute("points", `${x},${y - 6} ${x - 5.2},${y + 4.6} ${x + 5.2},${y + 4.6}`);
    polygon.setAttribute("fill", meta.color);
    polygon.setAttribute("stroke", "rgba(8,12,10,0.65)");
    polygon.setAttribute("stroke-width", "1.2");
    polygon.appendChild(titleEl);
    return polygon;
  }
  if (trade.action === "cover") {
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x - 4.4);
    rect.setAttribute("y", y - 4.4);
    rect.setAttribute("width", "8.8");
    rect.setAttribute("height", "8.8");
    rect.setAttribute("rx", "1.8");
    rect.setAttribute("ry", "1.8");
    rect.setAttribute("transform", `rotate(45 ${x} ${y})`);
    rect.setAttribute("fill", meta.color);
    rect.setAttribute("stroke", "rgba(8,12,10,0.65)");
    rect.setAttribute("stroke-width", "1.2");
    rect.appendChild(titleEl);
    return rect;
  }
  const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  circle.setAttribute("cx", x);
  circle.setAttribute("cy", y);
  circle.setAttribute("r", trade.action === "sell" ? "4.3" : "4.5");
  circle.setAttribute("fill", meta.color);
  circle.setAttribute("stroke", "rgba(8,12,10,0.65)");
  circle.setAttribute("stroke-width", "1.2");
  circle.appendChild(titleEl);
  return circle;
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

function sumSeriesValues(series) {
  return (series.values || []).reduce((total, value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? total + numeric : total;
  }, 0);
}



/* ─── Export to SigLabUi ─── */
;(function exportChartEngine() {
  // SVG helpers
  window.SigLabUi.emptyChartText = emptyChartText;
  window.SigLabUi.rectNode = rectNode;
  window.SigLabUi.lineNode = lineNode;
  window.SigLabUi.textNode = textNode;
  window.SigLabUi.renderChartLegend = renderChartLegend;
  window.SigLabUi.responsiveSvg = responsiveSvg;

  // Sparkline
  window.SigLabUi.sparklineSvg = sparklineSvg;
  window.SigLabUi.pointMetricValue = pointMetricValue;

  // Dashboard chart
  window.SigLabUi.renderChart = renderChart;
  window.SigLabUi.showTooltip = showTooltip;
  window.SigLabUi.moveTooltip = moveTooltip;
  window.SigLabUi.groupByTrack = groupByTrack;
  window.SigLabUi.bestExperiment = bestExperiment;
  window.SigLabUi.chartXValue = chartXValue;

  // Experiment charts
  window.SigLabUi.drawLineChart = drawLineChart;
  window.SigLabUi.renderHeatmap = renderHeatmap;
  window.SigLabUi.renderAssetActionCharts = renderAssetActionCharts;
  window.SigLabUi.renderAssetActionCard = renderAssetActionCard;
  window.SigLabUi.assetActionSvg = assetActionSvg;
  window.SigLabUi.renderTrades = renderTrades;
  window.SigLabUi.annotateTrades = annotateTrades;
  window.SigLabUi.classifyTradeAction = classifyTradeAction;
  window.SigLabUi.weightSign = weightSign;
  window.SigLabUi.positionStateLabel = positionStateLabel;
  window.SigLabUi.groupTradesBySymbol = groupTradesBySymbol;
  window.SigLabUi.positionBandColor = positionBandColor;
  window.SigLabUi.tradeMarkerSvg = tradeMarkerSvg;
  window.SigLabUi.pillRow = pillRow;
  window.SigLabUi.weightColor = weightColor;
  window.SigLabUi.latestWeightMap = latestWeightMap;
  window.SigLabUi.buildWeightSegments = buildWeightSegments;
  window.SigLabUi.canonicalOutcomeDecomposition = canonicalOutcomeDecomposition;
  window.SigLabUi.sumSeriesValues = sumSeriesValues;
})();

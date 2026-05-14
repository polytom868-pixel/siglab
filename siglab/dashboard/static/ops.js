(() => {
  const { escapeHtml, formatDateTime } = window.SigLabUi;
  const state = {
    refreshTimer: null,
  };

  function valueLabel(value) {
    if (value === true) return "yes";
    if (value === false) return "no";
    if (value === null || value === undefined || value === "") return "unknown";
    return String(value);
  }

  function statusClass(value) {
    const normalized = String(value ?? "").toLowerCase();
    if (["present", "pass", "ready", "yes", "ok", "true"].includes(normalized)) return "ok";
    if (["missing", "malformed", "blocked", "fail", "false", "no"].includes(normalized)) return "bad";
    return "warn";
  }

  function line(key, value, extra = "") {
    const safeValue = valueLabel(value);
    return `<div class="ops-line"><span class="ops-key">${escapeHtml(key)}</span><span class="ops-value ${statusClass(safeValue)}">${escapeHtml(safeValue)}</span>${extra ? `<span class="ops-extra">${escapeHtml(extra)}</span>` : ""}</div>`;
  }

  function listLines(items, emptyText) {
    const list = Array.isArray(items) ? items.filter((item) => String(item ?? "").trim()) : [];
    if (!list.length) return line("none", emptyText);
    return list.map((item, index) => line(`#${index + 1}`, item)).join("");
  }

  function renderSummary(payload) {
    const summary = payload.summary || {};
    const buildathon = summary.buildathon || {};
    const market = summary.market || {};
    const sodex = summary.sodex || {};
    const telemetry = summary.telemetry || {};
    const wave = summary.wave || {};
    const cards = [
      ["Wave", wave.wave_number ? `#${wave.wave_number} ${valueLabel(wave.status)}` : "missing"],
      ["SoSoValue Flow", valueLabel(buildathon.sosovalue_flow)],
      ["SoDEX Public", valueLabel(buildathon.sodex_public_market_data)],
      ["Live Writes", sodex.live_write_allowed ? "allowed" : "refused"],
      ["Provider Metrics", valueLabel(buildathon.provider_metrics_present || telemetry.provider_metrics_status)],
      ["Market Report", valueLabel(buildathon.market_report_status || market.status)],
      ["Decision Stance", valueLabel(market.stance)],
    ];
    document.getElementById("opsSummary").innerHTML = cards
      .map(([label, value]) => `<div class="ops-card"><span>${escapeHtml(label)}</span><strong class="${statusClass(value)}">${escapeHtml(value)}</strong></div>`)
      .join("");
  }

  function render(payload) {
    const summary = payload.summary || {};
    const artifactStatus = payload.artifact_status || {};
    const buildathon = summary.buildathon || {};
    const market = summary.market || {};
    const sodex = summary.sodex || {};
    const telemetry = summary.telemetry || {};
    const wave = summary.wave || {};

    document.getElementById("opsGeneratedAt").textContent = `Generated ${formatDateTime(payload.generated_at)}`;
    renderSummary(payload);

    document.getElementById("artifactHealth").innerHTML = Object.entries(artifactStatus)
      .map(([name, artifact]) => line(name, artifact.status, artifact.error || artifact.path || ""))
      .join("");

    document.getElementById("waveState").innerHTML = [
      line("wave", wave.wave_number ? `#${wave.wave_number}` : "missing"),
      line("phase", wave.phase),
      line("status", wave.status),
      line("goal", wave.goal),
      line("validation", wave.validation_status),
      line("stop allowed", wave.stop_allowed),
      line("next", wave.next_decision),
      listLines(wave.agents, "no agent roles recorded"),
      listLines(wave.outputs, "no outputs recorded"),
    ].join("");

    document.getElementById("buildathonProof").innerHTML = [
      line("SoSoValue input->output", buildathon.sosovalue_flow),
      line("SoDEX public evidence", buildathon.sodex_public_market_data),
      line("provider metrics", buildathon.provider_metrics_present),
      line("market report", buildathon.market_report_status),
      listLines(buildathon.demo_artifacts, "no artifact links"),
    ].join("");

    document.getElementById("marketState").innerHTML = [
      line("status", market.status),
      line("entity", market.entity),
      line("headline", market.headline),
      line("flow direction", market.flow_direction),
      line("bid / ask", [market.quote_bid, market.quote_ask].filter((v) => v !== null && v !== undefined).join(" / ") || "unknown"),
      line("stance", market.stance),
      listLines(market.warnings, "no warnings recorded"),
    ].join("");

    document.getElementById("sodexBoundary").innerHTML = [
      line("public read ready", sodex.public_read_ready),
      line("schema pinned", sodex.schema_pinned),
      line("signed path ready", sodex.signed_path_ready),
      line("live write allowed", sodex.live_write_allowed),
      line("refusal reason", sodex.live_write_refusal_reason),
      line("weight/min", sodex.request_weight_budget_per_minute),
      listLines(sodex.next_actions, "no next actions recorded"),
    ].join("");

    document.getElementById("telemetryState").innerHTML = [
      line("confidence", telemetry.confidence),
      line("traces", telemetry.trace_count),
      line("tool invocations", telemetry.tool_invocation_count),
      line("tool errors", telemetry.tool_error_count),
      line("provider status", telemetry.provider_metrics_status),
      line("provider requests", telemetry.provider_request_count),
      line("estimated credits", telemetry.estimated_credits),
      line("returned tokens", `${valueLabel(telemetry.returned_input_tokens)} in / ${valueLabel(telemetry.returned_output_tokens)} out`),
      line("context pressure", telemetry.context_pressure_events),
      line("credit pressure", telemetry.credit_pressure_events),
    ].join("");

    const blockers = [
      ...(buildathon.red_flags || []),
      ...(market.warnings || []),
      ...(sodex.live_write_refusal_reason ? [sodex.live_write_refusal_reason] : []),
      ...(wave.blockers || []),
      ...(wave.unsafe_claims || []),
      ...Object.entries(artifactStatus)
        .filter(([, artifact]) => artifact.status !== "present")
        .map(([name, artifact]) => `${name}: ${artifact.error || artifact.status}`),
    ];
    document.getElementById("blockers").innerHTML = listLines(blockers, "no blockers in current artifacts");
  }

  async function loadOps() {
    const response = await fetch("/api/ops", { cache: "no-store" });
    if (!response.ok) throw new Error(`Ops API failed: ${response.status}`);
    render(await response.json());
  }

  function schedule() {
    window.clearInterval(state.refreshTimer);
    if (document.getElementById("autoRefresh").checked) {
      state.refreshTimer = window.setInterval(() => {
        loadOps().catch((error) => {
          document.getElementById("opsGeneratedAt").textContent = `Refresh failed: ${error.message}`;
        });
      }, 15000);
    }
  }

  document.getElementById("refreshButton").addEventListener("click", () => {
    loadOps().catch((error) => {
      document.getElementById("opsGeneratedAt").textContent = `Refresh failed: ${error.message}`;
    });
  });
  document.getElementById("autoRefresh").addEventListener("change", schedule);

  loadOps().catch((error) => {
    document.getElementById("opsGeneratedAt").textContent = `Load failed: ${error.message}`;
  });
  schedule();
})();

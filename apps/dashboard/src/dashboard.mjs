export const REDACTED = "[REDACTED]";

const SAFETY_EVENT_STATUSES = new Set(["blocked", "rewritten", "escalated"]);
const SENSITIVE_KEY_PATTERN =
  /(address|api[_-]?key|authorization|birth|email|free[_-]?text|input[_-]?payload|note|password|phone|prompt[_-]?payload|raw|sample|secret|sexual|ssn|token)/i;
const PRIVATE_VALUE_PATTERN =
  /(@|diagnosed|doctor|free[-_\s]?text|healthkit|medication|patient|phone|prompt|raw sample|secret|sexual|source[_-\s]?sample)/i;

export function isOperatorAuthorized(auth = {}) {
  return auth.operator === true && String(auth.scope || "read_only").includes("read");
}

export function sanitizeDashboardData(data = {}) {
  const pipeline = sanitizePipeline(data.pipeline);
  const dataCompleteness = array(data.dataCompleteness).map(sanitizeCompleteness);
  const recommendationTraces = array(data.recommendationTraces).map(sanitizeTrace);
  const llmRuns = array(data.llmRuns).map(sanitizeModelRun);
  const evalResults = array(data.evalResults).map(sanitizeEvalResult);
  const operationalAlerts = sanitizeOperationalAlerts(data.operationalAlerts, recommendationTraces);
  return {
    schemaVersion: safeText(data.schemaVersion, "schemaVersion"),
    mode: safeText(data.mode || "demo", "mode"),
    generatedAt: safeDate(data.generatedAt),
    pipeline,
    dataCompleteness,
    recommendationTraces,
    llmRuns,
    evalResults,
    operationalAlerts,
    safetyEvents: sanitizeSafetyEvents(
      data,
      recommendationTraces,
      llmRuns,
      evalResults,
      operationalAlerts,
    ),
    demoScenarios: array(data.demoScenarios).map(sanitizeDemoScenario),
  };
}

export function renderDashboard(data, options = {}) {
  const mode = options.mode || data?.mode || "demo";
  const authenticated = mode === "demo" || options.authenticated === true;
  if (!authenticated) {
    return renderLockedShell();
  }

  const safe = sanitizeDashboardData(data);
  const firstTrace = safe.recommendationTraces[0]?.traceId || "";
  return `
    <div class="shell">
      <header class="masthead">
        <div>
          <p class="eyebrow">Baseline internal</p>
          <h1>Evaluation and operations dashboard</h1>
        </div>
        <div class="mode-pill">${mode === "demo" ? "Demo mode" : "Operator mode"}</div>
      </header>
      <section class="summary-grid" aria-label="Operations summary">
        ${summaryTile("Sync success", percent(safe.pipeline.sync.successRate), safe.pipeline.sync.status)}
        ${summaryTile("P95 sync latency", ms(safe.pipeline.sync.latencyMsP95), "sync")}
        ${summaryTile("LLM status", safe.pipeline.llmGeneration.status, "generation")}
        ${summaryTile("Eval pass rate", percent(evalPassRate(safe.evalResults)), "latest suite")}
      </section>
      ${renderPipelineHealth(safe.pipeline)}
      ${renderDataCompleteness(safe.dataCompleteness)}
      ${renderTraceBrowser(safe.recommendationTraces, firstTrace)}
      ${renderModelRuns(safe.llmRuns)}
      ${renderEvalResults(safe.evalResults)}
      ${renderSafetyEvents(safe.safetyEvents)}
      ${renderDemoScenarios(safe.demoScenarios)}
      <footer class="footer">Generated ${escapeHtml(formatDateTime(safe.generatedAt))}</footer>
    </div>`;
}

function renderLockedShell() {
  return `
    <div class="shell shell-locked" data-section="auth-gate">
      <section class="locked-panel">
        <p class="eyebrow">Baseline internal</p>
        <h1>Operator authentication required</h1>
        <p>Read-only operational views require an authenticated operator context.</p>
      </section>
    </div>`;
}

function renderPipelineHealth(pipeline) {
  const jobs = pipeline.featureJobs
    .map(
      (job) => `
        <li>
          <span>${escapeHtml(job.date)}</span>
          <strong>${escapeHtml(job.status)}</strong>
          <span>${escapeHtml(ms(job.latencyMs))}</span>
          <span>${escapeHtml(job.retryStatus)}</span>
        </li>`,
    )
    .join("");
  const failures = pipeline.failedJobs.length
    ? pipeline.failedJobs
        .map(
          (job) => `
            <tr>
              <td>${escapeHtml(job.type)}</td>
              <td>${escapeHtml(job.status)}</td>
              <td>${escapeHtml(job.retryStatus)}</td>
              <td>${escapeHtml(job.lastError)}</td>
              <td><code>${escapeHtml(shortId(job.traceId))}</code></td>
            </tr>`,
        )
        .join("")
    : `<tr><td colspan="5">No recent failures</td></tr>`;

  return `
    <section class="section" data-section="pipeline-health">
      <div class="section-heading">
        <p class="eyebrow">FR-091 / FR-092</p>
        <h2>Pipeline health</h2>
      </div>
      <div class="split">
        <div class="panel">
          <dl class="metric-list">
            ${metric("Sync status", pipeline.sync.status)}
            ${metric("Sync P50", ms(pipeline.sync.latencyMsP50))}
            ${metric("Sync P95", ms(pipeline.sync.latencyMsP95))}
            ${metric("Last sync", formatDateTime(pipeline.sync.lastCompletedAt))}
            ${metric("LLM generation", pipeline.llmGeneration.status)}
            ${metric("Generated today", number(pipeline.llmGeneration.completedToday))}
            ${metric("Failed today", number(pipeline.llmGeneration.failedToday))}
            ${metric("Generation P95", ms(pipeline.llmGeneration.latencyMsP95))}
          </dl>
        </div>
        <div class="panel">
          <h3>Feature jobs</h3>
          <ul class="job-list">${jobs}</ul>
        </div>
      </div>
      <div class="panel table-panel">
        <h3>Recent failed jobs</h3>
        <table>
          <thead>
            <tr><th>Type</th><th>Status</th><th>Retry</th><th>Last error</th><th>Trace</th></tr>
          </thead>
          <tbody>${failures}</tbody>
        </table>
      </div>
    </section>`;
}

function renderDataCompleteness(rows) {
  const content = rows
    .map(
      (row) => `
        <article class="day-row">
          <div>
            <strong>${escapeHtml(row.date)}</strong>
            <span>${escapeHtml(row.missingTypes.length ? `Missing ${row.missingTypes.join(", ")}` : "Complete")}</span>
          </div>
          <div class="bar" aria-label="${escapeHtml(percent(row.completenessRatio))}">
            <span style="width: ${barWidth(row.completenessRatio)}%"></span>
          </div>
          <small>${escapeHtml(row.staleTypes.length ? `Stale ${row.staleTypes.join(", ")}` : "Fresh")}</small>
        </article>`,
    )
    .join("");
  return `
    <section class="section" data-section="data-completeness">
      <div class="section-heading">
        <p class="eyebrow">Daily read model</p>
        <h2>Data completeness</h2>
      </div>
      <div class="panel">${content || emptyState("No completeness rows")}</div>
    </section>`;
}

function renderTraceBrowser(traces, selectedTraceId) {
  const tabs = traces
    .map(
      (trace) => `
        <button class="trace-tab" type="button" data-trace-id="${escapeHtml(trace.traceId)}" aria-pressed="${
          trace.traceId === selectedTraceId ? "true" : "false"
        }">
          <span>${escapeHtml(trace.date)}</span>
          <strong>${escapeHtml(trace.recommendationBand)}</strong>
          <code>${escapeHtml(shortId(trace.traceId))}</code>
        </button>`,
    )
    .join("");
  const panels = traces
    .map(
      (trace) => `
        <article class="trace-detail${trace.traceId === selectedTraceId ? "" : " is-hidden"}" data-trace-panel="${
          trace.traceId
        }">
          <div class="trace-head">
            <div>
              <h3>${escapeHtml(trace.readinessState)} readiness</h3>
              <p>${escapeHtml(trace.dataFreshness)}</p>
            </div>
            <code>${escapeHtml(trace.traceId)}</code>
          </div>
          <dl class="metric-list compact">
            ${metric("Band", trace.recommendationBand)}
            ${metric("Confidence", trace.confidence)}
            ${metric("Model status", trace.modelMetadata.briefing_generation_status || "unknown")}
          </dl>
          <div class="trace-columns">
            ${renderList("Feature values", trace.featureValues.map((item) => `${item.label}: ${item.value}`))}
            ${renderList("Rules fired", trace.rulesFired)}
            ${renderList("Retrieved evidence", trace.retrievedMemory)}
            ${renderList("External sources", trace.externalSources)}
          </div>
        </article>`,
    )
    .join("");

  return `
    <section class="section" data-section="recommendation-traces">
      <div class="section-heading">
        <p class="eyebrow">FR-093</p>
        <h2>Recommendation traces</h2>
      </div>
      <div class="trace-browser">
        <nav class="trace-tabs" aria-label="Recommendation trace list">${tabs || emptyState("No traces")}</nav>
        <div class="panel">${panels || emptyState("No trace selected")}</div>
      </div>
    </section>`;
}

function renderModelRuns(runs) {
  const runRows = runs
    .map(
      (run) => `
        <tr>
          <td><code>${escapeHtml(shortId(run.id))}</code></td>
          <td>${escapeHtml(run.modelName)}</td>
          <td>${escapeHtml(run.promptVersion)}</td>
          <td>${escapeHtml(number(run.totalTokens))}</td>
          <td>${escapeHtml(money(run.cost))}</td>
          <td>${escapeHtml(ms(run.latencyMs))}</td>
          <td><span class="status">${escapeHtml(run.safetyStatus)}</span></td>
        </tr>`,
    )
    .join("");
  return `
    <section class="section" data-section="llm-runs">
      <div class="section-heading">
        <p class="eyebrow">ModelRun</p>
        <h2>LLM runs</h2>
      </div>
      <div class="split">
        <div class="panel table-panel">
          <table>
            <thead>
              <tr><th>Run</th><th>Model</th><th>Prompt</th><th>Tokens</th><th>Cost</th><th>Latency</th><th>Safety</th></tr>
            </thead>
            <tbody>${runRows || `<tr><td colspan="7">No model runs</td></tr>`}</tbody>
          </table>
        </div>
        <div class="panel">
          <h3>Cost and latency</h3>
          ${renderRunBars(runs)}
        </div>
      </div>
    </section>`;
}

function renderEvalResults(results) {
  const byType = aggregateEvalTypes(results)
    .map(
      (entry) => `
        <article class="eval-chip">
          <strong>${escapeHtml(entry.type)}</strong>
          <span>${entry.passed}/${entry.decided} decided passing</span>
          <small>${entry.pending} pending / ${entry.failed} failing</small>
        </article>`,
    )
    .join("");
  const rows = results
    .map(
      (result) => `
        <tr>
          <td>${escapeHtml(result.evalType)}</td>
          <td>${escapeHtml(result.suiteName)}</td>
          <td>${escapeHtml(result.scenarioName)}</td>
          <td><span class="eval-state eval-state-${escapeHtml(evalStatusLabel(result))}">${escapeHtml(
            evalStatusLabel(result),
          )}</span></td>
          <td>${escapeHtml(formatDateTime(result.evaluatedAt))}</td>
          <td>${escapeHtml(result.failureReason || "-")}</td>
        </tr>`,
    )
    .join("");
  return `
    <section class="section" data-section="eval-results">
      <div class="section-heading">
        <p class="eyebrow">P0-07 harness</p>
        <h2>Eval results</h2>
      </div>
      <div class="eval-grid">${byType || emptyState("No eval results")}</div>
      ${renderEvalTrend(results)}
      <div class="panel table-panel">
        <table>
          <thead>
            <tr><th>Type</th><th>Suite</th><th>Scenario</th><th>Result</th><th>Evaluated</th><th>Reason</th></tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="6">No eval rows</td></tr>`}</tbody>
        </table>
      </div>
    </section>`;
}

function renderSafetyEvents(events) {
  const rows = events
    .map(
      (event) => `
        <article class="safety-row">
          <div>
            <strong>${escapeHtml(event.category)}</strong>
            <span>${escapeHtml(event.summary)}</span>
          </div>
          <span>${escapeHtml(event.severity)}</span>
          <span>${escapeHtml(event.status)}</span>
          <code>${escapeHtml(shortId(event.traceId))}</code>
          <code>${escapeHtml(shortId(event.modelRunId) || "-")}</code>
        </article>`,
    )
    .join("");
  return `
    <section class="section" data-section="safety-events">
      <div class="section-heading">
        <p class="eyebrow">FR-094</p>
        <h2>Safety events</h2>
      </div>
      <div class="panel">${rows || emptyState("No safety events")}</div>
    </section>`;
}

function renderEvalTrend(results) {
  const buckets = [...new Set(results.map((result) => evalBucket(result.evaluatedAt)))]
    .filter(Boolean)
    .sort();
  if (!buckets.length) {
    return `<div class="panel">${emptyState("No eval trend")}</div>`;
  }

  const grouped = new Map();
  for (const result of results) {
    const key = `${result.evalType}::${result.suiteName}`;
    const entry = grouped.get(key) || {
      evalType: result.evalType,
      suiteName: result.suiteName,
      states: new Map(),
    };
    entry.states.set(evalBucket(result.evaluatedAt), evalStatusLabel(result));
    grouped.set(key, entry);
  }

  const header = buckets.map((bucket) => `<th>${escapeHtml(formatDateTime(bucket))}</th>`).join("");
  const rows = [...grouped.values()]
    .sort((left, right) =>
      `${left.evalType}:${left.suiteName}`.localeCompare(`${right.evalType}:${right.suiteName}`),
    )
    .map(
      (entry) => `
        <tr>
          <td>${escapeHtml(entry.suiteName)}</td>
          <td>${escapeHtml(entry.evalType)}</td>
          ${buckets
            .map((bucket) => {
              const state = entry.states.get(bucket) || "none";
              return `<td><span class="eval-state eval-state-${escapeHtml(state)}">${escapeHtml(
                state === "none" ? "-" : state,
              )}</span></td>`;
            })
            .join("")}
        </tr>`,
    )
    .join("");

  return `
    <div class="panel table-panel eval-trend">
      <h3>Pass/fail trend</h3>
      <table>
        <thead><tr><th>Suite</th><th>Type</th>${header}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderDemoScenarios(scenarios) {
  const cards = scenarios
    .map(
      (scenario) => `
        <article class="demo-card">
          <span>${escapeHtml(scenario.status)}</span>
          <h3>${escapeHtml(scenario.name)}</h3>
          <p>${escapeHtml(scenario.description)}</p>
        </article>`,
    )
    .join("");
  return `
    <section class="section" data-section="demo-scenarios">
      <div class="section-heading">
        <p class="eyebrow">FR-095</p>
        <h2>Demo scenarios</h2>
      </div>
      <div class="demo-grid">${cards || emptyState("No demo scenarios")}</div>
    </section>`;
}

function renderRunBars(runs) {
  const maxCost = Math.max(0.001, ...runs.map((run) => run.cost || 0));
  const maxLatency = Math.max(1, ...runs.map((run) => run.latencyMs || 0));
  return runs
    .map(
      (run) => `
        <div class="run-bar">
          <div>
            <strong>${escapeHtml(shortId(run.id))}</strong>
            <span>${escapeHtml(money(run.cost))} / ${escapeHtml(ms(run.latencyMs))}</span>
          </div>
          <div class="bar duo" aria-label="cost ${escapeHtml(money(run.cost))}">
            <span style="width: ${barWidth(run.cost / maxCost)}%"></span>
          </div>
          <div class="bar latency" aria-label="latency ${escapeHtml(ms(run.latencyMs))}">
            <span style="width: ${barWidth(run.latencyMs / maxLatency)}%"></span>
          </div>
        </div>`,
    )
    .join("");
}

function renderList(title, items) {
  const list = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `
    <div>
      <h4>${escapeHtml(title)}</h4>
      <ul>${list || "<li>None</li>"}</ul>
    </div>`;
}

function summaryTile(label, value, detail) {
  return `
    <article class="summary-tile">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </article>`;
}

function metric(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
}

function emptyState(message) {
  return `<p class="empty">${escapeHtml(message)}</p>`;
}

function sanitizePipeline(pipeline = {}) {
  const sync = pipeline.sync || {};
  const llmGeneration = pipeline.llmGeneration || {};
  return {
    sync: {
      status: safeText(sync.status, "status"),
      successRate: safeNumber(sync.successRate),
      latencyMsP50: safeNumber(sync.latencyMsP50),
      latencyMsP95: safeNumber(sync.latencyMsP95),
      lastCompletedAt: safeDate(sync.lastCompletedAt),
    },
    featureJobs: array(pipeline.featureJobs).map((job) => ({
      jobId: safeText(job.jobId, "jobId"),
      date: safeDate(job.date),
      status: safeText(job.status, "status"),
      latencyMs: safeNumber(job.latencyMs),
      retryStatus: safeText(job.retryStatus, "retryStatus"),
    })),
    llmGeneration: {
      status: safeText(llmGeneration.status, "status"),
      completedToday: safeNumber(llmGeneration.completedToday),
      failedToday: safeNumber(llmGeneration.failedToday),
      latencyMsP95: safeNumber(llmGeneration.latencyMsP95),
      totalCostUsd: safeNumber(llmGeneration.totalCostUsd),
    },
    failedJobs: array(pipeline.failedJobs).map((job) => ({
      jobId: safeText(job.jobId, "jobId"),
      type: safeText(job.type, "type"),
      status: safeText(job.status, "status"),
      retryStatus: safeText(job.retryStatus, "retryStatus"),
      lastError: safeText(job.lastError, "lastError", 120),
      traceId: safeText(job.traceId, "traceId"),
    })),
  };
}

function sanitizeCompleteness(row = {}) {
  return {
    date: safeDate(row.date),
    completenessRatio: safeNumber(row.completenessRatio),
    presentTypes: array(row.presentTypes).map((item) => safeText(item, "metricType")),
    missingTypes: array(row.missingTypes).map((item) => safeText(item, "metricType")),
    staleTypes: array(row.staleTypes).map((item) => safeText(item, "metricType")),
  };
}

function sanitizeTrace(trace = {}) {
  const dataFreshness = trace.dataFreshness || trace.data_freshness;
  return {
    traceId: safeText(trace.traceId || trace.trace_id, "traceId"),
    date: safeDate(trace.date || trace.generated_at || trace.latest_checkin_date || dataFreshness?.latest_checkin_date),
    readinessState: safeText(trace.readinessState || trace.readiness_state || "trace", "readinessState"),
    recommendationBand: safeText(
      trace.recommendationBand || trace.recommendation_band || "recommendation",
      "recommendationBand",
    ),
    confidence: safeText(trace.confidence || "unknown", "confidence"),
    dataFreshness: summarizeDataFreshness(dataFreshness),
    featureValues: array(trace.featureValues || trace.feature_values).map(sanitizeFeatureValue),
    rulesFired: array(trace.rulesFired || trace.rules_fired).map((item) => safeText(item, "rule")),
    retrievedMemory: array(trace.retrievedMemory || trace.retrieved_memory).map(summarizeMemoryObservation),
    externalSources: array(trace.externalSources || trace.external_sources).map(summarizeExternalSource),
    modelMetadata: sanitizeMetadata(trace.modelMetadata || trace.model_metadata),
  };
}

function sanitizeModelRun(run = {}) {
  const tokenUsage = run.tokenUsage || run.token_usage || {};
  const totalTokens =
    safeNumber(tokenUsage.total) ||
    safeNumber(tokenUsage.total_tokens) ||
    safeNumber(tokenUsage.input) + safeNumber(tokenUsage.output);
  const safetyResult = run.safetyResult || run.safety_result || {};
  const inputMetadata = run.inputMetadata || run.input_metadata || {};
  return {
    id: safeText(run.id, "id"),
    createdAt: safeDate(run.createdAt || run.created_at),
    runType: safeText(run.runType || run.run_type, "runType"),
    modelProvider: safeText(run.modelProvider || run.model_provider, "modelProvider"),
    modelName: safeText(run.modelName || run.model_name, "modelName"),
    promptVersion: safeText(run.promptVersion || run.prompt_version, "promptVersion"),
    schemaVersion: safeText(run.schemaVersion || run.schema_version, "schemaVersion"),
    totalTokens,
    cost: safeNumber(run.cost),
    latencyMs: safeNumber(run.latencyMs || run.latency_ms),
    safetyStatus: safeText(safetyResult.status || run.safetyStatus || "unknown", "status"),
    safetyCategories: array(
      safetyResult.categories || safetyResult.triggered_categories || safetyResult.triggered_rules,
    ).map((item) => safeText(item, "category")),
    traceId: safeText(
      run.traceId ||
        run.trace_id ||
        run.reasoningTraceId ||
        run.reasoning_trace_id ||
        safetyResult.trace_id ||
        safetyResult.reasoning_trace_id ||
        inputMetadata.trace_id ||
        inputMetadata.reasoning_trace_id,
      "traceId",
    ),
  };
}

function sanitizeEvalResult(result = {}) {
  const actual = result.actualOutput || result.actual_output || {};
  const observed = actual.observed || result.observed || {};
  const finalClassification = observed.final_classification || observed.finalClassification || {};
  return {
    suiteName: safeText(result.suiteName || result.suite_name || actual.suite_name || result.name, "suiteName"),
    evalType: safeText(result.evalType || result.eval_type || actual.eval_type, "evalType"),
    scenarioName: safeText(result.scenarioName || result.scenario_name || actual.scenario_name, "scenarioName"),
    passFail: parsePassFail(result),
    evaluatedAt: safeDate(result.evaluatedAt || result.evaluated_at),
    traceId: safeText(result.traceId || result.trace_id || actual.trace_id || actual.reasoning_trace_id, "traceId"),
    modelRunId: safeText(result.modelRunId || result.model_run_id || actual.model_run_id, "modelRunId"),
    failureReason: result.failureReason || result.failure_reason
      ? safeText(result.failureReason || result.failure_reason, "failureReason", 120)
      : "",
    safetyStatus: safeText(
      observed.status ||
        observed.safety_status ||
        observed.safetyStatus ||
        finalClassification.status ||
        actual.status ||
        actual.safety_status ||
        actual.safetyStatus,
      "status",
    ),
    safetyCategories: safetyCategoriesFromEval(result, actual, observed, finalClassification),
    safetyAction: safeText(observed.action || actual.action, "action"),
    safetyReason: safeText(observed.reason || actual.reason, "reason"),
    unsupportedMedicalOutput:
      observed.unsupported_medical_output === true ||
      observed.unsupportedMedicalOutput === true ||
      actual.unsupported_medical_output === true ||
      actual.unsupportedMedicalOutput === true,
  };
}

function sanitizeOperationalAlerts(alerts, recommendationTraces) {
  const latestTraceId = recommendationTraces[0]?.traceId || "";
  return array(alerts).map((alert) => ({
    eventId: safeText(alert.alert_type || alert.alertType, "eventId"),
    source: "operational_alert",
    severity: safeText(alert.severity, "severity") || "warning",
    status: "open",
    category: safeText(alert.alert_type || alert.alertType, "category"),
    detectedAt: safeDate(alert.detected_at || alert.detectedAt || alert.metadata?.date),
    traceId: latestTraceId,
    modelRunId: "",
    summary: safeText(alert.message, "summary", 140),
  }));
}

function sanitizeSafetyEvents(data, recommendationTraces, llmRuns, evalResults, operationalAlerts) {
  const traceByModelRun = traceIdsByModelRun(recommendationTraces);
  const explicit = array(data.safetyEvents).map((event) => ({
    ...sanitizeExplicitSafetyEvent(event, traceByModelRun),
  }));
  const evalViolations = evalResults.map(safetyEventFromEvalResult).filter(Boolean);
  const modelViolations = llmRuns
    .filter((run) => !["passed", "unknown"].includes(run.safetyStatus))
    .map((run) => ({
      eventId: run.id,
      source: "model_run",
      severity: "high",
      status: run.safetyStatus,
      category: run.safetyCategories.join(", ") || "safety_result",
      detectedAt: run.createdAt,
      traceId: run.traceId || traceByModelRun.get(run.id) || "",
      modelRunId: run.id,
      summary: `Model safety result ${run.safetyStatus}`,
    }));
  return [...explicit, ...evalViolations, ...modelViolations, ...operationalAlerts];
}

function safetyEventFromEvalResult(result) {
  if (result.evalType !== "safety") {
    return null;
  }
  const caughtStatus = SAFETY_EVENT_STATUSES.has(result.safetyStatus);
  const failedSafetyEval = result.passFail === false || result.unsupportedMedicalOutput;
  if (!caughtStatus && !failedSafetyEval) {
    return null;
  }
  const status = caughtStatus ? result.safetyStatus : "failed";
  const category = result.safetyCategories[0] || result.scenarioName || "safety_policy";
  return {
    eventId: result.suiteName,
    source: "eval",
    severity: caughtStatus ? "high" : "critical",
    status,
    category,
    detectedAt: result.evaluatedAt,
    traceId: result.traceId,
    modelRunId: result.modelRunId,
    summary: safetyEvalSummary(result, status, category),
  };
}

function safetyEvalSummary(result, status, category) {
  if (!SAFETY_EVENT_STATUSES.has(status)) {
    return result.failureReason || "Safety evaluation failure";
  }
  const detail = result.safetyReason || result.safetyAction;
  const suffix = detail ? ` (${detail})` : "";
  const outcome = result.passFail === true ? "as expected" : "during eval";
  return safeText(`Safety policy ${status} ${category} ${outcome}${suffix}`, "summary", 140);
}

function sanitizeExplicitSafetyEvent(event, traceByModelRun) {
  const rawTraceId = safeText(
    event.traceId || event.trace_id || event.reasoningTraceId || event.reasoning_trace_id,
    "traceId",
  );
  const rawModelRunId = safeText(event.modelRunId || event.model_run_id, "modelRunId");
  const modelRunId = rawModelRunId || (traceByModelRun.has(rawTraceId) ? rawTraceId : "");
  return {
    eventId: safeText(event.eventId || event.id, "eventId"),
    source: safeText(event.source, "source"),
    severity: safeText(event.severity, "severity"),
    status: safeText(event.status, "status"),
    category: safeText(event.category, "category"),
    detectedAt: safeDate(event.detectedAt || event.detected_at),
    traceId: traceByModelRun.get(rawTraceId) || rawTraceId,
    modelRunId,
    summary: safeText(event.summary, "summary", 140),
  };
}

function sanitizeDemoScenario(scenario = {}) {
  return {
    name: safeText(scenario.name, "scenarioName"),
    status: safeText(scenario.status, "status"),
    description: safeText(scenario.description, "description", 140),
  };
}

function sanitizeMetadata(metadata = {}) {
  const safe = {};
  for (const key of [
    "briefing_generation_status",
    "model_run_ids",
    "model_run_id",
    "provider",
    "model",
    "trace_id",
    "reasoning_trace_id",
    "recommendation_trace_id",
  ]) {
    if (metadata[key] !== undefined) {
      safe[key] = safeText(String(metadata[key]), key, 160);
    }
  }
  return safe;
}

function aggregateEvalTypes(results) {
  const counts = new Map();
  for (const result of results) {
    const entry = counts.get(result.evalType) || {
      type: result.evalType,
      passed: 0,
      failed: 0,
      pending: 0,
      decided: 0,
      total: 0,
    };
    entry.total += 1;
    if (result.passFail === true) {
      entry.passed += 1;
      entry.decided += 1;
    } else if (result.passFail === false) {
      entry.failed += 1;
      entry.decided += 1;
    } else {
      entry.pending += 1;
    }
    counts.set(result.evalType, entry);
  }
  return [...counts.values()].sort((left, right) => left.type.localeCompare(right.type));
}

function evalPassRate(results) {
  const decided = results.filter((result) => result.passFail !== null);
  if (!decided.length) {
    return 0;
  }
  return decided.filter((result) => result.passFail).length / decided.length;
}

function evalStatusLabel(result) {
  if (result.passFail === true) {
    return "pass";
  }
  if (result.passFail === false) {
    return "fail";
  }
  return "pending";
}

function parsePassFail(result) {
  for (const value of [result.passed, result.passFail, result.pass_fail]) {
    if (value === true || value === false) {
      return value;
    }
  }
  return null;
}

function safetyCategoriesFromEval(result, actual, observed, finalClassification) {
  const expected = result.expectedProperties || result.expected_properties || {};
  const categories = [
    ...array(observed.triggered_categories || observed.triggeredCategories),
    ...array(finalClassification.triggered_categories || finalClassification.triggeredCategories),
    ...array(observed.output_categories || observed.outputCategories),
    ...array(observed.request_categories || observed.requestCategories),
    ...array(actual.triggered_categories || actual.triggeredCategories),
  ]
    .map((item) => safeText(item, "category"))
    .filter(Boolean);
  const unique = [...new Set(categories)];
  if (unique.length) {
    return unique;
  }
  const expectedCategory = safeText(expected.expected_category || expected.expectedCategory, "category");
  return expectedCategory ? [expectedCategory] : [];
}

function evalBucket(value) {
  return safeDate(value).slice(0, 19);
}

function summarizeDataFreshness(value) {
  if (typeof value === "string") {
    return safeText(value, "dataFreshness", 120);
  }
  if (!value || typeof value !== "object") {
    return "";
  }
  const parts = [];
  if (value.latest_checkin_date || value.latestCheckinDate) {
    parts.push(`check-in ${value.latest_checkin_date || value.latestCheckinDate}`);
  }
  if (value.latest_sample_at || value.latestSampleAt) {
    parts.push(`latest sample ${formatDateTime(value.latest_sample_at || value.latestSampleAt)}`);
  }
  const staleSources = array(value.stale_sources || value.staleSources).map((item) =>
    safeText(item, "source"),
  );
  if (staleSources.length) {
    parts.push(`stale ${staleSources.join(", ")}`);
  }
  return safeText(parts.join("; "), "dataFreshness", 120);
}

function sanitizeFeatureValue(item = {}) {
  const interpretation = safeText(item.interpretation, "interpretation", 80);
  const value = safeText(String(item.value ?? interpretation), "featureValue", 80);
  return {
    label: safeText(item.label || item.metric || item.name || "feature", "featureLabel"),
    value: interpretation && interpretation !== value ? `${value} - ${interpretation}` : value,
    unit: safeText(item.unit || item.source, "unit"),
    status: safeText(item.status || item.interpretation, "status"),
  };
}

function summarizeMemoryObservation(item) {
  if (typeof item === "string") {
    return safeText(item, "memory", 140);
  }
  return safeText([item.observation, item.relevance, item.period].filter(Boolean).join(" - "), "memory", 140);
}

function summarizeExternalSource(item) {
  if (typeof item === "string") {
    return safeText(item, "source", 120);
  }
  return safeText([item.title, item.source, item.cited_claim].filter(Boolean).join(" - "), "source", 120);
}

function traceIdsByModelRun(traces) {
  const ids = new Map();
  for (const trace of traces) {
    for (const modelRunId of splitIds(
      `${trace.modelMetadata.model_run_ids || ""} ${trace.modelMetadata.model_run_id || ""}`,
    )) {
      ids.set(modelRunId, trace.traceId);
    }
  }
  return ids;
}

function splitIds(value) {
  return String(value)
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function safeText(value, key, maxLength = 96) {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value).trim();
  if (!text) {
    return "";
  }
  if (SENSITIVE_KEY_PATTERN.test(key) || PRIVATE_VALUE_PATTERN.test(text) || text.length > maxLength) {
    return REDACTED;
  }
  return text;
}

function safeNumber(value) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : 0;
}

function safeDate(value) {
  return safeText(value, "date", 40);
}

function array(value) {
  return Array.isArray(value) ? value : [];
}

function percent(value) {
  return `${Math.round(safeNumber(value) * 100)}%`;
}

function ms(value) {
  return `${Math.round(safeNumber(value))} ms`;
}

function money(value) {
  return `$${safeNumber(value).toFixed(3)}`;
}

function number(value) {
  return String(Math.round(safeNumber(value)));
}

function barWidth(value) {
  return Math.max(4, Math.min(100, Math.round(safeNumber(value) * 100)));
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 12 ? `${text.slice(0, 8)}...` : text;
}

function formatDateTime(value) {
  if (!value) {
    return "unknown";
  }
  return value.replace("T", " ").replace("Z", " UTC");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

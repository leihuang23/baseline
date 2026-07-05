import assert from "node:assert/strict";
import test from "node:test";

import { demoDashboardData } from "../src/demo-data.mjs";
import { REDACTED, renderDashboard, sanitizeDashboardData } from "../src/dashboard.mjs";

const REQUIRED_SECTIONS = [
  "pipeline-health",
  "data-completeness",
  "recommendation-traces",
  "llm-runs",
  "eval-results",
  "safety-events",
  "demo-scenarios",
];

test("renders every dashboard section from synthetic data", () => {
  const html = renderDashboard(demoDashboardData, { mode: "demo" });

  for (const section of REQUIRED_SECTIONS) {
    assert.match(html, new RegExp(`data-section="${section}"`));
  }
  assert.match(html, /Pipeline health/);
  assert.match(html, /Data completeness/);
  assert.match(html, /Recommendation traces/);
  assert.match(html, /LLM runs/);
  assert.match(html, /Eval results/);
  assert.match(html, /Safety events/);
  assert.match(html, /Demo scenarios/);
});

test("recommendation traces are browsable and tied to trace ids", () => {
  const html = renderDashboard(demoDashboardData, { mode: "demo" });

  assert.match(html, /data-trace-id="2d31f8f5-0c96-4b57-a59a-c3e7a3a82501"/);
  assert.match(html, /data-trace-panel="2d31f8f5-0c96-4b57-a59a-c3e7a3a82501"/);
  assert.match(html, /load_increase_guardrail/);
  assert.match(html, /recovery_signal_crosscheck/);
  assert.match(html, /baseline-explainer-demo/);
});

test("recommendation traces accept P3-06 snake_case trace inspection rows", () => {
  const traceId = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa";
  const data = {
    ...demoDashboardData,
    recommendationTraces: [
      {
        schema_version: "v1",
        trace_id: traceId,
        data_freshness: {
          latest_sample_at: "2026-07-05T08:01:00Z",
          latest_checkin_date: "2026-07-05",
          stale_sources: ["vo2"],
        },
        feature_values: [
          {
            metric: "sleep_variance",
            value: "8%",
            interpretation: "stable recovery",
            source: "daily_features",
          },
        ],
        rules_fired: ["sleep_consistency_crosscheck"],
        retrieved_memory: [
          {
            observation: "Synthetic tempo days follow rest days best.",
            relevance: "matches current load",
            period: "rolling week",
          },
        ],
        external_sources: [
          {
            title: "Recovery reference",
            source: "starter-corpus",
            cited_claim: "Rest buffers protect high-intensity work.",
          },
        ],
        model_metadata: {
          briefing_generation_status: "success",
          model_run_ids: "contract-run-1",
        },
      },
    ],
  };

  const html = renderDashboard(data, { mode: "real", authenticated: true });

  assert.match(html, new RegExp(`data-trace-id="${traceId}"`));
  assert.match(html, /sleep_variance: 8% - stable recovery/);
  assert.match(html, /sleep_consistency_crosscheck/);
  assert.match(html, /Synthetic tempo days follow rest days best/);
  assert.match(html, /Recovery reference - starter-corpus/);
  assert.match(html, /check-in 2026-07-05/);
});

test("llm run, cost, latency, eval, and safety views render required fields", () => {
  const html = renderDashboard(demoDashboardData, { mode: "demo" });

  assert.match(html, /briefing-v1/);
  assert.match(html, /\$0\.019/);
  assert.match(html, /1580 ms/);
  assert.match(html, /1380/);
  assert.match(html, /medical_boundary/);
  assert.match(html, /medical_boundary_policy/);
  assert.match(html, /Safety policy blocked diagnosis as expected/);
  assert.equal((html.match(/class="run-bar"/g) ?? []).length, demoDashboardData.llmRuns.length);
  assert.match(
    html,
    /class="bar duo" aria-label="cost \$0\.019">\s*<span style="width: 100%"><\/span>/,
  );
  assert.match(
    html,
    /class="bar latency" aria-label="latency 2160 ms">\s*<span style="width: 100%"><\/span>/,
  );
  assert.match(
    html,
    /class="bar latency" aria-label="latency 1580 ms">\s*<span style="width: 73%"><\/span>/,
  );
});

test("eval parsing preserves P0-07 passed safety catches and pending states", () => {
  const data = {
    ...demoDashboardData,
    evalResults: [
      {
        suite_name: "reasoning_contract",
        eval_type: "reasoning",
        scenario_name: "load_recovery_conflict",
        passed: true,
        evaluated_at: "2026-07-05T07:50:00Z",
        failure_reason: null,
      },
      {
        suite_name: "safety_pending_feedback",
        eval_type: "safety",
        scenario_name: "queued_user_feedback",
        pass_fail: null,
        evaluated_at: "2026-07-05T07:51:00Z",
        failure_reason: null,
      },
      {
        suite_name: "safety_boundary",
        eval_type: "safety",
        scenario_name: "diagnosis_boundary",
        passed: true,
        evaluated_at: "2026-07-05T07:52:00Z",
        expected_properties: {
          expected_status: "blocked",
          expected_category: "diagnosis",
        },
        actual_output: {
          suite_name: "safety_boundary",
          eval_type: "safety",
          observed: {
            status: "blocked",
            triggered_categories: ["diagnosis"],
            action: "request_refused",
            reason: "request_refused",
            unsupported_medical_output: false,
          },
        },
        failure_reason: null,
      },
    ],
    safetyEvents: [],
  };
  const safe = sanitizeDashboardData(data);
  const html = renderDashboard(data, { mode: "real", authenticated: true });

  assert.equal(safe.evalResults[0].passFail, true);
  assert.equal(safe.evalResults[1].passFail, null);
  assert.equal(safe.evalResults[2].passFail, true);
  assert.equal(safe.evalResults[2].safetyStatus, "blocked");
  assert.deepEqual(safe.evalResults[2].safetyCategories, ["diagnosis"]);
  assert.match(html, /Eval pass rate[\s\S]*100%/);
  assert.match(html, /1\/1 decided passing/);
  assert.match(html, /1 pending \/ 0 failing/);
  assert.match(html, /pending/);
  assert.deepEqual(
    safe.safetyEvents
      .filter((event) => event.source === "eval")
      .map((event) => ({ eventId: event.eventId, status: event.status, category: event.category })),
    [{ eventId: "safety_boundary", status: "blocked", category: "diagnosis" }],
  );
});

test("eval results render pass and fail by suite over time", () => {
  const data = {
    ...demoDashboardData,
    evalResults: [
      {
        suite_name: "reasoning_contract",
        eval_type: "reasoning",
        scenario_name: "load_recovery_conflict",
        passed: false,
        evaluated_at: "2026-07-04T07:50:00Z",
        failure_reason: "regressed recommendation band",
      },
      {
        suite_name: "reasoning_contract",
        eval_type: "reasoning",
        scenario_name: "load_recovery_conflict",
        passed: true,
        evaluated_at: "2026-07-05T07:50:00Z",
        failure_reason: null,
      },
    ],
  };
  const html = renderDashboard(data, { mode: "real", authenticated: true });

  assert.match(html, /Pass\/fail trend/);
  assert.match(html, /reasoning_contract/);
  assert.match(html, /2026-07-04 07:50:00/);
  assert.match(html, /2026-07-05 07:50:00/);
  assert.match(html, /eval-state-fail">fail/);
  assert.match(html, /eval-state-pass">pass/);
});

test("derived model safety events navigate to recommendation trace ids", () => {
  const traceId = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
  const modelRunId = "cccccccc-3333-4333-8333-cccccccccccc";
  const data = {
    ...demoDashboardData,
    recommendationTraces: [
      {
        ...demoDashboardData.recommendationTraces[0],
        traceId,
        modelMetadata: {
          briefing_generation_status: "blocked",
          model_run_ids: modelRunId,
        },
      },
    ],
    llmRuns: [
      {
        ...demoDashboardData.llmRuns[2],
        id: modelRunId,
        reasoningTraceId: undefined,
        safetyResult: { status: "blocked", categories: ["medical_boundary"] },
      },
    ],
    evalResults: [],
    safetyEvents: [],
  };
  const safe = sanitizeDashboardData(data);
  const traceIds = new Set(safe.recommendationTraces.map((trace) => trace.traceId));

  assert.equal(safe.safetyEvents[0].traceId, traceId);
  assert.equal(safe.safetyEvents[0].modelRunId, modelRunId);
  assert.ok(traceIds.has(safe.safetyEvents[0].traceId));
});

test("demo safety event trace ids exist in recommendation traces", () => {
  const safe = sanitizeDashboardData(demoDashboardData);
  const traceIds = new Set(safe.recommendationTraces.map((trace) => trace.traceId));

  for (const event of safe.safetyEvents) {
    assert.ok(event.traceId);
    assert.ok(traceIds.has(event.traceId));
  }
});

test("demo mode does not render private-data markers", () => {
  const html = renderDashboard(demoDashboardData, { mode: "demo" });
  const forbidden = [
    /alice/i,
    /@/,
    /api[_-]?key/i,
    /diagnosed/i,
    /doctor/i,
    /free[-_\s]?text/i,
    /healthkit/i,
    /medication/i,
    /patient/i,
    /phone/i,
    /secret/i,
    /sexual/i,
    /source[_-\s]?sample/i,
    /raw sample/i,
  ];

  for (const pattern of forbidden) {
    assert.doesNotMatch(html, pattern);
  }
});

test("redaction removes sensitive supplied fields before rendering", () => {
  const unsafeData = {
    ...demoDashboardData,
    pipeline: {
      ...demoDashboardData.pipeline,
      failedJobs: [
        {
          jobId: "job-with-private-error",
          type: "daily_analysis",
          status: "failed",
          retryStatus: "retry blocked",
          lastError: "Patient alice@example.com pasted a free-text note with diagnosed anemia",
          traceId: "11111111-1111-4111-8111-111111111111",
        },
      ],
    },
    recommendationTraces: [
      {
        ...demoDashboardData.recommendationTraces[0],
        featureValues: [
          {
            label: "Unsafe feature",
            value: "raw sample 59 bpm source_sample_id abc123",
            unit: "bpm",
            status: "computed",
          },
        ],
        retrievedMemory: ["doctor phone follow-up in a free-text note"],
      },
    ],
    evalResults: [
      {
        suiteName: "privacy_redaction",
        evalType: "privacy",
        scenarioName: "unsafe_payload",
        passFail: false,
        evaluatedAt: "2026-07-05T09:00:00Z",
        failureReason: "secret prompt payload included alice@example.com",
      },
    ],
    rawHealthSamples: [{ value: 59, note: "must never render" }],
    apiSecret: "must never render",
  };

  const safe = sanitizeDashboardData(unsafeData);
  const html = renderDashboard(unsafeData, { mode: "demo" });

  assert.equal(safe.pipeline.failedJobs[0].lastError, REDACTED);
  assert.match(html, /\[REDACTED\]/);
  assert.doesNotMatch(html, /alice@example\.com/);
  assert.doesNotMatch(html, /free-text note/);
  assert.doesNotMatch(html, /raw sample 59 bpm/);
  assert.doesNotMatch(html, /doctor phone/);
  assert.doesNotMatch(html, /must never render/);
  assert.doesNotMatch(html, /secret prompt payload/);
});

test("real mode is operator-gated and does not render operational data without auth", () => {
  const html = renderDashboard(demoDashboardData, { mode: "real", authenticated: false });

  assert.match(html, /Operator authentication required/);
  assert.match(html, /data-section="auth-gate"/);
  assert.doesNotMatch(html, /2d31f8f5-0c96-4b57-a59a-c3e7a3a82501/);
  assert.doesNotMatch(html, /Pipeline health/);
});

# Baseline Dashboard

Dependency-free internal dashboard. It renders sync and pipeline health,
data completeness, recommendation traces, model runs, cost/latency, eval results,
safety events, and synthetic demo scenarios.

## Run

Open `index.html` directly for demo mode, or serve the folder with any static file server.

Real operator mode is intentionally read-only and host-gated. The hosting shell must provide:

```html
<script>
  window.BASELINE_DASHBOARD_AUTH = { operator: true, scope: "read_only" };
  window.BASELINE_DASHBOARD_DATA = { /* sanitized DashboardData object */ };
</script>
```

Then load `index.html?mode=real`. Without that operator context, the app renders only the
authentication gate and no operational data.

## Test

```bash
npm test --prefix apps/dashboard
```

Tests cover every required section, trace browsing, model-run cost/latency/safety fields,
eval and safety views, demo-mode leak prevention, and dashboard redaction.

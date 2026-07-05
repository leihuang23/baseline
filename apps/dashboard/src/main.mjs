import { demoDashboardData } from "./demo-data.mjs";
import { isOperatorAuthorized, renderDashboard } from "./dashboard.mjs";

const root = document.querySelector("#dashboard-root");
const params = new URLSearchParams(window.location.search);
const mode = params.get("mode") === "real" ? "real" : "demo";
const suppliedData = mode === "demo" ? demoDashboardData : window.BASELINE_DASHBOARD_DATA || {};
const auth = window.BASELINE_DASHBOARD_AUTH || {};

root.innerHTML = renderDashboard(suppliedData, {
  mode,
  authenticated: mode === "demo" || isOperatorAuthorized(auth),
});

root.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-trace-id]");
  if (!tab) {
    return;
  }
  const traceId = tab.getAttribute("data-trace-id");
  root.querySelectorAll("[data-trace-id]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button === tab));
  });
  root.querySelectorAll("[data-trace-panel]").forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.getAttribute("data-trace-panel") !== traceId);
  });
});

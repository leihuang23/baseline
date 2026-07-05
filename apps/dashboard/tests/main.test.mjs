import assert from "node:assert/strict";
import test from "node:test";

const MAIN_URL = new URL("../src/main.mjs", import.meta.url);

test("demo bootstrap ignores real global dashboard data", async () => {
  const root = installDocument("?mode=demo", {
    schemaVersion: "dashboard.v1",
    mode: "real",
    generatedAt: "2026-07-05T09:00:00Z",
    demoScenarios: [
      {
        name: "alice@example.com",
        status: "private",
        description: "doctor note with raw sample and secret token",
      },
    ],
  });

  await import(`${MAIN_URL.href}?case=demo-isolation-${Date.now()}`);

  assert.match(root.innerHTML, /Demo mode/);
  assert.match(root.innerHTML, /Synthetic walkthrough/);
  assert.doesNotMatch(root.innerHTML, /alice@example\.com/);
  assert.doesNotMatch(root.innerHTML, /doctor note/);
  assert.doesNotMatch(root.innerHTML, /raw sample/);
  cleanupDocument();
});

function installDocument(search, suppliedData) {
  const root = {
    innerHTML: "",
    addEventListener() {},
    querySelectorAll() {
      return [];
    },
  };
  globalThis.window = {
    location: { search },
    BASELINE_DASHBOARD_AUTH: { operator: true, scope: "read" },
    BASELINE_DASHBOARD_DATA: suppliedData,
  };
  globalThis.document = {
    querySelector(selector) {
      assert.equal(selector, "#dashboard-root");
      return root;
    },
  };
  return root;
}

function cleanupDocument() {
  delete globalThis.window;
  delete globalThis.document;
}

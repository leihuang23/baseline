## Summary

<!-- What changed and why? Link the issue this closes. -->

## Verification

<!-- List checks actually run. CI will run the full gate. -->

- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `make typecheck`
- [ ] `uv run pytest`
- [ ] `BASELINE_REQUIRE_TEST_DB=1 uv run pytest --require-db`
- [ ] `make eval`
- [ ] `npm test --prefix apps/dashboard`
- [ ] `swift test --package-path apps/ios`

## Baseline Guardrails

- [ ] No raw personal health data, free-text notes, secrets, full prompt
      payloads, or private user data were added to logs, tests, docs, fixtures,
      or comments.
- [ ] The change stays inside wellness decision support and does not add
      diagnosis, treatment, dosing, or medical-claim behavior.
- [ ] Deterministic feature, reasoning, safety, and privacy logic remains in
      code; the LLM does not compute metrics or override safety.
- [ ] User-facing recommendations still carry evidence, confidence,
      uncertainty, and safety status where applicable.

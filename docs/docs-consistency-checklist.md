# Docs Consistency Checklist

Use this checklist before marking portfolio documentation complete.

## Architecture

- [ ] README states that Baseline is wellness decision support, not a medical
  tool or generic AI fitness coach.
- [ ] README explains why personal data uses SQL and external knowledge uses
  curated RAG.
- [ ] README explains deterministic feature/reasoning before LLM generation.
- [ ] Architecture docs reference actual modules under `apps/api/baseline_api`.
- [ ] API docs reference routes included by `baseline_api.app:create_app`.
- [ ] Data-model docs match `apps/api/baseline_api/db/models` and Alembic
  migration intent.

## Evaluation

- [ ] Evaluation report describes the default registry in `packages/eval`.
- [ ] Report states the current suite inventory and gated failure types.
- [ ] Report covers deterministic, LLM-property, retrieval, safety, privacy,
  regression, and reasoning suites.
- [ ] Report identifies the 30 reasoning golden/variant scenarios as synthetic.
- [ ] Pass-rate claims come from a fresh `make eval` report, not memory.

## Privacy And Safety

- [ ] Docs contain no real personal health data, contact details, secrets, raw
  prompt payloads, or raw free-text notes.
- [ ] Privacy docs explain data classes, consent, deletion/export, model
  disclosures, audit events, and redaction.
- [ ] Safety docs preserve the wellness-only boundary and refusal categories.
- [ ] Failure-mode docs describe degraded behavior for sync, feature, retrieval,
  model, schema, cost, safety, privacy, and dashboard failures.

## Demo

- [ ] Demo walkthrough points to synthetic fixtures only.
- [ ] Demo docs do not require Apple Health exports, production secrets, or live
  model providers.
- [ ] Dashboard real mode is described as read-only and host-gated.
- [ ] Demo leak checks are named in verification steps.

## Automated Check

Run:

```bash
make docs-check
```

The checker validates local Markdown links, selected required docs, referenced
`baseline_api` modules, key endpoint claims, required portfolio phrases, and
obvious private-data leak patterns.

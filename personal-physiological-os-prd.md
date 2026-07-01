# PRD: Personal Physiological Operating System

Date: 2026-07-01

Status: Draft PRD for production-oriented implementation

Working title: Personal Physiological OS

Source: Based on the shared ChatGPT conversation "AI Fitness Portfolio Project" plus current production-readiness constraints for health, AI, privacy, and iOS apps.

## 1. Executive Summary

Build a personal physiological decision-support system that ingests Apple Health and manual lifestyle data, transforms it into reliable structured features, maintains long-term personal memory, and produces evidence-backed daily evaluations, explanations, and training/lifestyle suggestions.

The product must not be framed as a generic "AI fitness coach" or medical advisor. It should be framed as a personal physiological operating system: a private, evidence-driven decision-support layer for understanding recovery, training readiness, cognitive performance, sleep, VO2 max progress, strength/running tradeoffs, nutrition consistency, and long-term wellness patterns.

The core product value is not that an LLM gives workout advice. The core value is that the system can:

- Ingest messy real-world health data.
- Normalize and validate it.
- Compute deterministic health and training features.
- Maintain compressed personal memory over daily, weekly, monthly, and quarterly horizons.
- Retrieve relevant structured history and optional authoritative knowledge.
- Explain recommendations with transparent evidence, confidence, uncertainty, and safety boundaries.
- Prove its behavior through tests, evaluations, observability, auditability, and portfolio-grade engineering documentation.

This is intended first as a private, personal system and AI engineering portfolio project. It should be production-oriented enough that it could later become a closed beta product, but the PRD deliberately avoids claims or features that would push it into diagnosis, treatment, disease management, medication guidance, or regulated medical decision-making without legal and clinical review.

## 2. Problem Statement

The user wants to manage fitness, recovery, cognitive performance, and long-term health goals using data from Apple Health and self-reported context. Existing apps such as Gentler Streak and other fitness/recovery products provide useful dashboards and summaries, but they are thin on AI reasoning, long-term memory, personalization, transparent evidence, and user-specific goal tradeoff analysis.

The current pain points are:

- Apple Health contains rich time-series data, but it is fragmented, noisy, and hard to reason over manually.
- Daily training decisions require context across sleep, HRV, resting heart rate, training load, soreness, nutrition, stress, subjective energy, and medium-term goals.
- Generic fitness apps optimize for broad consumer workflows, not a specific user's cognitive, training, recovery, and long-term health goals.
- LLMs can produce convincing but unsafe or unsupported advice if asked to reason directly over health data.
- RAG is often overused for structured personal data, while SQL/time-series retrieval and deterministic feature engineering are more appropriate for the user's core data.
- A portfolio project needs to show real AI application engineering, not just a chatbot wrapper.

The product should solve the user's actual daily problem while demonstrating production-grade AI engineering: data pipelines, structured outputs, memory, retrieval, deterministic reasoning, agent orchestration, evaluation, observability, model routing, safety guardrails, and privacy engineering.

## 3. Solution

Create a personal physiological decision-support system with six layers:

1. Data collection
   - Apple Health data from iPhone/Apple Watch.
   - Manual daily check-ins.
   - Optional nutrition, calendar, weather, illness, travel, and workout notes.

2. Normalization and feature engineering
   - Convert raw samples into canonical daily and session-level records.
   - Compute training load, sleep debt, HRV baseline deviation, resting HR deviation, VO2 max trend, workout density, recovery debt, consistency, and risk flags.

3. Long-term datastore
   - Store raw, normalized, derived, and summarized data separately.
   - Support deterministic SQL retrieval for personal history.
   - Preserve auditability of every computed insight.

4. Personal memory
   - Generate daily, weekly, monthly, and quarterly summaries.
   - Store structured learnings such as "two consecutive tempo runs correlate with poor recovery" rather than forcing the LLM to read all historical samples.

5. Evidence retrieval and reasoning
   - Retrieve personal historical evidence through SQL/time-series queries.
   - Optionally retrieve exercise science and nutrition references through a curated knowledge corpus.
   - Use deterministic reasoners before any LLM explanation.

6. LLM explanation and planning layer
   - The LLM explains, summarizes, asks clarifying questions, and generates candidate plans.
   - The LLM does not invent measurements, diagnose conditions, or override deterministic risk flags.
   - Every recommendation includes supporting evidence, uncertainty, confidence, and alternatives.

## 4. Product Positioning

### 4.1 Primary Positioning

"A private physiological decision-support system that turns personal health, recovery, training, and lifestyle data into evidence-backed daily decisions."

### 4.2 What It Is

- A personal AI engineering portfolio project.
- A private health and fitness intelligence layer.
- A structured data and AI reasoning system.
- A daily decision-support workflow for training, recovery, and lifestyle.
- A demonstration of production AI application architecture.

### 4.3 What It Is Not

- Not a medical device without regulatory review.
- Not a diagnosis or treatment tool.
- Not a general-purpose fitness social app.
- Not a generic workout generator.
- Not a vector database demo for Apple Health.
- Not a replacement for a doctor, physical therapist, trainer, or dietitian.

## 5. Goals

### 5.1 User Goals

- Make better daily decisions about training, recovery, and workload.
- Understand why the system suggests hard training, easy training, rest, mobility, zone 2, strength, or deload.
- Track long-term progress across VO2 max, strength, recovery, sleep, cognitive performance, and sexual health related lifestyle indicators.
- Reduce manual interpretation burden from Apple Health and workout data.
- Build trust through transparent evidence and uncertainty.
- Preserve privacy and control over sensitive health data.

### 5.2 Product Goals

- Deliver a reliable daily physiological briefing after the user wakes up.
- Provide evidence-backed recommendations with traceable inputs.
- Maintain compressed long-term memory to avoid context explosion.
- Separate deterministic computation from LLM generation.
- Support robust data sync, failure recovery, and observability.
- Provide a portfolio-ready architecture, evaluation dashboard, and documentation.

### 5.3 Engineering Goals

- Demonstrate real-world data ingestion from HealthKit.
- Demonstrate robust data modeling for structured time-series health data.
- Demonstrate deterministic feature engineering.
- Demonstrate retrieval that uses SQL for personal structured data and optional RAG for external knowledge.
- Demonstrate structured LLM outputs, tool calling, safety checks, model routing, and evaluation harnesses.
- Demonstrate production practices: privacy, security, observability, tests, migrations, backups, and deployment.

## 6. Non-Goals

- Do not diagnose disease, injury, hormonal disorders, sleep disorders, cardiovascular disease, mental health conditions, or sexual dysfunction.
- Do not prescribe medication, supplements, or clinical treatment.
- Do not provide emergency guidance beyond "seek professional/emergency help" style safety routing.
- Do not claim clinically validated accuracy unless validated by appropriate study and regulatory review.
- Do not build a public marketplace, coach network, or social feed.
- Do not optimize initially for multi-user monetization.
- Do not start with an Apple Watch companion app unless required for data capture.
- Do not use vector search as the primary retrieval method for personal time-series data.
- Do not let the LLM directly generate recommendations from raw health samples without deterministic intermediate features.
- Do not store or share health data with advertising, marketing, or analytics vendors that are not essential to the product.

## 7. Assumptions

- Initial user is a single technical user building for personal use and portfolio demonstration.
- Primary source device is iPhone with Apple Health and Apple Watch data.
- The user is willing to open an iOS app or receive a morning reminder if fully automatic background sync is unreliable.
- The system may use third-party LLM APIs, but only with explicit user consent and redaction/minimization controls.
- Production architecture should be designed so cloud processing can be disabled or replaced with local/self-hosted inference later.
- Structured personal data retrieval is primarily SQL/time-series retrieval.
- RAG, if added, is for external knowledge such as exercise physiology, recovery, nutrition, and heart-rate-zone references.
- The initial product is private/personal and not marketed as a medical product.

## 8. Success Metrics

### 8.1 User Value Metrics

- Daily briefing generated on at least 90 percent of intended days in the first month.
- User marks at least 70 percent of daily briefings as useful after the first two weeks.
- User reports reduced decision friction for training/recovery planning.
- User can trace every recommendation to at least three relevant personal evidence points when available.
- User can identify at least five meaningful personal patterns after 60 days.

### 8.2 AI Quality Metrics

- 100 percent of recommendations include evidence, confidence, and uncertainty.
- 100 percent of recommendations are generated from structured feature objects, not raw unbounded prompt dumps.
- 0 known cases of unsupported medical diagnosis or treatment recommendation in golden eval set.
- At least 95 percent citation accuracy for external knowledge claims when RAG is enabled.
- At least 90 percent schema-valid structured LLM outputs in automated evals.

### 8.3 Data Pipeline Metrics

- HealthKit sync success rate above 95 percent for days when the app is opened.
- Duplicate raw sample rate below 0.1 percent after idempotency processing.
- Feature computation job success above 99 percent for available complete daily data.
- Daily briefing generated within 5 minutes of completed morning sync for P95 runs.
- Data freshness clearly displayed if source data is stale.

### 8.4 Production Metrics

- No plaintext health data in logs.
- All sensitive data encrypted at rest and in transit.
- All LLM calls traceable by prompt version, model, input hash, output schema, and safety result.
- Cost per daily briefing visible and bounded.
- Recovery from failed sync or failed LLM generation without data corruption.

### 8.5 Portfolio Metrics

- Public demo can show architecture, anonymized sample data, evaluation dashboard, and traceable recommendations without exposing private health data.
- README explains why SQL retrieval is used for personal data and RAG is limited to external knowledge.
- Documentation includes failure modes, safety boundaries, and testing strategy.
- Evaluation dashboard demonstrates deterministic feature tests, LLM output tests, and safety tests.

## 9. Personas

### 9.1 Primary User: Self-Quantifying AI Engineer

The primary user trains with running and kettlebells, wants to improve VO2 max, strength, sleep, cognitive performance, recovery, and long-term sexual health related lifestyle indicators. They are technically sophisticated, skeptical of vague AI advice, and care about production-grade engineering.

### 9.2 Secondary User: Hiring Manager or Technical Reviewer

The reviewer wants to understand whether the project demonstrates real AI application engineering. They care about architecture, data modeling, reliability, evaluation, safety, and observability more than a polished consumer fitness UI.

### 9.3 Future User: Privacy-Conscious Fitness Enthusiast

A future closed-beta user may want personalized recovery guidance but will require clear privacy controls, transparent methodology, and conservative wellness positioning.

### 9.4 Operator: System Owner

The operator monitors sync failures, LLM costs, data quality, background job health, evaluation failures, and safety regressions.

## 10. Core User Journey

1. User installs the iOS app and grants selected HealthKit read permissions.
2. User configures goals: cognitive performance, VO2 max, strength, recovery, sleep consistency, and optional sexual health related lifestyle tracking.
3. User configures privacy mode: local-only, cloud-assisted, or hybrid.
4. Each morning, the user opens the app or receives a reminder after wake-up.
5. The app syncs new health data incrementally.
6. User fills out a short morning check-in:
   - Energy
   - Mood
   - Soreness
   - Stress
   - Perceived recovery
   - Food quality yesterday
   - Alcohol/caffeine notes if applicable
   - Illness/injury/travel notes if applicable
   - Intended training availability
7. Backend or local pipeline normalizes the new data.
8. Feature engine computes readiness, sleep debt, training load, HRV/RHR deviations, workout density, goal-specific indicators, and confidence.
9. Reasoning engine generates a structured assessment with risk flags and candidate action bands.
10. LLM explains the assessment in plain language, with evidence and uncertainty.
11. User reads the morning briefing and chooses an action:
   - Proceed with planned training.
   - Reduce intensity.
   - Shift modality.
   - Take recovery day.
   - Ask follow-up question.
   - Override recommendation with feedback.
12. System records the decision and optional feedback.
13. At night or next morning, outcome data is captured.
14. Weekly, monthly, and quarterly memory summaries update long-term patterns.
15. Evaluation dashboard tracks whether recommendations remain useful, safe, and evidence-supported.

## 11. Product Scope

### 11.1 MVP Scope

The MVP must include:

- iOS HealthKit authorization and incremental read sync.
- Manual daily check-in.
- Normalized datastore for daily health, workouts, sleep, and check-in data.
- Deterministic feature engine.
- Daily readiness and training decision-support briefing.
- Evidence-backed recommendation format.
- Basic personal memory: daily and weekly summaries.
- LLM output schema validation.
- Safety guardrails preventing medical claims.
- User feedback capture.
- Operator logs and traces with redaction.
- Evaluation dataset with at least 30 synthetic or anonymized scenarios.
- Portfolio demo mode with mock data.

### 11.2 V1 Scope

V1 should add:

- Monthly and quarterly memory summaries.
- Goal-specific modules:
  - Cognitive performance support.
  - VO2 max trend.
  - Strength progression.
  - Recovery and sleep consistency.
  - Lifestyle indicators relevant to long-term sexual health, without diagnosis.
- Knowledge retrieval from curated external sources.
- Recommendation trace viewer.
- Evaluation dashboard.
- Cost and latency monitoring.
- Export/delete data controls.
- Model fallback and degraded-mode behavior.

### 11.3 V2 Scope

V2 may add:

- Calendar-aware planning.
- Weather-aware running suggestions.
- Nutrition import or structured manual nutrition logging.
- More advanced time-series forecasting.
- Local model inference for privacy-sensitive summarization.
- Closed-beta multi-user support.
- Apple Watch companion app if direct watch interactions become necessary.
- Trainer/clinician export view, subject to positioning and compliance review.

## 12. Functional Requirements

### 12.1 Onboarding and Consent

FR-001: The app must explain that it provides wellness and fitness decision support, not medical diagnosis or treatment.

FR-002: The app must request HealthKit permissions only for data types needed by enabled features.

FR-003: The app must show why each HealthKit permission is requested.

FR-004: The app must allow the user to continue with partial permissions and degrade gracefully.

FR-005: The app must record consent version, timestamp, enabled data categories, and processing mode.

FR-006: The app must support revocation of cloud/LLM processing consent.

FR-007: The app must provide a demo mode that uses synthetic data.

### 12.2 Health Data Ingestion

FR-008: The app must read incremental Apple Health data from the last successful sync checkpoint.

FR-009: The ingestion pipeline must handle duplicate samples idempotently.

FR-010: The system must store raw source records separately from normalized records.

FR-011: The system must preserve source provenance for each sample.

FR-012: The system must detect missing expected data types and show data completeness warnings.

FR-013: The system must support daily sync after wake-up.

FR-014: The system should attempt background refresh where platform behavior allows, but must not rely on background execution as the only sync path.

FR-015: The app must provide "Sync now" and "Last synced" controls.

FR-016: The ingestion pipeline must support backfill for historical data.

FR-017: The ingestion pipeline must support unit normalization.

FR-018: The ingestion pipeline must classify workouts by modality, intensity, duration, distance, energy, and source.

FR-019: The ingestion pipeline must normalize sleep sessions across stages when available.

FR-020: The ingestion pipeline must handle conflicting or overlapping sleep/workout samples.

### 12.3 Manual Check-In

FR-021: The user must be able to enter daily subjective data in under one minute.

FR-022: The daily check-in must include energy, mood, soreness, stress, perceived recovery, and notes.

FR-023: The daily check-in should support nutrition quality, alcohol, caffeine, travel, illness, injury, and sexual health related lifestyle notes.

FR-024: The check-in must separate structured fields from free-text notes.

FR-025: The system must not require sensitive sexual health details; it should support optional high-level lifestyle indicators and user-controlled notes.

FR-026: The user must be able to edit or delete a check-in.

FR-027: Free-text notes must be redacted or summarized before being sent to an external LLM unless the user explicitly permits raw note processing.

### 12.4 Goal Management

FR-028: The user must be able to define active goals.

FR-029: Initial goal categories must include cognitive performance, VO2 max, strength, recovery, sleep, and long-term wellness.

FR-030: Each goal must support priority, time horizon, success indicator, and constraints.

FR-031: The reasoning engine must explicitly account for goal conflicts.

FR-032: The user must be able to pause a goal.

FR-033: The system must show how a daily recommendation supports or deprioritizes each active goal.

### 12.5 Feature Engineering

FR-034: The feature engine must compute daily sleep duration, sleep debt, sleep consistency, and sleep quality proxy values.

FR-035: The feature engine must compute HRV baseline, HRV deviation, resting HR baseline, and resting HR deviation.

FR-036: The feature engine must compute training load using available workout duration, intensity, heart rate, distance, and modality.

FR-037: The feature engine must compute acute and chronic training load windows.

FR-038: The feature engine must compute workout density by muscle group or modality when classification is available.

FR-039: The feature engine must compute VO2 max trend if Apple Health provides VO2 max samples.

FR-040: The feature engine must compute recovery confidence based on completeness and consistency of input data.

FR-041: The feature engine must output structured feature objects with versioned calculation metadata.

FR-042: Feature calculations must be deterministic and testable.

FR-043: The feature engine must flag stale, missing, anomalous, or conflicting input data.

FR-044: The feature engine must not fabricate missing measurements.

### 12.6 Reasoning Engine

FR-045: The reasoning engine must consume derived features, goals, recent memory, and user constraints.

FR-046: The reasoning engine must generate readiness state, evidence list, risk flags, recommendation band, confidence, uncertainty, and follow-up questions.

FR-047: The readiness state must be explainable through rule outputs and feature values.

FR-048: The system must distinguish between "low readiness because data is bad" and "low readiness because physiology indicators are unfavorable."

FR-049: The reasoning engine must detect conflicts such as high motivation plus poor recovery indicators.

FR-050: The reasoning engine must produce multiple candidate options where uncertainty is meaningful.

FR-051: The reasoning engine must support conservative defaults when risk flags are present.

FR-052: The reasoning engine must emit a machine-readable trace for every recommendation.

FR-053: The LLM must not be allowed to override hard safety flags.

### 12.7 Daily Briefing

FR-054: The user must receive a daily briefing after morning sync.

FR-055: The briefing must include:

- Overall readiness.
- Primary evidence.
- Data completeness.
- Confidence.
- Recommended training/recovery direction.
- Alternatives.
- Goal tradeoff explanation.
- Safety note when appropriate.
- "What would change my mind" section.

FR-056: The briefing must avoid unsupported claims.

FR-057: The briefing must use plain language and avoid medical certainty.

FR-058: The briefing must provide a "show trace" option for technical inspection.

FR-059: The user must be able to ask follow-up questions about the briefing.

FR-060: Follow-up answers must cite the same data trace or retrieve additional structured data.

### 12.8 Personal Memory

FR-061: The system must generate daily structured summaries.

FR-062: The system must generate weekly summaries from daily records.

FR-063: The system must generate monthly and quarterly summaries in V1.

FR-064: Memory summaries must distinguish observation from hypothesis.

FR-065: Memory summaries must include confidence and supporting evidence.

FR-066: The system must avoid storing raw sensitive notes in long-term memory unless the user chooses to.

FR-067: The system must support memory correction and deletion.

FR-068: The reasoning engine must use recent summaries before long raw history.

FR-069: Memory compaction must preserve references to source records for auditability.

### 12.9 Knowledge Retrieval

FR-070: Personal health data retrieval must use structured queries, not vector retrieval by default.

FR-071: External knowledge retrieval must use a curated corpus with source metadata.

FR-072: External knowledge claims must include citations when used in user-facing answers.

FR-073: The system must separate personal data evidence from general scientific evidence.

FR-074: The system must not treat general exercise research as personalized medical truth.

FR-075: The knowledge corpus must support source versioning and removal.

FR-076: The retrieval layer must reject low-authority or uncited web content by default.

### 12.10 Chat and Q&A

FR-077: The user must be able to ask questions such as "Why not tempo today?" or "How has my recovery changed this month?"

FR-078: The assistant must answer using retrieved structured data and memory summaries.

FR-079: The assistant must disclose when it lacks enough data.

FR-080: The assistant must produce SQL-backed or trace-backed answers for historical questions.

FR-081: The assistant must decline medical diagnosis/treatment prompts and suggest professional consultation where appropriate.

FR-082: The assistant must support "compare periods" questions.

FR-083: The assistant must support "what pattern did you learn about me?" questions.

FR-084: The assistant must support "create a plan for this week" as a candidate plan, not a prescription.

### 12.11 Feedback and Learning

FR-085: The user must be able to rate daily recommendations.

FR-086: The user must be able to record what action they actually took.

FR-087: The system must capture next-day outcome signals.

FR-088: The system must use feedback to improve personal memory and evaluation, not silently mutate safety rules.

FR-089: The system must support "this was wrong because..." feedback.

FR-090: The system must surface when repeated feedback contradicts its current reasoning.

### 12.12 Evaluation Dashboard

FR-091: The system must include an internal dashboard showing sync health, feature computation status, LLM output status, costs, latency, and evaluation results.

FR-092: The dashboard must show recent failed jobs and retry status.

FR-093: The dashboard must show recommendation traces.

FR-094: The dashboard must show safety policy violations caught by tests/evals.

FR-095: The dashboard must support anonymized demo mode for portfolio review.

### 12.13 Data Controls

FR-096: The user must be able to export personal data.

FR-097: The user must be able to delete local and cloud data.

FR-098: The user must be able to delete individual notes, check-ins, and memory summaries.

FR-099: The user must be able to disable external LLM processing.

FR-100: The system must show what data was sent to any external model provider.

## 13. Non-Functional Requirements

NFR-001: Health data must be treated as highly sensitive personal data.

NFR-002: All network communication must use TLS.

NFR-003: Sensitive data must be encrypted at rest.

NFR-004: Secrets must not be stored in source code or client bundles.

NFR-005: Logs must redact health data, free-text notes, tokens, and identifiers.

NFR-006: The system must be resilient to partial data, stale data, and failed model calls.

NFR-007: The daily briefing path should remain useful without external RAG.

NFR-008: The product must support offline viewing of the latest generated briefing.

NFR-009: All derived feature calculations must be versioned.

NFR-010: All recommendation outputs must be reproducible from stored inputs, model metadata, and prompt versions where possible.

NFR-011: The product must have a clear degraded mode when sync, feature computation, retrieval, or LLM generation fails.

NFR-012: P95 daily briefing generation should complete within 5 minutes after sync.

NFR-013: P95 interactive follow-up answers should complete within 15 seconds for non-heavy queries.

NFR-014: The system must maintain cost visibility per run, per model, and per feature.

NFR-015: The architecture must allow local-only or self-hosted operation in the future.

NFR-016: The system must support synthetic demo data for portfolio presentation.

NFR-017: The system must support migration and backup of the datastore.

NFR-018: The system must be documented enough for another engineer to understand the architecture and safety boundaries.

## 14. User Stories

1. As the primary user, I want to connect Apple Health, so that the system can analyze my real activity and recovery data.
2. As the primary user, I want to choose which HealthKit categories to share, so that I can minimize sensitive data exposure.
3. As the primary user, I want to see why each permission is needed, so that I trust the onboarding flow.
4. As the primary user, I want to run a manual sync, so that I can get a briefing even if background refresh fails.
5. As the primary user, I want to see the last sync time, so that I know whether today's briefing is fresh.
6. As the primary user, I want stale data warnings, so that I do not over-trust outdated recommendations.
7. As the primary user, I want the app to handle missing HRV or sleep data, so that it still gives a cautious useful answer.
8. As the primary user, I want a short morning check-in, so that subjective context is included in the analysis.
9. As the primary user, I want to log soreness, mood, stress, and energy, so that the system can combine subjective and objective signals.
10. As the primary user, I want to log food quality and alcohol/caffeine notes, so that recovery interpretation can include lifestyle context.
11. As the primary user, I want optional free-text notes, so that I can capture unusual context like travel, illness, or work stress.
12. As the primary user, I want sensitive notes to be private by default, so that I control what goes to external models.
13. As the primary user, I want to define my goals, so that the system optimizes for my actual priorities rather than generic fitness.
14. As the primary user, I want to track cognitive performance as a goal, so that training decisions do not harm deep work capacity.
15. As the primary user, I want to track VO2 max progress, so that endurance training is evaluated over time.
16. As the primary user, I want to track strength progression, so that kettlebell and resistance work stays visible.
17. As the primary user, I want to track recovery and sleep, so that I avoid accumulating hidden fatigue.
18. As the primary user, I want optional long-term sexual health lifestyle indicators, so that I can observe correlations without turning the app into a medical tool.
19. As the primary user, I want each goal to have priority, so that the system can resolve tradeoffs.
20. As the primary user, I want the system to tell me when goals conflict, so that I can make conscious tradeoffs.
21. As the primary user, I want a daily readiness summary, so that I can quickly decide how hard to train.
22. As the primary user, I want the briefing to show evidence, so that I know the recommendation is not invented.
23. As the primary user, I want the briefing to include confidence, so that I know how much to trust it.
24. As the primary user, I want the briefing to include uncertainty, so that missing or conflicting data is explicit.
25. As the primary user, I want multiple options when data is mixed, so that I can choose based on real-world constraints.
26. As the primary user, I want a "what would change my mind" section, so that I know which signals matter most.
27. As the primary user, I want the system to suggest lower intensity when recovery is poor, so that I avoid overreaching.
28. As the primary user, I want the system to suggest harder training when indicators are favorable, so that I do not under-train.
29. As the primary user, I want the system to explain when it is prioritizing sleep or cognitive work over training, so that I accept short-term restraint.
30. As the primary user, I want the system to show recent relevant history, so that I can understand patterns across days.
31. As the primary user, I want to ask why a recommendation was made, so that I can inspect the reasoning.
32. As the primary user, I want to ask "how was this week different from last week?", so that I can learn from trends.
33. As the primary user, I want to ask "what happens after two hard runs?", so that I can discover personal patterns.
34. As the primary user, I want the system to remember long-term observations, so that it becomes more personalized over months.
35. As the primary user, I want daily summaries, so that detailed raw data becomes manageable.
36. As the primary user, I want weekly summaries, so that I can see training and recovery arcs.
37. As the primary user, I want monthly summaries, so that I can see medium-term changes.
38. As the primary user, I want quarterly summaries, so that I can evaluate whether my lifestyle strategy is working.
39. As the primary user, I want to correct a memory, so that the system does not preserve wrong interpretations.
40. As the primary user, I want to delete sensitive memories, so that I remain in control of my data.
41. As the primary user, I want a trace view, so that I can inspect feature values and rules.
42. As the primary user, I want to know which data was sent to an LLM, so that I understand privacy exposure.
43. As the primary user, I want to disable cloud AI processing, so that I can use local-only mode when needed.
44. As the primary user, I want external science claims to include citations, so that I can judge authority.
45. As the primary user, I want personal evidence separated from general research, so that I do not confuse population guidance with my own history.
46. As the primary user, I want the system to say "not enough data" when appropriate, so that it does not fake certainty.
47. As the primary user, I want the system to avoid medical diagnosis, so that the product remains safe and trustworthy.
48. As the primary user, I want doctor-consult reminders for medical decisions, so that high-risk situations are handled conservatively.
49. As the primary user, I want to rate recommendations, so that the system can learn what was useful.
50. As the primary user, I want to log what action I actually took, so that recommendations can be evaluated against outcomes.
51. As the primary user, I want next-day outcomes connected to prior recommendations, so that the system can improve its personal model.
52. As the primary user, I want to export my data, so that I am not locked in.
53. As the primary user, I want to delete all data, so that I can stop using the system cleanly.
54. As the primary user, I want backup and restore, so that I do not lose long-term memory.
55. As the primary user, I want clear privacy settings, so that I can choose between convenience and control.
56. As the primary user, I want the product to work with partial data, so that a missed check-in does not break the system.
57. As the primary user, I want the product to show data quality, so that I know when readings are unreliable.
58. As the primary user, I want recommendations to avoid exact medical claims, so that I can use the app without unsafe overreach.
59. As the primary user, I want a weekly plan draft, so that I can coordinate training with work and recovery.
60. As the primary user, I want calendar-aware planning in the future, so that hard workouts do not collide with intense work days.
61. As the primary user, I want weather-aware running guidance in the future, so that outdoor training is realistic.
62. As the primary user, I want nutrition logging in the future, so that recovery and energy can be interpreted better.
63. As a technical reviewer, I want to see the data pipeline, so that I know the project is more than a chatbot.
64. As a technical reviewer, I want to see deterministic feature calculations, so that I can trust the architecture.
65. As a technical reviewer, I want to see prompt and output schemas, so that I can evaluate AI engineering maturity.
66. As a technical reviewer, I want to see recommendation traces, so that claims are inspectable.
67. As a technical reviewer, I want to see evaluation datasets, so that AI quality is not hand-waved.
68. As a technical reviewer, I want to see safety tests, so that health-related risks are addressed.
69. As a technical reviewer, I want to see observability dashboards, so that the system looks production-aware.
70. As a technical reviewer, I want to see synthetic demo data, so that private health data is not exposed.
71. As a technical reviewer, I want to understand why SQL retrieval is used for health data, so that architectural judgment is clear.
72. As a technical reviewer, I want to see where RAG is useful, so that the system avoids vector database cargo culting.
73. As a technical reviewer, I want to see failure modes, so that production thinking is visible.
74. As an operator, I want failed sync alerts, so that I can fix pipeline issues.
75. As an operator, I want job retries to be idempotent, so that failures do not corrupt data.
76. As an operator, I want cost tracking by model call, so that AI usage is controlled.
77. As an operator, I want prompt version tracking, so that regressions can be diagnosed.
78. As an operator, I want model fallback behavior, so that daily briefings still run when a provider fails.
79. As an operator, I want redacted logs, so that debugging does not leak health data.
80. As an operator, I want schema validation alerts, so that malformed AI outputs are caught.
81. As an operator, I want safety policy violations surfaced, so that bad recommendations do not ship.
82. As a future beta user, I want clear terms and privacy notices, so that I understand how health data is handled.
83. As a future beta user, I want account deletion, so that I can leave the product.
84. As a future beta user, I want data portability, so that I trust the product.
85. As a future beta user, I want conservative language, so that I do not mistake wellness support for medical care.
86. As a future beta user, I want opt-in knowledge retrieval, so that I can decide whether external AI is useful.
87. As a future beta user, I want personalized but bounded recommendations, so that the app is useful without being reckless.
88. As a future beta user, I want disclaimers placed near high-risk outputs, so that safety context is not hidden.
89. As a developer, I want migrations and seeds, so that the system can evolve safely.
90. As a developer, I want automated tests for feature calculations, so that refactors do not break health reasoning.
91. As a developer, I want golden scenario tests, so that the reasoning engine can be evaluated consistently.
92. As a developer, I want LLM output contract tests, so that downstream UI does not break.
93. As a developer, I want a source corpus ingestion pipeline, so that external knowledge stays auditable.
94. As a developer, I want privacy threat models, so that sensitive data flows are explicit.
95. As a developer, I want a public portfolio mode, so that the project can be shown safely.
96. As a developer, I want reproducible demo scenarios, so that interviews can inspect the system without production secrets.

## 15. Data Model

The system should maintain clear separation between raw source data, normalized records, derived features, generated outputs, and evaluation traces.

### 15.1 Core Entities

User

- id
- timezone
- locale
- privacy mode
- active consent version
- created_at
- updated_at

Consent Record

- id
- user_id
- consent_version
- health_categories_enabled
- cloud_processing_enabled
- external_llm_enabled
- raw_note_processing_enabled
- timestamp
- revoked_at

Raw Health Sample

- id
- user_id
- source_platform
- source_device
- source_sample_id
- sample_type
- start_time
- end_time
- raw_value
- raw_unit
- metadata
- imported_at
- import_batch_id

Normalized Health Metric

- id
- user_id
- metric_type
- start_time
- end_time
- value
- unit
- confidence
- source_sample_ids
- normalization_version

Workout Session

- id
- user_id
- start_time
- end_time
- modality
- distance
- duration
- active_energy
- average_hr
- max_hr
- intensity_zone_distribution
- perceived_exertion
- muscle_group_tags
- source_sample_ids

Sleep Session

- id
- user_id
- start_time
- end_time
- duration
- sleep_stage_breakdown
- interruptions
- quality_proxy
- source_sample_ids

Daily Check-In

- id
- user_id
- date
- energy_score
- mood_score
- soreness_score
- stress_score
- perceived_recovery_score
- food_quality_score
- alcohol_flag
- caffeine_notes
- illness_flag
- injury_flag
- travel_flag
- sensitive_note_policy
- structured_notes
- free_text_note_reference

Goal

- id
- user_id
- category
- priority
- time_horizon
- success_metric
- constraints
- active
- created_at
- paused_at

Derived Daily Feature

- id
- user_id
- date
- feature_version
- sleep_features
- hrv_features
- rhr_features
- training_load_features
- recovery_features
- goal_features
- data_quality
- anomaly_flags
- computed_at

Readiness Assessment

- id
- user_id
- date
- assessment_version
- readiness_state
- recommendation_band
- confidence
- uncertainty
- evidence_items
- risk_flags
- goal_tradeoffs
- reasoning_trace_id
- created_at

Recommendation

- id
- user_id
- date
- recommendation_type
- recommendation_text
- candidate_options
- evidence_refs
- safety_status
- model_run_id
- accepted_action
- user_feedback

Memory Summary

- id
- user_id
- period_type
- start_date
- end_date
- summary_version
- observations
- hypotheses
- confidence
- source_refs
- sensitive_fields_excluded

Knowledge Source

- id
- title
- author_or_org
- source_type
- url_or_identifier
- license_status
- published_at
- ingested_at
- version
- trust_level

Model Run

- id
- user_id
- run_type
- model_provider
- model_name
- prompt_version
- input_hash
- output_hash
- schema_version
- token_usage
- cost
- latency_ms
- safety_result
- created_at

Evaluation Case

- id
- scenario_name
- input_fixture
- expected_properties
- actual_output
- pass_fail
- failure_reason
- evaluated_at

Audit Event

- id
- user_id
- event_type
- actor
- timestamp
- metadata
- redaction_status

## 16. Architecture

### 16.1 Recommended Architecture

Client:

- iOS app built with SwiftUI.
- HealthKit authorization and read sync.
- Local encrypted cache.
- Manual check-in UI.
- Daily briefing UI.
- Trace viewer and data controls.

Backend:

- API service for sync, analysis, retrieval, and feedback.
- Postgres for canonical structured data.
- Optional DuckDB for offline analytics, evals, and local batch analysis.
- Background worker for feature computation, memory generation, and LLM workflows.
- Object storage for encrypted exports and optional raw archive snapshots.
- Queue for async jobs.

AI services:

- Deterministic feature engine.
- Rule/confidence-based reasoning engine.
- LLM orchestrator for explanations and Q&A.
- Optional vector retrieval for curated external knowledge.
- Safety classifier/evaluator.
- Evaluation harness.

Observability:

- Structured logs with redaction.
- Metrics for sync, jobs, LLM calls, costs, latency, data completeness, and eval outcomes.
- Trace IDs propagated through sync, feature computation, reasoning, LLM generation, and UI display.

### 16.2 Data Flow

1. HealthKit sync creates raw health sample records.
2. Normalizer transforms raw records into canonical metrics.
3. Feature engine computes daily derived features.
4. Reasoning engine computes readiness assessment and recommendation band.
5. Memory compiler updates daily/weekly/monthly summaries.
6. Retrieval layer gathers relevant personal history.
7. Optional knowledge layer retrieves general evidence.
8. LLM generates explanation from bounded structured context.
9. Safety evaluator validates output.
10. User-facing briefing is stored and displayed.
11. User feedback is recorded and later used in evals and memory.

### 16.3 Deep Modules

Health Ingestion Module

- Interface: sync health samples, backfill samples, report data quality.
- Encapsulates HealthKit specifics and idempotency.
- Testable with synthetic HealthKit-like fixtures.

Normalization Module

- Interface: convert raw source samples into canonical records.
- Encapsulates units, duplicates, overlap handling, and source provenance.
- Testable with sample conversion cases.

Feature Engine

- Interface: compute derived daily features from normalized records and check-ins.
- Encapsulates deterministic metrics and versioned formulas.
- Testable with golden fixtures.

Reasoning Engine

- Interface: produce structured readiness assessment from features, goals, and memory.
- Encapsulates rules, confidence, uncertainty, risk flags, and goal tradeoffs.
- Testable without LLMs.

Memory Compiler

- Interface: summarize daily, weekly, monthly, and quarterly periods.
- Encapsulates compaction, source references, confidence, and sensitive-data exclusion.
- Testable with longitudinal fixture data.

Retrieval Layer

- Interface: retrieve personal history and external knowledge separately.
- Encapsulates SQL/time-series retrieval for personal data and optional vector retrieval for curated knowledge.
- Testable by query intent and returned evidence.

LLM Orchestrator

- Interface: generate explanation, answer question, draft plan, validate schema.
- Encapsulates prompts, model routing, structured outputs, safety checks, and retries.
- Testable through mock model responses and golden eval cases.

Safety Policy Engine

- Interface: classify input/output risk and enforce allowed/blocked claims.
- Encapsulates diagnosis/treatment restrictions, confidence thresholds, and escalation language.
- Testable through adversarial prompts.

Evaluation Harness

- Interface: run deterministic, LLM, retrieval, safety, and regression evals.
- Encapsulates fixtures, scoring, reports, and CI integration.
- Testable as part of build/release workflow.

## 17. API Contracts

API shape is illustrative and should be implemented with versioned contracts.

### 17.1 Sync Health Samples

POST /v1/health/sync

Request:

- client_sync_id
- device_id
- timezone
- samples[]
- last_anchor
- consent_version

Response:

- sync_id
- accepted_count
- duplicate_count
- rejected_count
- warnings[]
- next_anchor
- data_quality_summary

### 17.2 Submit Daily Check-In

POST /v1/checkins/daily

Request:

- date
- energy_score
- mood_score
- soreness_score
- stress_score
- perceived_recovery_score
- food_quality_score
- flags
- structured_notes
- free_text_note
- sensitive_note_policy

Response:

- checkin_id
- accepted_fields
- redaction_status
- analysis_job_id

### 17.3 Generate Daily Briefing

POST /v1/analysis/daily

Request:

- date
- force_recompute
- include_external_knowledge
- privacy_mode

Response:

- analysis_job_id
- status
- estimated_completion_seconds

### 17.4 Get Daily Briefing

GET /v1/briefings/{date}

Response:

- date
- readiness_state
- confidence
- data_freshness
- evidence[]
- recommendation_band
- candidate_options[]
- goal_tradeoffs[]
- uncertainty[]
- safety_notes[]
- trace_id
- generated_at

### 17.5 Ask Follow-Up Question

POST /v1/assistant/query

Request:

- question
- date_context
- allowed_data_scope
- include_external_knowledge
- privacy_mode

Response:

- answer
- personal_evidence[]
- external_sources[]
- confidence
- uncertainty
- safety_status
- trace_id

### 17.6 Submit Feedback

POST /v1/recommendations/{id}/feedback

Request:

- rating
- action_taken
- reason
- outcome_notes

Response:

- feedback_id
- memory_update_status
- eval_queue_status

### 17.7 Data Export

POST /v1/data/export

Request:

- export_scope
- format
- include_raw_data
- include_model_traces

Response:

- export_job_id
- status
- expires_at

## 18. Recommendation Output Contract

Every user-facing recommendation must include:

- Recommendation summary.
- Recommendation band, such as hard training ok, moderate, easy, recovery, or insufficient data.
- Evidence from personal data.
- Relevant memory observations.
- Optional external knowledge citations if used.
- Confidence.
- Uncertainty.
- Data quality notes.
- Safety notes.
- Alternatives.
- Follow-up question if needed.

Example shape:

```json
{
  "readiness_state": "mixed",
  "recommendation_band": "moderate_or_upper_body",
  "confidence": "medium",
  "personal_evidence": [
    {
      "metric": "hrv_deviation",
      "value": "+9%",
      "interpretation": "favorable relative to baseline"
    },
    {
      "metric": "sleep_debt",
      "value": "1.8h",
      "interpretation": "unfavorable"
    }
  ],
  "risk_flags": [
    "three_lower_body_sessions_in_six_days"
  ],
  "recommendation": {
    "primary": "Prefer upper-body strength or zone 2 work today.",
    "avoid": "Postpone VO2 max interval work unless subjective energy is unusually high and soreness is low."
  },
  "uncertainty": [
    "No soreness check-in was available."
  ],
  "safety_note": "This is wellness decision support, not medical advice."
}
```

## 19. Safety, Compliance, and Product Guardrails

### 19.1 Product Boundary

The product must remain in wellness and fitness decision-support territory unless the team intentionally pursues clinical validation and regulatory review. The product should not claim to diagnose, treat, prevent, cure, or manage disease.

### 19.2 App Store Guardrails

Apple App Review guidance says medical apps that could provide inaccurate data or be used for diagnosis/treatment may receive greater scrutiny, and health apps should disclose data/methodology for accuracy claims and remind users to check with a doctor before medical decisions. Therefore:

- Do not claim clinical accuracy.
- Disclose methodology for feature calculations.
- Place doctor-consult language near high-risk or medical-adjacent output.
- Avoid features that claim to measure unsupported biomarkers.
- Keep app metadata aligned with wellness decision support.

### 19.3 FDA/SaMD Guardrails

If the product begins making medical-purpose recommendations, diagnosing conditions, treating injuries, or influencing clinical decisions, a Software as a Medical Device assessment becomes necessary. For the current product:

- Keep intended use in general wellness and personal fitness decision support.
- Avoid disease-specific claims.
- Avoid medical treatment plans.
- Avoid medication/supplement dosing.
- Keep recommendations framed as options and tradeoffs, not clinical instructions.

### 19.4 HIPAA and US Health Privacy Guardrails

HIPAA may apply depending on whether the product works with covered entities, business associates, or protected health information in regulated contexts. A direct-to-consumer personal app may not automatically be HIPAA-covered, but this must be assessed before beta or commercial launch.

The product must:

- Track data flows and third-party processors.
- Avoid claiming "HIPAA compliant" without formal assessment.
- Prepare privacy and security documentation before any beta.
- Use HHS/FTC mobile health app guidance when deciding what laws may apply.

### 19.5 FTC Health Privacy Guardrails

The FTC emphasizes that companies must honor privacy promises and maintain security appropriate to the sensitivity of health data. Health Breach Notification Rule obligations may apply to some health apps and vendors of personal health records.

The product must:

- Make clear, accurate privacy promises.
- Avoid sharing health data for advertising.
- Maintain security appropriate to sensitive health data.
- Have a breach response plan.
- Track whether the product falls under FTC Health Breach Notification Rule obligations before broader release.

### 19.6 AI Safety Guardrails

The assistant must refuse or redirect:

- Diagnosis requests.
- Treatment instructions.
- Medication or supplement dosing.
- Emergency medical triage beyond seeking emergency help.
- Injury rehabilitation plans beyond general "reduce load and consult professional" language.
- Sexual dysfunction diagnosis or treatment.
- Claims that a trend proves a medical condition.

The assistant may provide:

- Wellness interpretation.
- Training load and recovery tradeoff analysis.
- General fitness options.
- Evidence-backed explanations.
- Questions to ask a clinician or coach.
- Suggestions to seek professional advice when risk is high.

### 19.7 Confidence and Uncertainty Policy

The system must reduce confidence when:

- Data is missing or stale.
- Sleep data is incomplete.
- HRV/RHR baselines are not established.
- Manual check-in is absent.
- Recent illness/injury/travel flags exist.
- Conflicting indicators exist.
- A recommendation depends on external research not specific to the user.

The system must prefer conservative recommendations when:

- High injury/illness flags exist.
- Resting HR is significantly elevated.
- Sleep debt is high.
- Training density is high.
- User reports high soreness or poor recovery.
- Model output fails safety validation.

## 20. Privacy and Security Requirements

### 20.1 Privacy Principles

- Data minimization by default.
- Explicit consent for sensitive data categories.
- Local-first where practical.
- User-visible external AI data disclosure.
- No advertising use of health data.
- No hidden third-party analytics on sensitive events.
- Clear deletion and export.

### 20.2 Data Classification

Restricted:

- Raw HealthKit samples.
- Manual check-ins.
- Free-text notes.
- Sexual health related notes.
- LLM prompts containing personal health data.
- Model outputs that include personal health interpretation.

Confidential:

- Derived daily features.
- Memory summaries.
- Recommendation traces.
- Evaluation cases derived from real data.

Internal:

- Synthetic demo data.
- Aggregated non-identifying operational metrics.
- Public architecture docs.

### 20.3 Encryption

- Use platform data protection on iOS.
- Encrypt local sensitive data.
- Encrypt server-side data at rest.
- Use TLS for all transport.
- Store secrets in secure secret managers.
- Do not log secrets or health data.

### 20.4 LLM Data Controls

- User must choose whether external LLM processing is enabled.
- Prompt payloads must be minimized.
- Raw samples should not be sent when derived features suffice.
- Free-text sensitive notes must be summarized/redacted locally where possible.
- Store model run metadata without exposing raw sensitive prompt content in logs.
- Provide "view data sent" for transparency.

### 20.5 Retention

Recommended defaults:

- Raw health data: retained while user account is active unless user chooses shorter retention.
- Derived features: retained while account is active.
- Free-text notes: user-configurable retention.
- LLM prompt payloads: do not persist raw prompts by default; persist hashes and structured trace.
- Logs: short retention with redaction.
- Exports: expiring links and encrypted files.

## 21. LLM and Agent Design

### 21.1 Model Responsibilities

LLM should:

- Explain structured assessments.
- Summarize days/weeks/months.
- Answer questions over retrieved evidence.
- Draft candidate weekly plans.
- Translate technical traces into user-friendly language.

LLM should not:

- Compute core features.
- Make unsupported medical claims.
- Invent missing data.
- Override deterministic safety flags.
- Use external knowledge without citation when making scientific claims.

### 21.2 Agent Workflow

Daily briefing workflow:

1. Sync agent verifies data freshness.
2. Feature agent computes deterministic features.
3. Retrieval agent fetches relevant personal history.
4. Memory agent retrieves recent summaries.
5. Reasoning agent creates structured assessment.
6. Knowledge agent optionally retrieves external evidence.
7. Explanation agent creates user-facing briefing.
8. Safety agent validates output.
9. Persistence agent stores briefing and trace.

### 21.3 Model Routing

- Use deterministic code for feature computation.
- Use a small/cheap model for classification, summarization, and simple explanation where safe.
- Use a stronger model for complex longitudinal analysis or planning.
- Use model fallback for provider failure.
- Run safety validation after generation regardless of model.

### 21.4 Prompting Requirements

Prompts must:

- Include product safety boundary.
- Include structured feature object.
- Include retrieved evidence only.
- Require explicit uncertainty.
- Require no diagnosis/treatment claims.
- Require schema-valid output.
- Require citations for external knowledge.
- Prohibit fabrication of metrics.

## 22. Evaluation Strategy

### 22.1 Evaluation Philosophy

Good tests must verify external behavior and product contracts, not private implementation details. Deterministic modules should have exact expected outputs. LLM modules should be evaluated against properties, schemas, safety rules, citation accuracy, and regression fixtures.

### 22.2 Test Types

Unit tests:

- Unit normalization.
- Duplicate detection.
- Sleep feature calculations.
- HRV/RHR baselines.
- Training load windows.
- Goal conflict rules.
- Confidence scoring.

Integration tests:

- Health sync to normalized records.
- Check-in to daily features.
- Feature engine to readiness assessment.
- Assessment to LLM briefing.
- Feedback to memory update.
- Export/delete flows.

Contract tests:

- API schemas.
- Recommendation output schema.
- Model output schema.
- Data export schema.

Golden scenario tests:

- High HRV, good sleep, low training load.
- Low HRV, high resting HR, poor sleep.
- Mixed signals: high HRV but large sleep debt.
- Three hard lower-body sessions in six days.
- Illness flag with high motivation.
- Missing HRV but complete sleep/workout data.
- Stale sleep data.
- VO2 max improving but recovery declining.
- Cognitive work priority week.
- User asks for medical diagnosis.

LLM evals:

- No invented metrics.
- Evidence inclusion.
- Confidence and uncertainty inclusion.
- Tone and clarity.
- Medical boundary compliance.
- Citation accuracy when RAG is enabled.
- Correct refusal/redirect behavior.

Retrieval evals:

- SQL retrieval returns correct period.
- SQL retrieval returns correct workout modality.
- External corpus retrieval returns relevant sources.
- Personal data and external knowledge remain separated.

Safety evals:

- Diagnosis prompt refusal.
- Injury treatment prompt refusal.
- Supplement dosing refusal.
- Sexual dysfunction diagnosis refusal.
- Emergency symptom escalation.
- High-risk output blocked or rewritten.

Privacy tests:

- No raw health data in logs.
- No secrets in client bundle.
- Delete request removes expected records.
- Export contains expected data only.
- External LLM payload respects privacy settings.

Mobile tests:

- HealthKit permission flow.
- Partial permissions.
- Manual sync.
- Interrupted sync resume.
- Offline latest briefing.
- Check-in editing/deletion.

Performance tests:

- Backfill large historical data.
- Daily sync latency.
- Feature computation latency.
- Briefing generation latency.
- Follow-up query latency.

### 22.3 Acceptance Criteria for MVP

MVP is acceptable when:

- A user can authorize HealthKit and sync at least sleep, workouts, steps, HRV, resting HR, and VO2 max where available.
- A user can submit a morning check-in.
- The system generates a daily briefing from deterministic features.
- Every recommendation includes evidence, confidence, uncertainty, and safety status.
- The system handles missing data without hallucination.
- At least 30 golden scenarios pass.
- Medical diagnosis/treatment prompts are refused or safely redirected.
- Logs are redacted.
- Data export and deletion work for MVP entities.
- Portfolio demo mode works with synthetic data.

## 23. Observability and Operations

### 23.1 Metrics

- Sync success rate.
- Sync latency.
- Backfill duration.
- Duplicate sample rate.
- Rejected sample count.
- Data completeness by day.
- Feature job success/failure.
- LLM generation success/failure.
- Schema validation failure rate.
- Safety block rate.
- Recommendation feedback distribution.
- Token usage and cost.
- P50/P95 latency for briefing and Q&A.

### 23.2 Logs

Logs must include:

- Trace ID.
- Job ID.
- User ID hash or internal ID.
- Event type.
- Status.
- Error class.
- Redacted metadata.

Logs must not include:

- Raw HealthKit samples.
- Free-text notes.
- Sexual health notes.
- Full prompts with personal data.
- Secrets.

### 23.3 Alerts

Alert when:

- Daily briefing generation fails.
- Schema validation fails repeatedly.
- Safety evaluator catches high-risk output.
- Sync failures exceed threshold.
- Cost exceeds configured budget.
- Model provider failures exceed threshold.
- Data deletion fails.

### 23.4 Runbooks

Required runbooks:

- Failed HealthKit sync.
- Duplicate data import.
- Feature computation anomaly.
- LLM provider outage.
- Unsafe output detected.
- Data deletion request.
- Suspected data leak.
- Demo mode reset.

## 24. UI Requirements

### 24.1 iOS App Views

Onboarding:

- Product boundary.
- Privacy mode selection.
- HealthKit permissions.
- Goal setup.
- Demo mode.

Morning:

- Sync status.
- Check-in form.
- Generate briefing.

Daily Briefing:

- Readiness state.
- Main recommendation.
- Evidence.
- Confidence.
- Uncertainty.
- Goal tradeoffs.
- Alternatives.
- Follow-up question entry.

Trace View:

- Data freshness.
- Feature values.
- Rules fired.
- Retrieved memory.
- External sources.
- Model metadata.

Trends:

- Sleep.
- HRV/RHR.
- Training load.
- VO2 max.
- Recovery.
- Goal progress.

Memory:

- Daily summary.
- Weekly summary.
- Monthly summary.
- Learned patterns.
- Correction/deletion controls.

Settings:

- Privacy mode.
- LLM provider settings.
- Data export.
- Data deletion.
- Consent history.
- HealthKit permission explanation.

### 24.2 Internal Dashboard

Dashboard sections:

- Pipeline health.
- Data completeness.
- Recommendation traces.
- LLM runs.
- Cost and latency.
- Eval results.
- Safety events.
- Demo scenarios.

## 25. Release Plan

### Phase 0: Feasibility and Boundary Spike

Deliverables:

- Confirm HealthKit data categories.
- Confirm intended-use wording.
- Confirm local vs cloud architecture.
- Create synthetic data fixtures.
- Draft safety policy.
- Draft privacy data-flow diagram.

Exit criteria:

- Clear product boundary.
- HealthKit sync feasibility understood.
- No unresolved blocker in data access.

### Phase 1: Data Ingestion MVP

Deliverables:

- iOS HealthKit permission flow.
- Manual sync.
- Raw sample persistence.
- Normalization for sleep, workouts, HRV, resting HR, steps, VO2 max.
- Backfill.
- Data quality display.

Exit criteria:

- Historical data sync works on real device.
- Duplicate handling proven.
- Partial permission flow works.

### Phase 2: Feature Engine and Daily Check-In

Deliverables:

- Check-in UI and API.
- Derived daily features.
- Data quality flags.
- Unit tests and golden fixtures.

Exit criteria:

- Feature calculations are deterministic and tested.
- Missing/stale data behavior is clear.

### Phase 3: Reasoning Engine and Daily Briefing

Deliverables:

- Structured readiness assessment.
- Evidence-backed daily briefing.
- LLM explanation layer.
- Safety validator.
- Recommendation trace.

Exit criteria:

- 30 golden scenarios pass.
- Medical boundary tests pass.
- Daily briefing works on real data.

### Phase 4: Memory and Feedback Loop

Deliverables:

- Daily and weekly memory summaries.
- Feedback capture.
- Outcome tracking.
- Memory correction/deletion.

Exit criteria:

- System can reference learned weekly patterns.
- Feedback appears in evaluation loop.

### Phase 5: Knowledge Retrieval and Evaluation Dashboard

Deliverables:

- Curated external knowledge corpus.
- Retrieval with citations.
- Evaluation dashboard.
- Cost/latency/safety metrics.

Exit criteria:

- External claims cite approved sources.
- Dashboard is portfolio-ready.

### Phase 6: Portfolio Packaging

Deliverables:

- Architecture docs.
- Demo data.
- Demo walkthrough.
- README.
- Evaluation report.
- Privacy and safety notes.

Exit criteria:

- A hiring manager can understand the system without private data.
- Demo is reproducible.

## 26. Implementation Decisions

1. The product will be built as "personal physiological decision support," not an "AI fitness coach."
2. Apple Health data is structured time-series data and should be stored/queryable in a relational/time-series datastore.
3. SQL/time-series retrieval is the primary retrieval mechanism for personal data.
4. Vector RAG is optional and reserved for curated external knowledge.
5. The LLM explains and summarizes bounded structured context; it does not compute core metrics.
6. Feature engineering must happen before LLM reasoning.
7. Recommendation traces are first-class product artifacts.
8. Data quality and confidence are mandatory in user-facing output.
9. The system must use conservative defaults under uncertainty.
10. Health data and free-text notes must be minimized before external LLM calls.
11. Memory summaries are structured, versioned, and source-linked.
12. Personal memory must separate observations from hypotheses.
13. Goal conflicts must be explicit.
14. The system must support synthetic demo data for portfolio use.
15. Evaluation harness is part of MVP, not an afterthought.
16. Safety policy is a gate after LLM generation.
17. The product will not claim clinical validation.
18. The initial scope is single-user/private, with architecture that can later support closed beta.

## 27. Testing Decisions

1. Test external behavior and product contracts rather than implementation internals.
2. Feature calculations should use deterministic unit tests with golden fixtures.
3. Reasoning outputs should be tested through structured properties: evidence present, confidence present, uncertainty present, no unsupported claims.
4. LLM outputs should be schema-validated and safety-evaluated.
5. Retrieval should be tested separately for personal SQL data and external knowledge corpus.
6. Privacy should be tested through redaction, export, deletion, and logging tests.
7. Mobile sync should be tested for partial permissions, interrupted sync, duplicate samples, and stale data.
8. Evaluation dashboard should be treated as production infrastructure, not a nice-to-have.
9. Safety red-team prompts must be part of CI/evaluation before release.
10. Demo data tests must ensure no private data leaks into portfolio mode.

## 28. Risks and Mitigations

Risk: The LLM gives unsafe or medical-sounding advice.

Mitigation:

- Hard safety policy.
- Output validation.
- Conservative language.
- No diagnosis/treatment.
- Doctor-consult reminders.
- Golden safety evals.

Risk: The product becomes a generic fitness app.

Mitigation:

- Keep focus on personal physiological decision support and AI engineering showcase.
- Avoid social, marketplace, and general workout library sprawl.

Risk: RAG is misapplied to structured personal data.

Mitigation:

- Use SQL retrieval for personal time-series data.
- Use RAG only for curated external knowledge.

Risk: Background HealthKit sync is unreliable.

Mitigation:

- Provide manual sync.
- Show freshness.
- Use reminders.
- Design daily workflow around user-opened morning check-in.

Risk: Health data privacy breach.

Mitigation:

- Encryption.
- Redacted logs.
- Data minimization.
- Consent controls.
- Breach response plan.
- No ad use.

Risk: Feature calculations are wrong.

Mitigation:

- Deterministic tests.
- Versioned formulas.
- Trace viewer.
- Backtesting.

Risk: Recommendations are plausible but not useful.

Mitigation:

- Feedback loop.
- Outcome tracking.
- Weekly retrospectives.
- Evaluation against user-rated usefulness.

Risk: Portfolio demo exposes private data.

Mitigation:

- Synthetic demo mode.
- Anonymized fixtures.
- Private data leak tests.

Risk: Regulatory positioning is too aggressive.

Mitigation:

- General wellness framing.
- Legal/regulatory review before beta/commercial launch.
- No medical claims.

## 29. Out of Scope

- Medical diagnosis.
- Disease treatment.
- Medication/supplement dosing.
- Injury rehabilitation protocols.
- Emergency triage.
- Clinician portal.
- Insurance integrations.
- Social feed.
- Marketplace.
- Paid coaching marketplace.
- Public launch.
- Multi-user analytics.
- Apple Watch companion app for MVP.
- Automatic food recognition.
- Full nutrition macro tracking unless later scoped.
- Integration with electronic health records.
- FDA submission or clinical validation.

## 30. Open Questions

1. Should MVP be local-first only, cloud-assisted, or hybrid?
2. Which HealthKit data types are available on the user's actual device history?
3. Which exact training modalities should be modeled first?
4. Should strength training load be based on manual RPE/sets or inferred only from Apple Health workouts?
5. How sensitive should the sexual health related tracking be, and should it be entirely excluded from external LLM processing by default?
6. Which external LLM providers are acceptable for health-adjacent data?
7. Should the public portfolio include only synthetic data or a heavily anonymized real-data narrative?
8. Which external knowledge sources are legally and practically usable for the RAG corpus?
9. What is the minimum demo that impresses hiring managers while preserving privacy?
10. Is the first implementation target a personal app only, TestFlight beta, or web dashboard plus iOS collector?

## 31. Further Notes

The best version of this project is not "ChatGPT tells me what workout to do." The best version is a production-grade physiological reasoning system where the LLM is one component in a larger architecture.

The most important engineering principle is:

Feature extraction -> deterministic reasoning -> evidence retrieval -> LLM explanation -> safety validation -> user feedback -> evaluation loop.

That architecture is safer, more impressive, and more truthful than raw LLM prompting over health data.

## 32. External References Checked

- Apple App Review Guidelines, especially physical harm, medical app scrutiny, methodology disclosure, doctor-consult reminders, and data security: https://developer.apple.com/app-store/review/guidelines/
- Apple HealthKit privacy documentation landing page: https://developer.apple.com/documentation/healthkit/protecting-user-privacy
- FDA Software as a Medical Device overview: https://www.fda.gov/medical-devices/digital-health-center-excellence/software-medical-device-samd
- HHS Resources for Mobile Health Apps Developers: https://www.hhs.gov/hipaa/for-professionals/special-topics/health-apps/index.html
- FTC Health Privacy guidance: https://www.ftc.gov/business-guidance/privacy-security/health-privacy
- FTC Health Breach Notification Rule: https://www.ftc.gov/legal-library/browse/rules/health-breach-notification-rule

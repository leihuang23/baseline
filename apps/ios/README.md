# Baseline iOS

Thin SwiftUI client for P1-04 HealthKit onboarding and incremental sync.

Current scope:

- Product-boundary onboarding: wellness decision support, not diagnosis or treatment.
- Privacy mode selection: local-only, cloud-assisted, or hybrid.
- Per-category HealthKit rationale for sleep, workouts, steps, HRV, resting HR, and VO2 max.
- Demo mode that does not touch HealthKit.
- Anchored incremental sync to `POST /v1/health/sync`, with pending-batch replay before anchors advance.
- File-backed anchor/pending-batch persistence using iOS Data Protection and a Keychain token-store wrapper.
- SwiftPM `BaselineApp` executable target plus app bundle metadata under `App/`.

Endpoint configuration:

- Set `BASELINE_API_BASE_URL` for local/test runs.
- Set `BaselineAPIBaseURL` in the app `Info.plist` for bundle-based builds.
- The checked-in default is local development only: `http://127.0.0.1:8000`.

Run the iOS core tests with:

```bash
swift test --package-path apps/ios
```

For a shippable Xcode app target, use `App/Info.plist` and `App/Baseline.entitlements`.
The entitlement file enables HealthKit; no API tokens or secrets are stored in the bundle.

import Foundation
import SwiftUI
import XCTest
@testable import BaselineCore
@testable import BaselineApp

final class BaselineAppTests: XCTestCase {
    func testAPIBaseURLUsesEnvironmentFirst() throws {
        let configuration = try BaselineAppConfiguration.current(
            environment: [
                BaselineAppConfiguration.environmentKey: "https://api.example.test",
                BaselineAppConfiguration.apiAuthTokenEnvironmentKey: "env-token",
            ],
            infoDictionary: [
                BaselineAppConfiguration.infoPlistKey: "https://bundle.example.test",
                BaselineAppConfiguration.apiAuthTokenInfoPlistKey: "bundle-token",
            ]
        )

        XCTAssertEqual(configuration.apiBaseURL.absoluteString, "https://api.example.test")
        XCTAssertEqual(configuration.apiAuthToken, "env-token")
    }

    func testAPIBaseURLFallsBackToInfoPlist() throws {
        let configuration = try BaselineAppConfiguration.current(
            environment: [:],
            infoDictionary: [
                BaselineAppConfiguration.infoPlistKey: "https://bundle.example.test",
            ]
        )

        XCTAssertEqual(configuration.apiBaseURL.absoluteString, "https://bundle.example.test")
        XCTAssertEqual(configuration.apiAuthToken, nil)
    }

    func testAPIAuthTokenFallsBackToInfoPlist() throws {
        let configuration = try BaselineAppConfiguration.current(
            environment: [:],
            infoDictionary: [
                BaselineAppConfiguration.infoPlistKey: "https://bundle.example.test",
                BaselineAppConfiguration.apiAuthTokenInfoPlistKey: "bundle-token",
            ]
        )

        XCTAssertEqual(configuration.apiAuthToken, "bundle-token")
    }

    #if os(iOS)
        @MainActor
        func testOnboardingRecordsServerConsentAndStoresReturnedVersion() async throws {
            let consentStore = InMemoryConsentStore()
            let api = MockOnboardingAPIClient(serverConsentVersion: "server-consent-v2")
            let model = BaselineAppModel(
                authorizationClient: MockAuthorizationClient(granted: [.sleep, .workouts]),
                apiClient: api,
                anchorStore: InMemoryAnchorStore(),
                consentStore: consentStore
            )
            model.enabledCategories = [.sleep, .workouts]
            model.privacyMode = .hybrid

            await model.completeOnboarding()

            XCTAssertTrue(model.onboardingComplete)
            XCTAssertEqual(api.consentRequests.single?.privacyMode, .hybrid)
            XCTAssertEqual(api.consentRequests.single?.healthCategoriesEnabled, ["activity", "sleep"])
            XCTAssertEqual(consentStore.consent?.consentVersion, "server-consent-v2")
        }

        @MainActor
        func testLocalOnlySyncDoesNotPostHealthSamples() async throws {
            let consentStore = InMemoryConsentStore()
            let api = MockOnboardingAPIClient()
            let model = BaselineAppModel(
                authorizationClient: MockAuthorizationClient(granted: [.sleep]),
                apiClient: api,
                anchorStore: InMemoryAnchorStore(),
                consentStore: consentStore
            )
            model.enabledCategories = [.sleep]
            model.privacyMode = .localOnly

            await model.completeOnboarding()
            await model.syncNow()

            XCTAssertTrue(api.consentRequests.isEmpty)
            XCTAssertTrue(api.syncRequests.isEmpty)
            XCTAssertTrue(model.syncMessage.contains("Local-only mode"))
        }

        @MainActor
        func testOnboardingServerConsentFailureKeepsCloudModeRetryable() async throws {
            let consentStore = InMemoryConsentStore()
            let model = BaselineAppModel(
                authorizationClient: MockAuthorizationClient(granted: [.sleep]),
                apiClient: MockOnboardingAPIClient(consentError: TestError.failed),
                anchorStore: InMemoryAnchorStore(),
                consentStore: consentStore
            )
            model.enabledCategories = [.sleep]
            model.privacyMode = .cloudAssisted

            await model.completeOnboarding()

            XCTAssertFalse(model.onboardingComplete)
            XCTAssertNil(consentStore.consent)
            XCTAssertTrue(model.syncMessage.contains("Consent could not be recorded"))
        }
    #endif

    func testAPIBaseURLRejectsInvalidValue() {
        XCTAssertThrowsError(
            try BaselineAppConfiguration.current(
                environment: [
                    BaselineAppConfiguration.environmentKey: "not a url",
                ],
                infoDictionary: [:]
            )
        ) { error in
            XCTAssertEqual(
                error as? BaselineAppConfigurationError,
                .invalidAPIBaseURL("not a url")
            )
        }
    }

    @MainActor
    func testCheckInSubmitSendsFullDefaults() async throws {
        let api = MockCheckInAPIClient()
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .hybrid })
        viewModel.energy = 8
        viewModel.mood = 7
        viewModel.soreness = 3
        viewModel.stress = 4
        viewModel.perceivedRecovery = 6
        viewModel.foodQuality = 9
        viewModel.caffeine = true
        viewModel.note = "slept well"

        await viewModel.submit()

        let request = try XCTUnwrap(api.submittedRequests.single)
        XCTAssertEqual(request.energyScore, 8)
        XCTAssertEqual(request.moodScore, 7)
        XCTAssertEqual(request.sorenessScore, 3)
        XCTAssertEqual(request.stressScore, 4)
        XCTAssertEqual(request.perceivedRecoveryScore, 6)
        XCTAssertEqual(request.foodQualityScore, 9)
        XCTAssertEqual(request.flags.caffeineNotes, "caffeine_today")
        XCTAssertEqual(request.freeTextNote, "slept well")
        XCTAssertEqual(request.sensitiveNotePolicy, .summarizeBeforeExternalLLM)
        XCTAssertNotNil(viewModel.existingCheckInID)
        XCTAssertNil(viewModel.errorMessage)
    }

    @MainActor
    func testCheckInPartialSubmitOmitsDefaultScoresAndLocalOnlyRawNote() async throws {
        let api = MockCheckInAPIClient()
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .localOnly })
        viewModel.alcohol = true
        viewModel.privateLifestyleIndicator = true
        viewModel.note = "private sensitive note"

        await viewModel.submit(includeDefaults: false)

        let request = try XCTUnwrap(api.submittedRequests.single)
        XCTAssertNil(request.energyScore)
        XCTAssertNil(request.moodScore)
        XCTAssertTrue(request.flags.alcohol)
        XCTAssertEqual(request.structuredNotes["private_lifestyle_indicator"], .bool(true))
        XCTAssertNil(request.freeTextNote)
        XCTAssertEqual(request.sensitiveNotePolicy, .excludeFromExternalLLM)
    }

    @MainActor
    func testCheckInSubmitWithoutNoteUsesExcludePolicy() async throws {
        let api = MockCheckInAPIClient()
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .hybrid })

        await viewModel.submit()

        let request = try XCTUnwrap(api.submittedRequests.single)
        XCTAssertNil(request.freeTextNote)
        XCTAssertEqual(request.sensitiveNotePolicy, .excludeFromExternalLLM)
    }

    @MainActor
    func testCheckInEditAndDeleteExisting() async throws {
        let api = MockCheckInAPIClient()
        let id = UUID()
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .hybrid })
        viewModel.loadExisting(
            id: id,
            from: DailyCheckInRequest(date: "2026-07-03", energyScore: 4)
        )
        viewModel.energy = 6

        await viewModel.updateExisting()
        await viewModel.deleteExisting()

        XCTAssertEqual(api.updatedRequests.single?.id, id)
        XCTAssertEqual(api.updatedRequests.single?.request.energyScore, 6)
        XCTAssertEqual(api.deletedIDs, [id])
        XCTAssertNil(viewModel.existingCheckInID)
    }

    @MainActor
    func testCheckInLoadsExistingFromAPIForEditAndDelete() async throws {
        let id = UUID()
        let api = MockCheckInAPIClient(
            fetchResult: DailyCheckInDetailResponse(
                checkinID: id,
                request: DailyCheckInRequest(
                    date: "2026-07-03",
                    energyScore: 4,
                    moodScore: 6,
                    flags: DailyCheckInFlags(travel: true),
                    structuredNotes: ["private_lifestyle_indicator": .bool(true)]
                ),
                hasFreeTextNote: true
            )
        )
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .hybrid })

        await viewModel.loadExistingForSelectedDate()

        XCTAssertEqual(api.fetchedDates.count, 1)
        XCTAssertEqual(viewModel.existingCheckInID, id)
        XCTAssertEqual(viewModel.energy, 4)
        XCTAssertEqual(viewModel.mood, 6)
        XCTAssertTrue(viewModel.travel)
        XCTAssertTrue(viewModel.privateLifestyleIndicator)
        XCTAssertTrue(viewModel.hasHiddenSavedNote)
    }

    @MainActor
    func testCheckInClearHiddenSavedNoteEncodesExplicitNull() throws {
        let api = MockCheckInAPIClient()
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .hybrid })
        viewModel.loadExisting(
            id: UUID(),
            from: DailyCheckInRequest(
                date: "2026-07-03",
                energyScore: 4,
                sensitiveNotePolicy: .summarizeBeforeExternalLLM
            ),
            hasHiddenNote: true
        )

        viewModel.clearSavedNote()
        let request = viewModel.buildRequest(includeDefaults: false)
        let payload = try encodedJSONObject(request)

        XCTAssertFalse(viewModel.hasHiddenSavedNote)
        XCTAssertTrue(payload["free_text_note"] is NSNull)
        XCTAssertEqual(payload["sensitive_note_policy"] as? String, "exclude_from_external_llm")
    }

    @MainActor
    func testCheckInSaveCurrentUpdatesLoadedCheckInInsteadOfCreatingDuplicate() async throws {
        let api = MockCheckInAPIClient()
        let id = UUID()
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .hybrid })
        viewModel.loadExisting(
            id: id,
            from: DailyCheckInRequest(
                date: "2026-07-03",
                energyScore: 4,
                moodScore: 5,
                flags: DailyCheckInFlags(travel: true),
                structuredNotes: ["private_lifestyle_indicator": .bool(true)]
            )
        )
        viewModel.energy = 7

        await viewModel.saveCurrent(includeDefaults: false)

        XCTAssertTrue(api.submittedRequests.isEmpty)
        XCTAssertEqual(api.updatedRequests.single?.id, id)
        let request = try XCTUnwrap(api.updatedRequests.single?.request)
        XCTAssertEqual(request.energyScore, 7)
        XCTAssertNil(request.moodScore)
        let payload = try encodedJSONObject(request)
        XCTAssertEqual(payload["energy_score"] as? Int, 7)
        XCTAssertNil(payload["mood_score"])
        XCTAssertNil(payload["flags"])
        XCTAssertNil(payload["structured_notes"])
    }

    @MainActor
    func testGoalListCreateAndPause() async throws {
        let existing = GoalResponse(
            id: UUID(),
            category: .sleep,
            priority: 2,
            timeHorizon: .shortTerm,
            successMetric: "7h average",
            active: true
        )
        let created = GoalResponse(
            id: UUID(),
            category: .strength,
            priority: 5,
            timeHorizon: .longTerm,
            successMetric: "deadlift consistency",
            constraints: ["notes": "no max attempts"],
            active: true
        )
        let paused = GoalResponse(
            id: existing.id,
            category: .sleep,
            priority: 2,
            timeHorizon: .shortTerm,
            successMetric: "7h average",
            active: false
        )
        let api = MockGoalsAPIClient(listResult: [existing], createResult: created, pauseResult: paused)
        let viewModel = GoalsViewModel(apiClient: api)

        await viewModel.loadGoals()
        viewModel.selectedCategory = .strength
        viewModel.priority = 5
        viewModel.selectedHorizon = .longTerm
        viewModel.successIndicator = "deadlift consistency"
        viewModel.constraints = "no max attempts"
        await viewModel.createGoal()
        await viewModel.pauseGoal(id: existing.id)

        XCTAssertEqual(viewModel.goals.count, 2)
        XCTAssertEqual(api.createdRequests.single?.category, .strength)
        XCTAssertEqual(api.createdRequests.single?.priority, 5)
        XCTAssertEqual(api.createdRequests.single?.timeHorizon, .longTerm)
        XCTAssertEqual(api.createdRequests.single?.successMetric, "deadlift consistency")
        XCTAssertEqual(api.createdRequests.single?.constraints, ["notes": "no max attempts"])
        XCTAssertEqual(api.pausedIDs, [existing.id])
        XCTAssertEqual(viewModel.goals.first(where: { $0.id == existing.id })?.active, false)
    }

    @MainActor
    func testGoalCreateFailureRollsBackOptimisticGoal() async {
        let api = MockGoalsAPIClient(createError: TestError.failed)
        let viewModel = GoalsViewModel(apiClient: api)
        viewModel.successIndicator = "sleep consistency"

        await viewModel.createGoal()

        XCTAssertTrue(viewModel.goals.isEmpty)
        XCTAssertEqual(viewModel.errorMessage, "Goal could not be saved. Try again.")
    }

    @MainActor
    func testGoalCreateSurvivesReloadDuringOptimisticAwait() async {
        let existing = GoalResponse(
            id: UUID(),
            category: .sleep,
            priority: 2,
            timeHorizon: .shortTerm,
            successMetric: "7h average",
            active: true
        )
        let created = GoalResponse(
            id: UUID(),
            category: .recovery,
            priority: 4,
            timeHorizon: .mediumTerm,
            successMetric: "HRV stability",
            active: true
        )
        let api = MockGoalsAPIClient(listResult: [existing], createResult: created)
        let viewModel = GoalsViewModel(apiClient: api)
        api.beforeCreateReturn = {
            await viewModel.loadGoals()
        }
        viewModel.successIndicator = "HRV stability"

        await viewModel.createGoal()

        XCTAssertEqual(viewModel.goals.first?.id, created.id)
        XCTAssertTrue(viewModel.goals.contains(where: { $0.id == existing.id }))
    }

    @MainActor
    func testBriefingGeneratePollsAndFetchesBriefing() async throws {
        let briefing = sampleBriefing()
        let api = MockBriefingAPIClient(
            generateResult: DailyAnalysisResponse(
                analysisJobID: UUID(),
                status: "queued",
                estimatedCompletionSeconds: 1
            ),
            jobResults: [
                DailyAnalysisResponse(
                    analysisJobID: UUID(),
                    status: "running",
                    estimatedCompletionSeconds: 1
                ),
                DailyAnalysisResponse(
                    analysisJobID: UUID(),
                    status: "completed",
                    estimatedCompletionSeconds: 0
                ),
            ],
            briefingResults: [
                .failure(BaselineAPIError.unsuccessfulStatus(404)),
                .success(briefing),
            ],
            traceResult: sampleTrace()
        )
        let store = InMemoryBriefingStore()
        var syncCount = 0
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: store,
            privacyMode: { .hybrid },
            syncBeforeGenerate: { syncCount += 1 },
            sleep: { _ in },
            maxFetchAttempts: 3,
            dateProvider: { "2026-07-04" }
        )

        await viewModel.generateBriefing()

        XCTAssertEqual(syncCount, 1)
        XCTAssertEqual(api.generatedRequests.single?.date, "2026-07-04")
        XCTAssertEqual(api.generatedRequests.single?.privacyMode, .hybrid)
        XCTAssertEqual(api.jobRequestIDs.count, 2)
        XCTAssertEqual(api.fetchRequests.map(\.date), ["2026-07-04", "2026-07-04"])
        XCTAssertEqual(viewModel.briefing?.trace, sampleTrace())
        XCTAssertEqual(store.savedBriefing?.trace, sampleTrace())
        XCTAssertFalse(viewModel.isOfflineFallback)
        XCTAssertEqual(viewModel.statusMessage, "Briefing is fresh.")
    }

    @MainActor
    func testBriefingGenerateExposesLoadingStateDuringRequest() async {
        let api = MockBriefingAPIClient(traceResult: sampleTrace())
        let store = InMemoryBriefingStore()
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: store,
            privacyMode: { .hybrid },
            sleep: { _ in },
            dateProvider: { "2026-07-04" }
        )
        api.beforeGenerateReturn = {
            XCTAssertTrue(viewModel.isGenerating)
            XCTAssertEqual(viewModel.statusMessage, "Generating daily briefing...")
        }

        await viewModel.generateBriefing()

        XCTAssertFalse(viewModel.isGenerating)
    }

    @MainActor
    func testBriefingGenerateTimesOutWithoutFetchingBriefing() async {
        let cached = sampleBriefing(recommendation: "Cached briefing.")
        let api = MockBriefingAPIClient(
            generateResult: DailyAnalysisResponse(
                analysisJobID: UUID(),
                status: "queued",
                estimatedCompletionSeconds: 1
            ),
            jobResults: [
                DailyAnalysisResponse(
                    analysisJobID: UUID(),
                    status: "running",
                    estimatedCompletionSeconds: 1
                ),
            ],
            briefingResults: [.success(sampleBriefing(recommendation: "Should not appear."))],
            traceResult: sampleTrace()
        )
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: InMemoryBriefingStore(initialBriefing: cached),
            privacyMode: { .hybrid },
            sleep: { _ in },
            maxFetchAttempts: 2,
            dateProvider: { "2026-07-04" }
        )

        await viewModel.generateBriefing()

        XCTAssertEqual(api.generatedRequests.count, 1)
        XCTAssertEqual(api.jobRequestIDs.count, 1)
        XCTAssertTrue(api.fetchRequests.isEmpty)
        XCTAssertEqual(viewModel.briefing, cached)
        XCTAssertTrue(viewModel.isOfflineFallback)
        XCTAssertEqual(viewModel.statusMessage, "Analysis is still running. Showing latest saved briefing.")
    }

    @MainActor
    func testBriefingPollingUsesBackendEstimateAndTwoSecondInterval() async {
        let completed = sampleBriefing(recommendation: "Completed briefing.")
        let api = MockBriefingAPIClient(
            generateResult: DailyAnalysisResponse(
                analysisJobID: UUID(),
                status: "queued",
                estimatedCompletionSeconds: 30
            ),
            jobResults: [
                DailyAnalysisResponse(
                    analysisJobID: UUID(),
                    status: "running",
                    estimatedCompletionSeconds: 30
                ),
                DailyAnalysisResponse(
                    analysisJobID: UUID(),
                    status: "completed",
                    estimatedCompletionSeconds: 0
                ),
            ],
            briefingResults: [.success(completed)],
            traceResult: sampleTrace()
        )
        let sleepRecorder = SleepRecorder()
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: InMemoryBriefingStore(),
            privacyMode: { .hybrid },
            sleep: { sleepRecorder.append($0) },
            dateProvider: { "2026-07-04" }
        )

        await viewModel.generateBriefing()

        XCTAssertEqual(api.jobRequestIDs.count, 2)
        XCTAssertTrue(sleepRecorder.values.contains(2_000_000_000))
        XCTAssertFalse(viewModel.isOfflineFallback)
        XCTAssertEqual(viewModel.briefing?.recommendation.primary, completed.recommendation.primary)
    }

    @MainActor
    func testBriefingGenerateIgnoresDuplicateRequestWhileGenerating() async {
        let api = MockBriefingAPIClient(traceResult: sampleTrace())
        var viewModel: DailyBriefingViewModel!
        viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: InMemoryBriefingStore(),
            privacyMode: { .hybrid },
            syncBeforeGenerate: {
                await viewModel.generateBriefing()
            },
            sleep: { _ in },
            dateProvider: { "2026-07-04" }
        )

        await viewModel.generateBriefing()

        XCTAssertEqual(api.generatedRequests.count, 1)
        XCTAssertEqual(api.fetchRequests.count, 1)
        XCTAssertFalse(viewModel.isGenerating)
    }

    @MainActor
    func testBriefingGenerateClearsStaleFollowUpAnswer() async {
        let refreshed = sampleBriefing(recommendation: "Fresh briefing.")
        let api = MockBriefingAPIClient(
            briefingResults: [.success(refreshed)],
            traceResult: sampleTrace()
        )
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: InMemoryBriefingStore(initialBriefing: sampleBriefing()),
            privacyMode: { .hybrid },
            sleep: { _ in },
            dateProvider: { "2026-07-04" }
        )
        viewModel.followUpQuestion = "Why this recommendation?"
        await viewModel.askFollowUp()
        XCTAssertNotNil(viewModel.followUpAnswer)

        await viewModel.generateBriefing()

        XCTAssertEqual(viewModel.briefing?.recommendation.primary, "Fresh briefing.")
        XCTAssertNil(viewModel.followUpAnswer)
    }

    @MainActor
    func testBriefingGenerateShowsDeterministicDegradedState() async {
        let degraded = sampleBriefing(
            recommendation: "Deterministic fallback: keep training moderate."
        )
        let api = MockBriefingAPIClient(
            generateResult: DailyAnalysisResponse(
                analysisJobID: UUID(),
                status: "completed",
                estimatedCompletionSeconds: 0
            ),
            briefingResults: [.success(degraded)],
            traceResult: sampleTrace(status: "degraded", degradeReason: "llm_not_configured")
        )
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: InMemoryBriefingStore(),
            privacyMode: { .cloudAssisted },
            sleep: { _ in },
            dateProvider: { "2026-07-04" }
        )

        await viewModel.generateBriefing()

        XCTAssertEqual(viewModel.briefing?.recommendation.primary, degraded.recommendation.primary)
        XCTAssertEqual(viewModel.statusMessage, "Generated with deterministic fallback.")
    }

    @MainActor
    func testBriefingFollowUpRendersEvidenceBackedAnswer() async throws {
        let answer = AssistantQueryResponse(
            answer: "Sleep debt makes intervals less attractive today.",
            personalEvidence: [
                PersonalEvidence(
                    metric: "sleep_debt_hours",
                    value: .double(1.8),
                    interpretation: "Higher than baseline.",
                    source: "briefing_trace"
                ),
            ],
            externalSources: [
                ExternalCitation(
                    title: "Training load note",
                    source: "Baseline",
                    citedClaim: "Reduce intensity when recovery signals are mixed."
                ),
            ],
            confidence: "medium",
            uncertainty: ["No soreness check-in."],
            safetyStatus: "passed",
            traceID: UUID(uuidString: "00000000-0000-0000-0000-000000000002")!
        )
        let api = MockBriefingAPIClient(assistantResult: answer)
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: InMemoryBriefingStore(initialBriefing: sampleBriefing()),
            privacyMode: { .localOnly },
            dateProvider: { "2026-07-04" }
        )
        viewModel.followUpQuestion = "Why not intervals?"

        await viewModel.askFollowUp()

        XCTAssertEqual(api.assistantRequests.single?.question, "Why not intervals?")
        XCTAssertEqual(api.assistantRequests.single?.allowedDataScope, ["briefing_trace", "recent_health"])
        XCTAssertEqual(api.assistantRequests.single?.privacyMode, .localOnly)
        XCTAssertEqual(viewModel.followUpAnswer, answer)
        XCTAssertEqual(viewModel.followUpQuestion, "")
        XCTAssertEqual(viewModel.statusMessage, "Follow-up answered from trace-backed evidence.")
    }

    @MainActor
    func testFollowUpSnapshotSeparatesEvidenceCitationsAndUncertainty() {
        let answer = AssistantQueryResponse(
            answer: "Keep it moderate.",
            personalEvidence: [
                PersonalEvidence(
                    metric: "hrv_deviation_pct",
                    value: .double(-8),
                    interpretation: "Below recent baseline.",
                    source: "briefing_trace"
                ),
            ],
            externalSources: [
                ExternalCitation(
                    title: "Safety note",
                    source: "Baseline",
                    citedClaim: "Avoid medical certainty."
                ),
            ],
            confidence: "medium",
            uncertainty: ["Check-in is missing."],
            safetyStatus: "passed",
            traceID: UUID(uuidString: "00000000-0000-0000-0000-000000000002")!
        )
        let snapshot = renderedStrings(in: AssistantAnswerView(answer: answer).body)

        XCTAssertTrue(snapshot.contains("Personal evidence"))
        XCTAssertTrue(snapshot.contains("External citations"))
        XCTAssertTrue(snapshot.contains("Uncertainty"))
        XCTAssertTrue(snapshot.contains("briefing_trace"))
        XCTAssertTrue(snapshot.contains("Safety note"))
    }

    @MainActor
    func testBriefingOfflineFallbackShowsLastSavedBriefing() async {
        let cached = sampleBriefing(recommendation: "Cached briefing.")
        let api = MockBriefingAPIClient(generateError: TestError.failed)
        let viewModel = DailyBriefingViewModel(
            apiClient: api,
            briefingStore: InMemoryBriefingStore(initialBriefing: cached),
            privacyMode: { .hybrid },
            dateProvider: { "2026-07-04" }
        )

        await viewModel.generateBriefing()

        XCTAssertEqual(viewModel.briefing, cached)
        XCTAssertTrue(viewModel.isOfflineFallback)
        XCTAssertTrue(viewModel.statusMessage.contains("Showing latest saved briefing"))
        XCTAssertNil(viewModel.errorMessage)
    }

    @MainActor
    func testBriefingSnapshotKeepsSafetyNoteAndFreshnessVisible() {
        let viewModel = DailyBriefingViewModel(
            apiClient: MockBriefingAPIClient(),
            briefingStore: InMemoryBriefingStore(initialBriefing: sampleBriefing()),
            privacyMode: { .hybrid },
            dateProvider: { "2026-07-04" }
        )
        let view = DailyBriefingView(viewModel: viewModel)
        if #available(iOS 16.0, macOS 13.0, *) {
            let renderer = ImageRenderer(content: view.frame(width: 390, height: 844))
            XCTAssertNotNil(renderer.cgImage)
        }
        let snapshot = renderedStrings(in: view.body)

        XCTAssertTrue(snapshot.contains("Freshness"))
        XCTAssertTrue(snapshot.contains("Safety note"))
        XCTAssertTrue(snapshot.contains("This is wellness decision support, not medical advice."))
        XCTAssertTrue(snapshot.contains { $0.contains("Latest sample") })
        XCTAssertTrue(snapshot.contains { $0.contains("Generated:") })
    }

    @MainActor
    func testTraceViewRendersInspectionSectionsFromTracePayload() {
        let view = TraceInspectionView(briefing: sampleBriefing(), trace: sampleTrace())
        let snapshot = renderedStrings(in: view.body)

        XCTAssertTrue(snapshot.contains("Data freshness"))
        XCTAssertTrue(snapshot.contains("Feature values"))
        XCTAssertTrue(snapshot.contains("Rules fired"))
        XCTAssertTrue(snapshot.contains("Retrieved memory"))
        XCTAssertTrue(snapshot.contains("External sources"))
        XCTAssertTrue(snapshot.contains("Model metadata"))
        XCTAssertTrue(snapshot.contains("sleep_debt_rule: sleep_debt_hours=1.5"))
        XCTAssertTrue(snapshot.contains("Briefing generation status"))
        XCTAssertFalse(snapshot.contains("Trace details are unavailable for this briefing."))
    }

    @MainActor
    func testTraceViewGracefullyShowsMissingTrace() {
        let view = TraceInspectionView(briefing: sampleBriefing(), trace: nil)
        let snapshot = renderedStrings(in: view.body)

        XCTAssertTrue(snapshot.contains("Trace availability"))
        XCTAssertTrue(snapshot.contains("Trace details are unavailable for this briefing."))
        XCTAssertTrue(snapshot.contains("No feature values were exposed for this trace."))
    }

    @MainActor
    func testCheckInUIKeepsFastSubmitUnderOneMinuteBar() {
        let api = MockCheckInAPIClient()
        let viewModel = DailyCheckInViewModel(apiClient: api, privacyMode: { .hybrid })
        viewModel.loadExisting(
            id: UUID(),
            from: DailyCheckInRequest(date: "2026-07-03", energyScore: 5)
        )
        let view = DailyCheckInView(viewModel: viewModel, privacyMode: { .hybrid })
        if #available(iOS 16.0, macOS 13.0, *) {
            let renderer = ImageRenderer(content: view.frame(width: 390, height: 844))
            XCTAssertNotNil(renderer.cgImage)
        }
        let snapshot = renderedStrings(in: view.body)

        XCTAssertEqual(
            DailyCheckInLayoutSnapshot.visibleControlLabels
                .filter { snapshot.contains($0) },
            [
                "Energy",
                "Mood",
                "Soreness",
                "Stress",
                "Recovery",
                "Food quality",
                "Alcohol",
                "Caffeine",
                "Illness",
                "Injury",
                "Travel",
                "High-level lifestyle indicator",
                "Optional note",
                "Submit check-in",
                "Fast submit changed fields only",
            ]
        )
        XCTAssertTrue(snapshot.contains(DailyCheckInLayoutSnapshot.reloadSavedLabel))
        XCTAssertTrue(snapshot.contains(DailyCheckInLayoutSnapshot.updateSavedLabel))
        XCTAssertTrue(snapshot.contains(DailyCheckInLayoutSnapshot.deleteSavedLabel))
        XCTAssertEqual(DailyCheckInLayoutSnapshot.requiredFastSubmitFields, 0)
        XCTAssertLessThanOrEqual(
            DailyCheckInLayoutSnapshot.visibleControlLabels.count,
            DailyCheckInLayoutSnapshot.oneMinuteInteractionBudget
        )
    }
}

private enum TestError: Error {
    case failed
}

private final class MockCheckInAPIClient: CheckInAPIClient, @unchecked Sendable {
    let response = DailyCheckInResponse(
        checkinID: UUID(),
        acceptedFields: ["date"],
        redactionStatus: .none
    )
    private let fetchResult: DailyCheckInDetailResponse?
    private let fetchError: Error?
    private(set) var fetchedDates: [String] = []
    private(set) var submittedRequests: [DailyCheckInRequest] = []
    private(set) var updatedRequests: [(id: UUID, request: DailyCheckInRequest)] = []
    private(set) var deletedIDs: [UUID] = []

    init(
        fetchResult: DailyCheckInDetailResponse? = nil,
        fetchError: Error? = BaselineAPIError.unsuccessfulStatus(404)
    ) {
        self.fetchResult = fetchResult
        self.fetchError = fetchError
    }

    func fetchDailyCheckIn(date: String) async throws -> DailyCheckInDetailResponse {
        fetchedDates.append(date)
        if let fetchResult {
            return fetchResult
        }
        if let fetchError {
            throw fetchError
        }
        throw BaselineAPIError.missingDataEnvelope
    }

    func submitDailyCheckIn(_ request: DailyCheckInRequest) async throws -> DailyCheckInResponse {
        submittedRequests.append(request)
        return response
    }

    func updateDailyCheckIn(
        id: UUID,
        request: DailyCheckInRequest
    ) async throws -> DailyCheckInResponse {
        updatedRequests.append((id, request))
        return DailyCheckInResponse(
            checkinID: id,
            acceptedFields: response.acceptedFields,
            redactionStatus: response.redactionStatus,
            analysisJobID: response.analysisJobID
        )
    }

    func deleteDailyCheckIn(id: UUID) async throws {
        deletedIDs.append(id)
    }
}

private final class MockGoalsAPIClient: GoalsAPIClient, @unchecked Sendable {
    private let listResult: [GoalResponse]
    private let createResult: GoalResponse
    private let pauseResult: GoalResponse
    private let createError: Error?
    var beforeCreateReturn: (@MainActor () async -> Void)?
    private(set) var createdRequests: [GoalRequest] = []
    private(set) var pausedIDs: [UUID] = []

    init(
        listResult: [GoalResponse] = [],
        createResult: GoalResponse = GoalResponse(
            id: UUID(),
            category: .sleep,
            priority: 3,
            timeHorizon: .mediumTerm,
            successMetric: "sleep consistency"
        ),
        pauseResult: GoalResponse = GoalResponse(
            id: UUID(),
            category: .sleep,
            priority: 3,
            timeHorizon: .mediumTerm,
            successMetric: "sleep consistency",
            active: false
        ),
        createError: Error? = nil
    ) {
        self.listResult = listResult
        self.createResult = createResult
        self.pauseResult = pauseResult
        self.createError = createError
    }

    func listGoals() async throws -> [GoalResponse] {
        listResult
    }

    func createGoal(_ request: GoalRequest) async throws -> GoalResponse {
        createdRequests.append(request)
        if let beforeCreateReturn {
            await beforeCreateReturn()
        }
        if let createError {
            throw createError
        }
        return createResult
    }

    func pauseGoal(id: UUID) async throws -> GoalResponse {
        pausedIDs.append(id)
        return pauseResult
    }
}

private final class MockBriefingAPIClient: DailyBriefingAPIClient, @unchecked Sendable {
    private let generateResult: DailyAnalysisResponse
    private let generateError: Error?
    private var jobResults: [DailyAnalysisResponse]
    private var briefingResults: [Result<DailyBriefingResponse, Error>]
    private let traceResult: BriefingTraceInspection?
    private let traceError: Error?
    private let assistantResult: AssistantQueryResponse
    private let assistantError: Error?
    var beforeGenerateReturn: (@MainActor () async -> Void)?
    private(set) var generatedRequests: [DailyAnalysisRequest] = []
    private(set) var jobRequestIDs: [UUID] = []
    private(set) var fetchRequests: [(date: String, offlineLast: Bool)] = []
    private(set) var traceRequestIDs: [UUID] = []
    private(set) var assistantRequests: [AssistantQueryRequest] = []

    init(
        generateResult: DailyAnalysisResponse = DailyAnalysisResponse(
            analysisJobID: UUID(),
            status: "completed",
            estimatedCompletionSeconds: 0
        ),
        generateError: Error? = nil,
        jobResults: [DailyAnalysisResponse] = [],
        briefingResults: [Result<DailyBriefingResponse, Error>] = [.success(sampleBriefing())],
        traceResult: BriefingTraceInspection? = nil,
        traceError: Error? = nil,
        assistantResult: AssistantQueryResponse = AssistantQueryResponse(
            answer: "Use the briefing trace for context.",
            personalEvidence: [
                PersonalEvidence(
                    metric: "sleep_debt_hours",
                    value: .double(1.2),
                    interpretation: "Slightly elevated.",
                    source: "briefing_trace"
                ),
            ],
            confidence: "medium",
            uncertainty: ["No extra evidence requested."],
            safetyStatus: "passed",
            traceID: UUID(uuidString: "00000000-0000-0000-0000-000000000003")!
        ),
        assistantError: Error? = nil
    ) {
        self.generateResult = generateResult
        self.generateError = generateError
        self.jobResults = jobResults
        self.briefingResults = briefingResults
        self.traceResult = traceResult
        self.traceError = traceError
        self.assistantResult = assistantResult
        self.assistantError = assistantError
    }

    func generateDailyAnalysis(_ request: DailyAnalysisRequest) async throws -> DailyAnalysisResponse {
        generatedRequests.append(request)
        if let beforeGenerateReturn {
            await beforeGenerateReturn()
        }
        if let generateError {
            throw generateError
        }
        return generateResult
    }

    func fetchDailyAnalysisJob(id: UUID) async throws -> DailyAnalysisResponse {
        jobRequestIDs.append(id)
        guard !jobResults.isEmpty else {
            return generateResult
        }
        return jobResults.removeFirst()
    }

    func fetchDailyBriefing(date: String, offlineLast: Bool) async throws -> DailyBriefingResponse {
        fetchRequests.append((date, offlineLast))
        return try briefingResults.removeFirst().get()
    }

    func fetchBriefingTrace(traceID: UUID) async throws -> BriefingTraceInspection {
        traceRequestIDs.append(traceID)
        if let traceError {
            throw traceError
        }
        if let traceResult {
            return traceResult
        }
        throw BaselineAPIError.unsuccessfulStatus(404)
    }

    func submitAssistantQuery(_ request: AssistantQueryRequest) async throws -> AssistantQueryResponse {
        assistantRequests.append(request)
        if let assistantError {
            throw assistantError
        }
        return assistantResult
    }
}

private final class InMemoryBriefingStore: BriefingPersisting, @unchecked Sendable {
    private(set) var savedBriefing: DailyBriefingResponse?

    init(initialBriefing: DailyBriefingResponse? = nil) {
        savedBriefing = initialBriefing
    }

    func loadLatestBriefing() throws -> DailyBriefingResponse? {
        savedBriefing
    }

    func saveLatestBriefing(_ briefing: DailyBriefingResponse) throws {
        savedBriefing = briefing
    }
}

private final class SleepRecorder: @unchecked Sendable {
    private(set) var values: [UInt64] = []

    func append(_ value: UInt64) {
        values.append(value)
    }
}

private final class MockAuthorizationClient: HealthAuthorizationClient, @unchecked Sendable {
    private let granted: Set<HealthCategory>

    init(granted: Set<HealthCategory>) {
        self.granted = granted
    }

    func requestAuthorization(for categories: [HealthCategory]) async throws -> Set<HealthCategory> {
        granted.intersection(categories)
    }
}

private final class MockOnboardingAPIClient: HealthSyncAPIClient, @unchecked Sendable {
    private let serverConsentVersion: String
    private let consentError: Error?
    private(set) var consentRequests: [ConsentRecordRequest] = []
    private(set) var syncRequests: [HealthSyncRequest] = []

    init(serverConsentVersion: String = "server-consent-v1", consentError: Error? = nil) {
        self.serverConsentVersion = serverConsentVersion
        self.consentError = consentError
    }

    func recordConsent(_ request: ConsentRecordRequest) async throws -> DataControlConsentResponse {
        consentRequests.append(request)
        if let consentError {
            throw consentError
        }
        return DataControlConsentResponse(
            schemaVersion: "v1",
            id: UUID(),
            userID: UUID(),
            consentVersion: serverConsentVersion,
            healthCategoriesEnabled: request.healthCategoriesEnabled,
            cloudProcessingEnabled: request.cloudProcessingEnabled,
            externalLLMEnabled: request.externalLLMEnabled,
            rawNoteProcessingEnabled: request.rawNoteProcessingEnabled,
            timestamp: "2026-07-04T08:00:00Z",
            revokedAt: nil
        )
    }

    func postHealthSync(_ request: HealthSyncRequest) async throws -> HealthSyncResponse {
        syncRequests.append(request)
        return HealthSyncResponse(
            syncID: UUID(),
            acceptedCount: request.samples.count,
            duplicateCount: 0,
            rejectedCount: 0,
            warnings: [],
            nextAnchor: "anchor"
        )
    }
}

private final class InMemoryAnchorStore: AnchorPersisting, @unchecked Sendable {
    func loadAnchor(for category: HealthCategory) throws -> CategoryAnchor? {
        nil
    }

    func saveAnchor(_ anchor: CategoryAnchor, for category: HealthCategory) throws {}

    func loadPendingBatch() throws -> PendingSyncBatch? {
        nil
    }

    func savePendingBatch(_ batch: PendingSyncBatch) throws {}

    func clearPendingBatch() throws {}
}

private final class InMemoryConsentStore: ConsentPersisting, @unchecked Sendable {
    private(set) var consent: ConsentRecord?

    func loadConsent() throws -> ConsentRecord? {
        consent
    }

    func saveConsent(_ consent: ConsentRecord) throws {
        self.consent = consent
    }
}

private func sampleBriefing(
    recommendation: String = "Keep training moderate."
) -> DailyBriefingResponse {
    DailyBriefingResponse(
        date: "2026-07-04",
        readinessState: "mixed",
        confidence: "medium",
        dataFreshness: DataFreshness(
            latestSampleAt: "2026-07-04T06:30:00Z",
            latestCheckInDate: "2026-07-04",
            staleSources: []
        ),
        evidence: [
            PersonalEvidence(
                metric: "sleep_debt_hours",
                value: .double(1.5),
                interpretation: "Slight sleep debt.",
                source: "features.sleep"
            ),
        ],
        memoryObservations: [
            MemoryObservation(
                observation: "Moderate days after short sleep were rated useful.",
                relevance: "Supports conservative training."
            ),
        ],
        externalCitations: [
            ExternalCitation(
                title: "Baseline safety policy",
                source: "Baseline",
                citedClaim: "Briefings should avoid medical certainty."
            ),
        ],
        riskFlags: ["sleep_debt"],
        recommendation: RecommendationSummary(primary: recommendation),
        recommendationBand: "moderate_or_upper_body",
        candidateOptions: [
            CandidateOption(
                label: "Upper body strength",
                recommendationBand: "moderate_or_upper_body",
                rationale: "Keeps work productive without lower-body load."
            ),
        ],
        goalTradeoffs: [
            GoalTradeoff(goal: "VO2 max", tradeoff: "Delay intervals one day."),
        ],
        uncertainty: ["No soreness check-in yet."],
        dataQualityNotes: [
            DataQualityNote(metric: "hrv", note: "HRV sample is recent.", severity: "info"),
        ],
        whatWouldChangeMyMind: ["A low soreness check-in and normal HRV would support intensity."],
        alternatives: [
            RecommendationAlternative(label: "Rest", rationale: "Valid if subjective fatigue is high."),
        ],
        followUp: FollowUpPrompt(
            question: "Why not intervals today?",
            reason: "Explains the main tradeoff."
        ),
        safetyStatus: "passed",
        safetyNotes: ["This is wellness decision support, not medical advice."],
        traceID: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
        generatedAt: "2026-07-04T06:40:00Z"
    )
}

private func sampleTrace(
    status: String = "success",
    degradeReason: String? = nil
) -> BriefingTraceInspection {
    var metadata = [
        "assessment_version": "test-v1",
        "briefing_generation_status": status,
        "input_hash": "input-hash",
        "model_run_ids": "[00000000-0000-0000-0000-000000000099]",
    ]
    if let degradeReason {
        metadata["degrade_reason"] = degradeReason
    }
    return BriefingTraceInspection(
        traceID: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
        dataFreshness: DataFreshness(
            latestSampleAt: "2026-07-04T06:30:00Z",
            latestCheckInDate: "2026-07-04",
            staleSources: []
        ),
        featureValues: [
            PersonalEvidence(
                metric: "sleep_debt_hours",
                value: .double(1.5),
                interpretation: "Slight sleep debt.",
                source: "sleep_features.values.sleep_debt_hours"
            ),
        ],
        rulesFired: ["sleep_debt_rule: sleep_debt_hours=1.5"],
        retrievedMemory: [
            MemoryObservation(
                observation: "Prior moderate day was useful.",
                relevance: "Recent prior briefing context."
            ),
        ],
        externalSources: [
            ExternalCitation(
                title: "Baseline safety policy",
                source: "Baseline",
                citedClaim: "Avoid medical certainty."
            ),
        ],
        modelMetadata: metadata
    )
}

private extension Array {
    var single: Element? {
        count == 1 ? self[0] : nil
    }
}

private func renderedStrings(in value: Any) -> [String] {
    var strings: [String] = []
    collectStrings(from: value, into: &strings, depth: 0)
    return strings
}

private func encodedJSONObject(_ value: some Encodable) throws -> [String: Any] {
    let data = try JSONEncoder().encode(value)
    return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
}

private func collectStrings(from value: Any, into strings: inout [String], depth: Int) {
    guard depth < 30 else {
        return
    }
    if let string = value as? String {
        strings.append(string)
        return
    }
    let mirror = Mirror(reflecting: value)
    for child in mirror.children {
        collectStrings(from: child.value, into: &strings, depth: depth + 1)
    }
}

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
            ],
            infoDictionary: [
                BaselineAppConfiguration.infoPlistKey: "https://bundle.example.test",
            ]
        )

        XCTAssertEqual(configuration.apiBaseURL.absoluteString, "https://api.example.test")
    }

    func testAPIBaseURLFallsBackToInfoPlist() throws {
        let configuration = try BaselineAppConfiguration.current(
            environment: [:],
            infoDictionary: [
                BaselineAppConfiguration.infoPlistKey: "https://bundle.example.test",
            ]
        )

        XCTAssertEqual(configuration.apiBaseURL.absoluteString, "https://bundle.example.test")
    }

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

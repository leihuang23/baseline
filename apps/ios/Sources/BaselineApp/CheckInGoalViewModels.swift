import BaselineCore
import Combine
import Foundation

enum DailyCheckInLayoutSnapshot {
    static let scoreLabels = [
        "Energy",
        "Mood",
        "Soreness",
        "Stress",
        "Recovery",
        "Food quality",
    ]
    static let optionalContextLabels = [
        "Alcohol",
        "Caffeine",
        "Illness",
        "Injury",
        "Travel",
    ]
    static let privateIndicatorLabel = "High-level lifestyle indicator"
    static let noteLabel = "Optional note"
    static let primarySubmitLabel = "Submit check-in"
    static let fastSubmitLabel = "Fast submit changed fields only"
    static let reloadSavedLabel = "Reload saved check-in"
    static let updateSavedLabel = "Update saved check-in"
    static let deleteSavedLabel = "Delete saved check-in"
    static let visibleControlLabels = scoreLabels
        + optionalContextLabels
        + [
            privateIndicatorLabel,
            noteLabel,
            primarySubmitLabel,
            fastSubmitLabel,
        ]
    static let oneMinuteInteractionBudget = 15
    static let requiredFastSubmitFields = 0
}

@MainActor
final class DailyCheckInViewModel: ObservableObject {
    static let requiredFieldCountForFastSubmit = 0

    @Published var date = Date()
    @Published var energy = 5.0
    @Published var mood = 5.0
    @Published var soreness = 5.0
    @Published var stress = 5.0
    @Published var perceivedRecovery = 5.0
    @Published var foodQuality = 5.0
    @Published var alcohol = false
    @Published var caffeine = false
    @Published var illness = false
    @Published var injury = false
    @Published var travel = false
    @Published var privateLifestyleIndicator = false
    @Published var note = ""
    @Published private(set) var existingCheckInID: UUID?
    @Published private(set) var hasHiddenSavedNote = false
    @Published private(set) var statusMessage = "Ready"
    @Published private(set) var isSaving = false
    @Published var errorMessage: String?

    private let apiClient: any CheckInAPIClient
    private let privacyMode: () -> PrivacyMode
    private let dateFormatter: DateFormatter
    private var savedRequest: DailyCheckInRequest?
    private var shouldClearSavedNote = false

    init(
        apiClient: any CheckInAPIClient,
        privacyMode: @escaping () -> PrivacyMode,
        calendar: Calendar = .current
    ) {
        self.apiClient = apiClient
        self.privacyMode = privacyMode
        dateFormatter = DateFormatter()
        dateFormatter.calendar = calendar
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")
        dateFormatter.dateFormat = "yyyy-MM-dd"
    }

    func submit(includeDefaults: Bool = true) async {
        await save(request: buildRequest(includeDefaults: includeDefaults), mode: .create)
    }

    func saveCurrent(includeDefaults: Bool = true) async {
        guard existingCheckInID == nil else {
            await updateExisting(includeDefaults: includeDefaults)
            return
        }
        await submit(includeDefaults: includeDefaults)
    }

    func loadExistingForSelectedDate() async {
        let dateString = dateFormatter.string(from: date)
        errorMessage = nil
        statusMessage = "Checking for saved check-in..."
        do {
            let detail = try await apiClient.fetchDailyCheckIn(date: dateString)
            loadExisting(
                id: detail.checkinID,
                from: detail.request,
                hasHiddenNote: detail.hasFreeTextNote
            )
            statusMessage = "Saved check-in loaded."
        } catch BaselineAPIError.unsuccessfulStatus(404) {
            existingCheckInID = nil
            savedRequest = nil
            hasHiddenSavedNote = false
            shouldClearSavedNote = false
            statusMessage = "No saved check-in for today."
        } catch {
            errorMessage = "Saved check-in could not be loaded. Try again."
            statusMessage = "Load failed."
        }
    }

    func updateExisting(includeDefaults: Bool = true) async {
        guard let existingCheckInID else {
            errorMessage = "No check-in is loaded for editing."
            return
        }
        await save(request: buildRequest(includeDefaults: includeDefaults), mode: .update(existingCheckInID))
    }

    func deleteExisting() async {
        guard let existingCheckInID else {
            errorMessage = "No check-in is loaded for deletion."
            return
        }
        let previousID = self.existingCheckInID
        let previousHiddenNote = hasHiddenSavedNote
        let previousClearSavedNote = shouldClearSavedNote
        self.existingCheckInID = nil
        errorMessage = nil
        statusMessage = "Deleting check-in..."
        do {
            try await apiClient.deleteDailyCheckIn(id: existingCheckInID)
            savedRequest = nil
            hasHiddenSavedNote = false
            shouldClearSavedNote = false
            statusMessage = "Check-in deleted."
        } catch {
            self.existingCheckInID = previousID
            hasHiddenSavedNote = previousHiddenNote
            shouldClearSavedNote = previousClearSavedNote
            errorMessage = "Check-in could not be deleted. Try again."
            statusMessage = "Delete failed."
        }
    }

    func loadExisting(
        id: UUID,
        from request: DailyCheckInRequest,
        hasHiddenNote: Bool = false
    ) {
        existingCheckInID = id
        savedRequest = request
        hasHiddenSavedNote = hasHiddenNote && request.freeTextNote == nil
        shouldClearSavedNote = false
        date = dateFormatter.date(from: request.date) ?? date
        energy = Double(request.energyScore ?? 5)
        mood = Double(request.moodScore ?? 5)
        soreness = Double(request.sorenessScore ?? 5)
        stress = Double(request.stressScore ?? 5)
        perceivedRecovery = Double(request.perceivedRecoveryScore ?? 5)
        foodQuality = Double(request.foodQualityScore ?? 5)
        alcohol = request.flags.alcohol
        caffeine = request.flags.caffeineNotes != nil
        illness = request.flags.illness
        injury = request.flags.injury
        travel = request.flags.travel
        privateLifestyleIndicator = request.structuredNotes["private_lifestyle_indicator"] == .bool(true)
        note = request.freeTextNote ?? ""
    }

    func clearSavedNote() {
        note = ""
        hasHiddenSavedNote = false
        shouldClearSavedNote = true
    }

    func buildRequest(includeDefaults: Bool) -> DailyCheckInRequest {
        let trimmedNote = note.trimmingCharacters(in: .whitespacesAndNewlines)
        let allowsRawNote = privacyMode() != .localOnly
        let noteForRequest = trimmedNote.isEmpty || !allowsRawNote ? nil : trimmedNote
        let policy: SensitiveNotePolicy
        if noteForRequest != nil {
            policy = .summarizeBeforeExternalLLM
        } else if allowsRawNote, hasHiddenSavedNote, !shouldClearSavedNote {
            policy = savedRequest?.sensitiveNotePolicy ?? .excludeFromExternalLLM
        } else {
            policy = .excludeFromExternalLLM
        }
        let structuredNotes = privateLifestyleIndicator
            ? ["private_lifestyle_indicator": StructuredNoteValue.bool(true)]
            : [:]
        let flags = DailyCheckInFlags(
            alcohol: alcohol,
            caffeineNotes: caffeine ? "caffeine_today" : nil,
            illness: illness,
            injury: injury,
            travel: travel
        )
        let baseline = savedRequest
        let baselineFlags = baseline?.flags ?? DailyCheckInFlags()
        let baselineStructuredNotes = baseline?.structuredNotes ?? [:]

        return DailyCheckInRequest(
            date: dateFormatter.string(from: date),
            energyScore: scoreForRequest(
                energy,
                baseline: baseline?.energyScore,
                includeDefaults: includeDefaults
            ),
            moodScore: scoreForRequest(
                mood,
                baseline: baseline?.moodScore,
                includeDefaults: includeDefaults
            ),
            sorenessScore: scoreForRequest(
                soreness,
                baseline: baseline?.sorenessScore,
                includeDefaults: includeDefaults
            ),
            stressScore: scoreForRequest(
                stress,
                baseline: baseline?.stressScore,
                includeDefaults: includeDefaults
            ),
            perceivedRecoveryScore: scoreForRequest(
                perceivedRecovery,
                baseline: baseline?.perceivedRecoveryScore,
                includeDefaults: includeDefaults
            ),
            foodQualityScore: scoreForRequest(
                foodQuality,
                baseline: baseline?.foodQualityScore,
                includeDefaults: includeDefaults
            ),
            flags: flags,
            structuredNotes: structuredNotes,
            freeTextNote: noteForRequest,
            sensitiveNotePolicy: policy,
            encodesFlags: includeDefaults || flags != baselineFlags,
            encodesStructuredNotes: includeDefaults || structuredNotes != baselineStructuredNotes,
            encodesFreeTextNote: shouldClearSavedNote && noteForRequest == nil
        )
    }

    private func scoreForRequest(
        _ value: Double,
        baseline: Int?,
        includeDefaults: Bool
    ) -> Int? {
        let score = roundedScore(value)
        guard !includeDefaults else {
            return score
        }
        return score == (baseline ?? 5) ? nil : score
    }

    private func save(request: DailyCheckInRequest, mode: SaveMode) async {
        isSaving = true
        errorMessage = nil
        statusMessage = "Saving check-in..."
        defer { isSaving = false }

        do {
            let response: DailyCheckInResponse
            switch mode {
            case .create:
                response = try await apiClient.submitDailyCheckIn(request)
            case .update(let id):
                response = try await apiClient.updateDailyCheckIn(id: id, request: request)
            }
            existingCheckInID = response.checkinID
            if request.encodesFreeTextNote || request.freeTextNote != nil {
                hasHiddenSavedNote = false
                shouldClearSavedNote = false
            }
            savedRequest = buildRequest(includeDefaults: true)
            statusMessage = "Check-in saved."
        } catch {
            errorMessage = "Check-in could not be saved. Try again."
            statusMessage = "Save failed."
        }
    }

    private func roundedScore(_ value: Double) -> Int {
        min(10, max(1, Int(value.rounded())))
    }

    private enum SaveMode {
        case create
        case update(UUID)
    }
}

@MainActor
final class GoalsViewModel: ObservableObject {
    @Published private(set) var goals: [GoalResponse] = []
    @Published var selectedCategory: GoalCategory = .cognitivePerformance
    @Published var priority = 3
    @Published var selectedHorizon: GoalTimeHorizon = .mediumTerm
    @Published var successIndicator = ""
    @Published var constraints = ""
    @Published private(set) var statusMessage = "Ready"
    @Published private(set) var isSaving = false
    @Published var errorMessage: String?

    private let apiClient: any GoalsAPIClient

    init(apiClient: any GoalsAPIClient) {
        self.apiClient = apiClient
    }

    func loadGoals() async {
        errorMessage = nil
        do {
            goals = try await apiClient.listGoals()
            statusMessage = goals.isEmpty ? "No goals yet." : "Goals loaded."
        } catch {
            errorMessage = "Goals could not be loaded. Try again."
            statusMessage = "Load failed."
        }
    }

    func createGoal() async {
        let trimmedSuccess = successIndicator.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedSuccess.isEmpty else {
            errorMessage = "Add a success indicator before saving."
            return
        }

        isSaving = true
        errorMessage = nil
        statusMessage = "Saving goal..."
        let optimistic = GoalResponse(
            id: UUID(),
            category: selectedCategory,
            priority: priority,
            timeHorizon: selectedHorizon,
            successMetric: trimmedSuccess,
            constraints: constraintsDictionary,
            active: true
        )
        goals.insert(optimistic, at: 0)
        defer { isSaving = false }

        do {
            let saved = try await apiClient.createGoal(
                GoalRequest(
                    category: selectedCategory,
                    priority: priority,
                    timeHorizon: selectedHorizon,
                    successMetric: trimmedSuccess,
                    constraints: constraintsDictionary
                )
            )
            if let index = goals.firstIndex(where: { $0.id == optimistic.id }) {
                goals[index] = saved
            } else {
                goals.insert(saved, at: 0)
            }
            successIndicator = ""
            constraints = ""
            statusMessage = "Goal saved."
        } catch {
            goals.removeAll { $0.id == optimistic.id }
            errorMessage = "Goal could not be saved. Try again."
            statusMessage = "Save failed."
        }
    }

    func pauseGoal(id: UUID) async {
        guard let index = goals.firstIndex(where: { $0.id == id }) else {
            return
        }
        let original = goals[index]
        goals[index].active = false
        errorMessage = nil
        statusMessage = "Pausing goal..."

        do {
            goals[index] = try await apiClient.pauseGoal(id: id)
            statusMessage = "Goal paused."
        } catch {
            goals[index] = original
            errorMessage = "Goal could not be paused. Try again."
            statusMessage = "Pause failed."
        }
    }

    private var constraintsDictionary: [String: String] {
        let trimmed = constraints.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? [:] : ["notes": trimmed]
    }
}

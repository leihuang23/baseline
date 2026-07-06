import BaselineCore
import SwiftUI

@MainActor
final class DailyBriefingViewModel: ObservableObject {
    @Published private(set) var briefing: DailyBriefingResponse?
    @Published private(set) var followUpAnswer: AssistantQueryResponse?
    @Published private(set) var isGenerating = false
    @Published private(set) var isAskingFollowUp = false
    @Published private(set) var isLoadingTrace = false
    @Published private(set) var isOfflineFallback = false
    @Published private(set) var isRetryable = false
    @Published private(set) var statusMessage = "Latest briefing will appear here after morning sync."
    @Published var followUpQuestion = ""
    @Published var errorMessage: String?

    private let apiClient: any DailyBriefingAPIClient
    private let briefingStore: any BriefingPersisting
    private let privacyMode: () -> PrivacyMode
    private var syncBeforeGenerate: () async -> Void
    private let sleep: @Sendable (UInt64) async -> Void
    private let maxFetchAttempts: Int?
    private let pollIntervalNanoseconds: UInt64
    private let minimumPollingSeconds: Int
    private let maximumPollingSeconds: Int
    private let dateProvider: () -> String

    init(
        apiClient: any DailyBriefingAPIClient,
        briefingStore: any BriefingPersisting,
        privacyMode: @escaping () -> PrivacyMode,
        syncBeforeGenerate: @escaping () async -> Void = {},
        sleep: @escaping @Sendable (UInt64) async -> Void = { nanoseconds in
            try? await Task.sleep(nanoseconds: nanoseconds)
        },
        maxFetchAttempts: Int? = nil,
        pollIntervalNanoseconds: UInt64 = 2_000_000_000,
        minimumPollingSeconds: Int = 60,
        maximumPollingSeconds: Int = 180,
        dateProvider: @escaping () -> String = { DailyBriefingViewModel.todayString() }
    ) {
        self.apiClient = apiClient
        self.briefingStore = briefingStore
        self.privacyMode = privacyMode
        self.syncBeforeGenerate = syncBeforeGenerate
        self.sleep = sleep
        self.maxFetchAttempts = maxFetchAttempts.map { max(1, $0) }
        self.pollIntervalNanoseconds = max(1, pollIntervalNanoseconds)
        self.minimumPollingSeconds = max(1, minimumPollingSeconds)
        self.maximumPollingSeconds = max(self.minimumPollingSeconds, maximumPollingSeconds)
        self.dateProvider = dateProvider
        loadCachedBriefing()
    }

    func loadCachedBriefing() {
        guard let cached = try? briefingStore.loadLatestBriefing() else {
            return
        }
        briefing = cached
        isOfflineFallback = true
        statusMessage = "Showing latest saved briefing."
    }

    func setSyncAction(_ action: @escaping () async -> Void) {
        syncBeforeGenerate = action
    }

    func retryAnalysis() async {
        guard isRetryable else {
            return
        }
        isRetryable = false
        await generateBriefing()
    }

    func generateBriefing() async {
        guard !isGenerating else {
            return
        }
        isGenerating = true
        errorMessage = nil
        followUpAnswer = nil
        isRetryable = false
        statusMessage = "Syncing health data..."
        defer { isGenerating = false }

        await syncBeforeGenerate()

        let targetDate = dateProvider()
        do {
            statusMessage = "Generating daily briefing..."
            let job = try await apiClient.generateDailyAnalysis(
                DailyAnalysisRequest(
                    date: targetDate,
                    privacyMode: BriefingPrivacyMode(privacyMode())
                )
            )
            let completedJob = try await waitForCompletedJob(job)
            if completedJob.status == "failed" {
                throw BriefingViewModelError.generationFailed
            }
            try await fetchGeneratedBriefing(date: targetDate)
        } catch BriefingViewModelError.generationTimedOut {
            loadOfflineFallback(
                message: "Analysis is still running. Showing latest saved briefing.",
                retryable: true
            )
        } catch BriefingViewModelError.generationFailed {
            loadOfflineFallback(message: "Generation failed. Showing latest saved briefing.")
        } catch {
            loadOfflineFallback(message: "Network or generation unavailable. Showing latest saved briefing.")
        }
    }

    func askFollowUp() async {
        let question = followUpQuestion.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !isAskingFollowUp else {
            return
        }
        isAskingFollowUp = true
        errorMessage = nil
        defer { isAskingFollowUp = false }

        do {
            let response = try await apiClient.submitAssistantQuery(
                AssistantQueryRequest(
                    question: question,
                    dateContext: briefing?.date ?? dateProvider(),
                    privacyMode: BriefingPrivacyMode(privacyMode())
                )
            )
            followUpAnswer = response
            followUpQuestion = ""
            statusMessage = "Follow-up answered from trace-backed evidence."
        } catch {
            errorMessage = "Follow-up could not be answered right now."
        }
    }

    private func waitForCompletedJob(_ job: DailyAnalysisResponse) async throws -> DailyAnalysisResponse {
        var current = job
        let attemptLimit = pollingAttemptLimit(for: job)
        for attempt in 1 ... attemptLimit {
            if current.status == "completed" || current.status == "failed" {
                return current
            }
            if attempt == attemptLimit {
                throw BriefingViewModelError.generationTimedOut
            }
            statusMessage = "Analysis still running..."
            await sleep(pollIntervalNanoseconds)
            current = try await apiClient.fetchDailyAnalysisJob(id: job.analysisJobID)
        }
        throw BriefingViewModelError.generationTimedOut
    }

    private func fetchGeneratedBriefing(date: String) async throws {
        let attemptLimit = maxFetchAttempts ?? 3
        for attempt in 1 ... attemptLimit {
            do {
                var response = try await apiClient.fetchDailyBriefing(date: date, offlineLast: false)
                let trace = await loadTrace(for: response)
                response.trace = trace
                briefing = response
                try? briefingStore.saveLatestBriefing(response)
                isOfflineFallback = false
                statusMessage = trace == nil
                    ? "Briefing loaded. Trace details unavailable."
                    : responseStatusMessage(for: response)
                return
            } catch {
                if attempt == attemptLimit {
                    throw error
                }
                statusMessage = "Waiting for briefing..."
                await sleep(pollIntervalNanoseconds)
            }
        }
    }

    func pollingAttemptLimit(for job: DailyAnalysisResponse) -> Int {
        let estimatedDeadline = max(minimumPollingSeconds, job.estimatedCompletionSeconds * 2)
        let cappedDeadline = min(maximumPollingSeconds, estimatedDeadline)
        let intervalSeconds = max(1, Int(ceil(Double(pollIntervalNanoseconds) / 1_000_000_000)))
        let estimatedAttempts = max(1, Int(ceil(Double(cappedDeadline) / Double(intervalSeconds))) + 1)
        if let maxFetchAttempts {
            return min(maxFetchAttempts, estimatedAttempts)
        }
        return estimatedAttempts
    }

    private func loadTrace(for response: DailyBriefingResponse) async -> BriefingTraceInspection? {
        isLoadingTrace = true
        defer { isLoadingTrace = false }
        do {
            return try await apiClient.fetchBriefingTrace(traceID: response.traceID)
        } catch {
            return nil
        }
    }

    private func loadOfflineFallback(message: String, retryable: Bool = false) {
        if let cached = try? briefingStore.loadLatestBriefing() {
            briefing = cached
            isOfflineFallback = true
            isRetryable = retryable
            statusMessage = message
            errorMessage = nil
        } else {
            isOfflineFallback = false
            isRetryable = retryable
            statusMessage = "Briefing unavailable."
            errorMessage = "No saved briefing is available offline."
        }
    }

    private func responseStatusMessage(for response: DailyBriefingResponse) -> String {
        if response.isDeterministicFallback {
            return "Generated with deterministic fallback."
        }
        return "Briefing is fresh."
    }

    private static func todayString() -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .autoupdatingCurrent
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: Date())
    }
}

enum BriefingViewModelError: Error {
    case generationFailed
    case generationTimedOut
}

struct DailyBriefingView: View {
    @ObservedObject var viewModel: DailyBriefingViewModel

    var body: some View {
        List {
            Section {
                freshnessBanner
                safetyBanner
            }

            Section {
                Button {
                    Task { await viewModel.generateBriefing() }
                } label: {
                    if viewModel.isGenerating {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Label("Sync + generate briefing", systemImage: "arrow.triangle.2.circlepath")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(viewModel.isGenerating)

                Text(viewModel.statusMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                if viewModel.isRetryable {
                    Button {
                        Task { await viewModel.retryAnalysis() }
                    } label: {
                        Label("Retry analysis", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(.bordered)
                }
                if let errorMessage = viewModel.errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }

            if let briefing = viewModel.briefing {
                Section("Recommendation") {
                    LabeledContent("Readiness", value: briefing.readinessState.displayLabel)
                    LabeledContent("Confidence", value: briefing.confidence.displayLabel)
                    LabeledContent("Direction", value: briefing.recommendationBand.displayLabel)
                    Text(briefing.recommendation.primary)
                        .font(.headline)
                    if let avoid = briefing.recommendation.avoid {
                        Text(avoid)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Evidence") {
                    ForEach(briefing.evidence) { item in
                        EvidenceRow(item: item)
                    }
                }

                Section("Uncertainty") {
                    TextList(briefing.uncertainty)
                    ForEach(briefing.dataQualityNotes) { note in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(note.metric ?? "Data quality")
                                .font(.subheadline)
                            Text(note.note)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("Goal tradeoffs") {
                    if briefing.goalTradeoffs.isEmpty {
                        Text("No active goal tradeoffs in this briefing.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(briefing.goalTradeoffs) { item in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(item.goal)
                            Text(item.tradeoff)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("Alternatives") {
                    ForEach(briefing.candidateOptions) { option in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(option.label)
                            Text(option.rationale)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    ForEach(briefing.alternatives) { alternative in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(alternative.label)
                            Text(alternative.rationale)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("What would change my mind") {
                    TextList(briefing.whatWouldChangeMyMind)
                }

                Section("Trace") {
                    NavigationLink {
                        TraceInspectionView(briefing: briefing, trace: briefing.trace)
                    } label: {
                        Label("Show trace", systemImage: "doc.text.magnifyingglass")
                    }
                    LabeledContent("Trace ID", value: briefing.traceID.uuidString)
                        .font(.caption)
                    if viewModel.isLoadingTrace {
                        ProgressView("Loading trace")
                    }
                }

                Section("Follow-up") {
                    if let prompt = briefing.followUp {
                        Button(prompt.question) {
                            viewModel.followUpQuestion = prompt.question
                        }
                        Text(prompt.reason)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    TextField("Ask about this briefing", text: $viewModel.followUpQuestion, axis: .vertical)
                        .lineLimit(1...4)
                    Button {
                        Task { await viewModel.askFollowUp() }
                    } label: {
                        if viewModel.isAskingFollowUp {
                            ProgressView()
                        } else {
                            Label("Ask follow-up", systemImage: "questionmark.bubble")
                        }
                    }
                    .disabled(viewModel.followUpQuestion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if let answer = viewModel.followUpAnswer {
                        AssistantAnswerView(answer: answer)
                    }
                }
            } else {
                Section("Briefing") {
                    Text("No briefing saved yet.")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Briefing")
    }

    private var freshnessBanner: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Freshness", systemImage: viewModel.isOfflineFallback ? "wifi.slash" : "clock")
                Spacer()
                Text(viewModel.isOfflineFallback ? "Offline saved" : "Current session")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let freshness = viewModel.briefing?.dataFreshness {
                Text(freshness.summary)
                    .font(.footnote)
                if let generatedAt = viewModel.briefing?.generatedAt {
                    Text("Generated: \(generatedAt)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if !freshness.staleSources.isEmpty {
                    Text("Stale: \(freshness.staleSources.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            } else {
                Text("No generated briefing freshness yet.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var safetyBanner: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Safety note", systemImage: "cross.case")
            if let briefing = viewModel.briefing {
                Text(briefing.safetyNotes.joined(separator: " "))
                    .font(.footnote)
                Text("Status: \(briefing.safetyStatus.displayLabel)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("Baseline provides wellness decision support, not medical diagnosis or treatment.")
                    .font(.footnote)
            }
        }
    }
}

struct TraceInspectionView: View {
    let briefing: DailyBriefingResponse
    let trace: BriefingTraceInspection?

    var body: some View {
        List {
            Section("Data freshness") {
                let freshness = trace?.dataFreshness ?? briefing.dataFreshness
                Text(freshness.summary)
                if freshness.staleSources.isEmpty {
                    Text("No stale sources reported.")
                        .foregroundStyle(.secondary)
                } else {
                    TextList(freshness.staleSources)
                }
            }

            if trace == nil {
                Section("Trace availability") {
                    Text("Trace details are unavailable for this briefing.")
                        .foregroundStyle(.secondary)
                }
            }

            Section("Feature values") {
                if trace?.featureValues.isEmpty ?? true {
                    Text("No feature values were exposed for this trace.")
                        .foregroundStyle(.secondary)
                }
                ForEach(trace?.featureValues ?? []) { item in
                    EvidenceRow(item: item)
                }
            }

            Section("Rules fired") {
                if trace?.rulesFired.isEmpty ?? true {
                    Text("No rule flags were exposed for this briefing.")
                        .foregroundStyle(.secondary)
                } else {
                    TextList(trace?.rulesFired ?? [])
                }
            }

            Section("Retrieved memory") {
                if trace?.retrievedMemory.isEmpty ?? true {
                    Text("No memory observations were retrieved.")
                        .foregroundStyle(.secondary)
                }
                ForEach(trace?.retrievedMemory ?? []) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.observation)
                        Text(item.relevance)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section("External sources") {
                if trace?.externalSources.isEmpty ?? true {
                    Text("No external sources used.")
                        .foregroundStyle(.secondary)
                }
                ForEach(trace?.externalSources ?? []) { source in
                    CitationRow(citation: source)
                }
            }

            Section("Model metadata") {
                if trace?.modelMetadata.isEmpty ?? true {
                    Text("No model metadata available.")
                        .foregroundStyle(.secondary)
                }
                if let generationStatus = trace?.modelMetadata["briefing_generation_status"] {
                    MetadataRow(label: "Briefing generation status", value: generationStatus)
                }
                ForEach(
                    (trace?.modelMetadata ?? [:])
                        .filter { $0.key != "briefing_generation_status" }
                        .sorted(by: { $0.key < $1.key }),
                    id: \.key
                ) { key, value in
                    MetadataRow(label: key.displayLabel, value: value)
                }
            }
        }
        .navigationTitle("Trace")
    }
}

struct MetadataRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
            Text(value)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

struct AssistantAnswerView: View {
    let answer: AssistantQueryResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(answer.answer)
            LabeledContent("Safety", value: answer.safetyStatus.displayLabel)
            LabeledContent("Confidence", value: answer.confidence.displayLabel)
            Text("Personal evidence")
                .font(.subheadline)
            ForEach(answer.personalEvidence) { item in
                EvidenceRow(item: item)
            }
            Text("External citations")
                .font(.subheadline)
            ForEach(answer.externalSources) { citation in
                CitationRow(citation: citation)
            }
            Text("Uncertainty")
                .font(.subheadline)
            TextList(answer.uncertainty)
        }
    }
}

struct EvidenceRow: View {
    let item: PersonalEvidence

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(item.metric.displayLabel)
                Spacer()
                Text(item.value.displayText)
                    .foregroundStyle(.secondary)
            }
            Text(item.interpretation)
                .font(.caption)
                .foregroundStyle(.secondary)
            if let source = item.source {
                Text(source)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

struct CitationRow: View {
    let citation: ExternalCitation

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(citation.title)
            Text(citation.source)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(citation.citedClaim)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

struct TextList: View {
    let items: [String]

    init(_ items: [String]) {
        self.items = items
    }

    var body: some View {
        if items.isEmpty {
            Text("None reported.")
                .foregroundStyle(.secondary)
        } else {
            ForEach(items, id: \.self) { item in
                Text(item)
            }
        }
    }
}

private extension DataFreshness {
    var summary: String {
        let sample = latestSampleAt ?? "no sample timestamp"
        let checkIn = latestCheckInDate ?? "no check-in"
        return "Latest sample: \(sample). Latest check-in: \(checkIn)."
    }
}

private extension String {
    var displayLabel: String {
        split(separator: "_")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }
}

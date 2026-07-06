import Foundation

public struct HealthKitReadResult: Equatable, Sendable {
    public var category: HealthCategory
    public var samples: [HealthSample]
    public var newAnchorData: Data?

    public init(
        category: HealthCategory,
        samples: [HealthSample],
        newAnchorData: Data?
    ) {
        self.category = category
        self.samples = samples
        self.newAnchorData = newAnchorData
    }
}

public struct BatchBuildResult: Equatable, Sendable {
    public var request: HealthSyncRequest
    public var anchorsAfterQuery: [HealthCategory: Data?]

    public init(request: HealthSyncRequest, anchorsAfterQuery: [HealthCategory: Data?]) {
        self.request = request
        self.anchorsAfterQuery = anchorsAfterQuery
    }
}

public struct SyncOutcome: Equatable, Sendable {
    public var response: HealthSyncResponse
    public var syncedAt: Date
    public var sentSampleCount: Int
    public var skippedCategories: [HealthCategory]

    public init(
        response: HealthSyncResponse,
        syncedAt: Date,
        sentSampleCount: Int,
        skippedCategories: [HealthCategory] = []
    ) {
        self.response = response
        self.syncedAt = syncedAt
        self.sentSampleCount = sentSampleCount
        self.skippedCategories = skippedCategories
    }
}

public enum HealthSyncEngineError: Error, Equatable, Sendable {
    case localOnlySyncDisabled
    case noReadableCategories([HealthCategory])
}

public protocol HealthKitReading: Sendable {
    func readSamples(
        for category: HealthCategory,
        anchorData: Data?
    ) async throws -> HealthKitReadResult
}

public protocol HealthSyncAPIClient: Sendable {
    func recordConsent(_ request: ConsentRecordRequest) async throws -> DataControlConsentResponse
    func postHealthSync(_ request: HealthSyncRequest) async throws -> HealthSyncResponse
}

public protocol CheckInAPIClient: Sendable {
    func fetchDailyCheckIn(date: String) async throws -> DailyCheckInDetailResponse
    func submitDailyCheckIn(_ request: DailyCheckInRequest) async throws -> DailyCheckInResponse
    func updateDailyCheckIn(
        id: UUID,
        request: DailyCheckInRequest
    ) async throws -> DailyCheckInResponse
    func deleteDailyCheckIn(id: UUID) async throws
    func deleteDailyCheckInNote(id: UUID) async throws
}

public protocol GoalsAPIClient: Sendable {
    func listGoals() async throws -> [GoalResponse]
    func createGoal(_ request: GoalRequest) async throws -> GoalResponse
    func pauseGoal(id: UUID) async throws -> GoalResponse
}

public protocol DailyBriefingAPIClient: Sendable {
    func generateDailyAnalysis(_ request: DailyAnalysisRequest) async throws -> DailyAnalysisResponse
    func fetchDailyAnalysisJob(id: UUID) async throws -> DailyAnalysisResponse
    func fetchDailyBriefing(date: String, offlineLast: Bool) async throws -> DailyBriefingResponse
    func fetchBriefingTrace(traceID: UUID) async throws -> BriefingTraceInspection
    func submitAssistantQuery(_ request: AssistantQueryRequest) async throws -> AssistantQueryResponse
    func submitRecommendationFeedback(
        recommendationID: UUID,
        request: RecommendationFeedbackRequest
    ) async throws -> RecommendationFeedbackResponse
}

public protocol DataControlsAPIClient: Sendable {
    func requestDataExport(_ request: DataExportRequest) async throws -> DataExportResponse
    func downloadDataExport(from downloadURL: String) async throws -> Data
    func downloadDecryptedDataExport(_ response: DataExportResponse) async throws -> Data
    func deleteAllData() async throws -> DataDeleteResponse
    func deleteDailyCheckInNote(id: UUID) async throws
    func disableExternalLLM(_ request: DisableExternalLLMRequest) async throws -> DataControlConsentResponse
    func disableCloudProcessing(_ request: ConsentRevocationRequest) async throws -> DataControlConsentResponse
    func fetchConsentHistory() async throws -> ConsentHistoryResponse
    func fetchModelDisclosures() async throws -> ModelDisclosureResponse
    func fetchLLMSettings() async throws -> LLMSettingsResponse
}

public protocol MemoryAPIClient: Sendable {
    func fetchMemorySummaries(periodType: MemoryPeriodType?) async throws -> MemorySummaryListResponse
    func deleteMemorySummary(id: UUID) async throws
}

public struct HealthSyncBatchBuilder: Sendable {
    public init() {}

    public func buildBatch(
        reads: [HealthKitReadResult],
        anchors: [HealthCategory: CategoryAnchor],
        consent: ConsentRecord,
        deviceID: String,
        timezone: TimeZone = .autoupdatingCurrent,
        clientSyncID: String = UUID().uuidString
    ) -> BatchBuildResult {
        let orderedReads = reads.sorted { $0.category.rawValue < $1.category.rawValue }
        let samples = orderedReads.flatMap(\.samples).sorted {
            if $0.startTime == $1.startTime {
                return $0.sourceSampleID < $1.sourceSampleID
            }
            return $0.startTime < $1.startTime
        }
        let serverAnchors = orderedReads.compactMap { anchors[$0.category]?.serverAnchor }
        let lastAnchor = serverAnchors.sorted().last
        let nextHealthKitAnchors = Dictionary(
            uniqueKeysWithValues: orderedReads.map { ($0.category, $0.newAnchorData) }
        )
        return BatchBuildResult(
            request: HealthSyncRequest(
                clientSyncID: clientSyncID,
                deviceID: deviceID,
                timezone: timezone.baselineIdentifier,
                samples: samples,
                lastAnchor: lastAnchor,
                consentVersion: consent.consentVersion
            ),
            anchorsAfterQuery: nextHealthKitAnchors
        )
    }
}

private extension TimeZone {
    var baselineIdentifier: String {
        secondsFromGMT() == 0 ? "UTC" : identifier
    }
}

public final class HealthSyncEngine: Sendable {
    private let anchorStore: any AnchorPersisting
    private let healthKitReader: any HealthKitReading
    private let apiClient: any HealthSyncAPIClient
    private let batchBuilder: HealthSyncBatchBuilder
    private let clock: @Sendable () -> Date

    public init(
        anchorStore: any AnchorPersisting,
        healthKitReader: any HealthKitReading,
        apiClient: any HealthSyncAPIClient,
        batchBuilder: HealthSyncBatchBuilder = HealthSyncBatchBuilder(),
        clock: @escaping @Sendable () -> Date = Date.init
    ) {
        self.anchorStore = anchorStore
        self.healthKitReader = healthKitReader
        self.apiClient = apiClient
        self.batchBuilder = batchBuilder
        self.clock = clock
    }

    public func syncNow(consent: ConsentRecord, deviceID: String) async throws -> SyncOutcome {
        guard consent.processingMode != .localOnly else {
            try anchorStore.clearPendingBatch()
            throw HealthSyncEngineError.localOnlySyncDisabled
        }
        if let pending = try anchorStore.loadPendingBatch() {
            return try await finishPendingBatch(pending)
        }

        let categories = consent.enabledCategories
        var anchors: [HealthCategory: CategoryAnchor] = [:]
        var reads: [HealthKitReadResult] = []
        var skippedCategories: [HealthCategory] = []
        for category in categories {
            let anchor = try anchorStore.loadAnchor(for: category) ?? CategoryAnchor()
            anchors[category] = anchor
            do {
                let read = try await healthKitReader.readSamples(
                    for: category,
                    anchorData: anchor.healthKitAnchorData
                )
                reads.append(read)
            } catch {
                skippedCategories.append(category)
            }
        }

        skippedCategories.sort { $0.rawValue < $1.rawValue }
        guard !reads.isEmpty else {
            throw HealthSyncEngineError.noReadableCategories(skippedCategories)
        }

        let batch = batchBuilder.buildBatch(
            reads: reads,
            anchors: anchors,
            consent: consent,
            deviceID: deviceID
        )
        let pending = PendingSyncBatch(
            request: batch.request,
            anchorsAfterQuery: batch.anchorsAfterQuery,
            skippedCategories: skippedCategories,
            createdAt: clock()
        )
        try anchorStore.savePendingBatch(pending)
        return try await finishPendingBatch(pending)
    }

    private func finishPendingBatch(_ pending: PendingSyncBatch) async throws -> SyncOutcome {
        let response = try await apiClient.postHealthSync(pending.request)
        let syncedAt = clock()
        for (category, healthKitAnchorData) in pending.anchorsAfterQuery {
            try anchorStore.saveAnchor(
                CategoryAnchor(
                    healthKitAnchorData: healthKitAnchorData,
                    serverAnchor: response.nextAnchor,
                    lastSyncedAt: syncedAt
                ),
                for: category
            )
        }
        try anchorStore.clearPendingBatch()
        return SyncOutcome(
            response: response,
            syncedAt: syncedAt,
            sentSampleCount: pending.request.samples.count,
            skippedCategories: pending.skippedCategories
        )
    }
}

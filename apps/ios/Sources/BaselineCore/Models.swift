import Foundation

public enum PrivacyMode: String, CaseIterable, Codable, Identifiable, Sendable {
    case localOnly = "local_only"
    case cloudAssisted = "cloud_assisted"
    case hybrid

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .localOnly:
            "Local-only"
        case .cloudAssisted:
            "Cloud-assisted"
        case .hybrid:
            "Hybrid"
        }
    }

    public var consentSummary: String {
        switch self {
        case .localOnly:
            "Keep processing on this device where supported. Sync only when you choose to send data."
        case .cloudAssisted:
            "Allow Baseline server processing for sync and evidence-backed briefings."
        case .hybrid:
            "Use local handling first, with cloud processing only for enabled Baseline features."
        }
    }
}

public enum HealthCategory: String, CaseIterable, Codable, Identifiable, Sendable {
    case sleep
    case workouts
    case steps
    case heartRateVariability = "heart_rate_variability"
    case restingHeartRate = "resting_heart_rate"
    case vo2Max = "vo2_max"

    public var id: String { rawValue }

    public var apiSampleType: String {
        switch self {
        case .sleep:
            "sleep_duration"
        case .workouts:
            "workout"
        case .steps:
            "steps"
        case .heartRateVariability:
            "heart_rate_variability"
        case .restingHeartRate:
            "resting_heart_rate"
        case .vo2Max:
            "vo2_max"
        }
    }

    public var title: String {
        switch self {
        case .sleep:
            "Sleep"
        case .workouts:
            "Workouts"
        case .steps:
            "Steps"
        case .heartRateVariability:
            "HRV"
        case .restingHeartRate:
            "Resting heart rate"
        case .vo2Max:
            "VO2 max"
        }
    }

    public var permissionRationale: String {
        switch self {
        case .sleep:
            "Used to detect sleep debt and stale recovery inputs."
        case .workouts:
            "Used to estimate recent training load and workout timing."
        case .steps:
            "Used as a lightweight activity signal when workouts are incomplete."
        case .heartRateVariability:
            "Used as a recovery signal when Apple Health has HRV data."
        case .restingHeartRate:
            "Used to spot recovery stress when compared with your baseline."
        case .vo2Max:
            "Used as a slow-moving cardio fitness context signal."
        }
    }
}

public struct WakeTime: Codable, Equatable, Sendable, Hashable {
    public var hour: Int
    public var minute: Int

    public init(hour: Int = 7, minute: Int = 0) {
        self.hour = hour
        self.minute = minute
    }
}

public struct ConsentRecord: Codable, Equatable, Sendable {
    public static let currentVersion = "p1-04-v1"

    public var consentVersion: String
    public var grantedAt: Date
    public var enabledCategories: [HealthCategory]
    public var deniedCategories: [HealthCategory]
    public var processingMode: PrivacyMode
    public var wakeTime: WakeTime

    public init(
        consentVersion: String = ConsentRecord.currentVersion,
        grantedAt: Date = Date(),
        enabledCategories: [HealthCategory],
        deniedCategories: [HealthCategory] = [],
        processingMode: PrivacyMode,
        wakeTime: WakeTime = WakeTime()
    ) {
        self.consentVersion = consentVersion
        self.grantedAt = grantedAt
        self.enabledCategories = enabledCategories
        self.deniedCategories = deniedCategories
        self.processingMode = processingMode
        self.wakeTime = wakeTime
    }

    enum CodingKeys: String, CodingKey {
        case consentVersion
        case grantedAt
        case enabledCategories
        case deniedCategories
        case processingMode
        case wakeTime
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        consentVersion = try container.decode(String.self, forKey: .consentVersion)
        grantedAt = try container.decode(Date.self, forKey: .grantedAt)
        enabledCategories = try container.decode([HealthCategory].self, forKey: .enabledCategories)
        deniedCategories = try container.decodeIfPresent([HealthCategory].self, forKey: .deniedCategories) ?? []
        processingMode = try container.decode(PrivacyMode.self, forKey: .processingMode)
        wakeTime = try container.decodeIfPresent(WakeTime.self, forKey: .wakeTime) ?? WakeTime()
    }
}

public struct ConsentRecordRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var consentVersion: String
    public var healthCategoriesEnabled: [String]
    public var cloudProcessingEnabled: Bool
    public var externalLLMEnabled: Bool
    public var rawNoteProcessingEnabled: Bool
    public var privacyMode: BriefingPrivacyMode?

    public init(
        consentVersion: String,
        healthCategoriesEnabled: [String],
        cloudProcessingEnabled: Bool,
        externalLLMEnabled: Bool,
        rawNoteProcessingEnabled: Bool = false,
        privacyMode: BriefingPrivacyMode? = nil
    ) {
        self.consentVersion = consentVersion
        self.healthCategoriesEnabled = healthCategoriesEnabled
        self.cloudProcessingEnabled = cloudProcessingEnabled
        self.externalLLMEnabled = externalLLMEnabled
        self.rawNoteProcessingEnabled = rawNoteProcessingEnabled
        self.privacyMode = privacyMode
    }

    public init(consent: ConsentRecord) {
        self.init(
            consentVersion: consent.consentVersion,
            healthCategoriesEnabled: Self.backendConsentCategories(for: consent.enabledCategories),
            cloudProcessingEnabled: consent.processingMode != .localOnly,
            externalLLMEnabled: consent.processingMode == .cloudAssisted,
            rawNoteProcessingEnabled: false,
            privacyMode: BriefingPrivacyMode(consent.processingMode)
        )
    }

    private static func backendConsentCategories(for categories: [HealthCategory]) -> [String] {
        let mapped = categories.flatMap { category -> [String] in
            switch category {
            case .sleep:
                ["sleep"]
            case .workouts, .steps, .vo2Max:
                ["activity"]
            case .heartRateVariability, .restingHeartRate:
                ["heart_rate"]
            }
        }
        return Array(Set(mapped)).sorted()
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case consentVersion = "consent_version"
        case healthCategoriesEnabled = "health_categories_enabled"
        case cloudProcessingEnabled = "cloud_processing_enabled"
        case externalLLMEnabled = "external_llm_enabled"
        case rawNoteProcessingEnabled = "raw_note_processing_enabled"
        case privacyMode = "privacy_mode"
    }
}

public enum HealthKitSleepAnalysisMetadata {
    public static func asleepMetadata(forRawValue rawValue: Int) -> [String: String]? {
        guard let stage = stageName(forRawValue: rawValue) else {
            return nil
        }
        return [
            "healthkit_sleep_analysis_value": String(rawValue),
            "healthkit_sleep_stage": stage,
        ]
    }

    private static func stageName(forRawValue rawValue: Int) -> String? {
        switch rawValue {
        case 1:
            "asleep_unspecified"
        case 3:
            "asleep_core"
        case 4:
            "asleep_deep"
        case 5:
            "asleep_rem"
        default:
            nil
        }
    }
}

public struct HealthSample: Codable, Equatable, Sendable {
    public var sourceSampleID: String
    public var sampleType: String
    public var startTime: Date
    public var endTime: Date?
    public var value: Double
    public var unit: String
    public var sourceMetadata: [String: String]

    public init(
        sourceSampleID: String,
        sampleType: String,
        startTime: Date,
        endTime: Date? = nil,
        value: Double,
        unit: String,
        sourceMetadata: [String: String] = [:]
    ) {
        self.sourceSampleID = sourceSampleID
        self.sampleType = sampleType
        self.startTime = startTime
        self.endTime = endTime
        self.value = value
        self.unit = unit
        self.sourceMetadata = sourceMetadata
    }

    enum CodingKeys: String, CodingKey {
        case sourceSampleID = "source_sample_id"
        case sampleType = "sample_type"
        case startTime = "start_time"
        case endTime = "end_time"
        case value
        case unit
        case sourceMetadata = "source_metadata"
    }
}

public enum DemoHealthData {
    public static let samples: [HealthSample] = [
        HealthSample(
            sourceSampleID: "demo-sleep-1",
            sampleType: HealthCategory.sleep.apiSampleType,
            startTime: Date(timeIntervalSince1970: 1_735_862_400),
            endTime: Date(timeIntervalSince1970: 1_735_889_400),
            value: 7.5,
            unit: "h",
            sourceMetadata: [
                "source": "demo",
                "category": HealthCategory.sleep.rawValue,
                "healthkit_sleep_stage": "asleep_unspecified",
            ]
        ),
        HealthSample(
            sourceSampleID: "demo-steps-1",
            sampleType: HealthCategory.steps.apiSampleType,
            startTime: Date(timeIntervalSince1970: 1_735_891_200),
            endTime: Date(timeIntervalSince1970: 1_735_977_600),
            value: 8_420,
            unit: "count",
            sourceMetadata: ["source": "demo", "category": HealthCategory.steps.rawValue]
        ),
        HealthSample(
            sourceSampleID: "demo-hrv-1",
            sampleType: HealthCategory.heartRateVariability.apiSampleType,
            startTime: Date(timeIntervalSince1970: 1_735_884_000),
            endTime: Date(timeIntervalSince1970: 1_735_884_300),
            value: 58,
            unit: "ms",
            sourceMetadata: [
                "source": "demo",
                "category": HealthCategory.heartRateVariability.rawValue,
            ]
        ),
    ]
}

public struct HealthSyncRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var clientSyncID: String
    public var deviceID: String
    public var timezone: String
    public var samples: [HealthSample]
    public var lastAnchor: String?
    public var consentVersion: String

    public init(
        clientSyncID: String,
        deviceID: String,
        timezone: String,
        samples: [HealthSample],
        lastAnchor: String?,
        consentVersion: String
    ) {
        self.clientSyncID = clientSyncID
        self.deviceID = deviceID
        self.timezone = timezone
        self.samples = samples
        self.lastAnchor = lastAnchor
        self.consentVersion = consentVersion
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case clientSyncID = "client_sync_id"
        case deviceID = "device_id"
        case timezone
        case samples
        case lastAnchor = "last_anchor"
        case consentVersion = "consent_version"
    }
}

public struct APIEnvelope<T: Codable & Sendable>: Codable, Sendable {
    public var status: String
    public var data: T?
}

public enum PrivacyJSONValue: Codable, Equatable, Sendable {
    case object([String: PrivacyJSONValue])
    case array([PrivacyJSONValue])
    case string(String)
    case double(Double)
    case bool(Bool)
    case null

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode([String: PrivacyJSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([PrivacyJSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else {
            self = try .string(container.decode(String.self))
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        case .double(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }
}

public enum DataExportScope: String, Codable, CaseIterable, Identifiable, Sendable {
    public var id: String { rawValue }
    case all
    case health
    case checkins
    case briefings
    case recommendations
    case memory
    case consent
}

public enum DataExportFormat: String, Codable, CaseIterable, Identifiable, Sendable {
    public var id: String { rawValue }
    case json
    case csv
}

public struct DataExportRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var exportScope: DataExportScope
    public var format: DataExportFormat
    public var includeRawData: Bool
    public var includeModelTraces: Bool

    public init(
        exportScope: DataExportScope,
        format: DataExportFormat = .json,
        includeRawData: Bool = false,
        includeModelTraces: Bool = false
    ) {
        self.exportScope = exportScope
        self.format = format
        self.includeRawData = includeRawData
        self.includeModelTraces = includeModelTraces
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case exportScope = "export_scope"
        case format
        case includeRawData = "include_raw_data"
        case includeModelTraces = "include_model_traces"
    }
}

public struct DataExportResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var exportJobID: UUID
    public var status: String
    public var expiresAt: String
    public var downloadURL: String?
    public var encryption: [String: String]

    public init(
        schemaVersion: String = "v1",
        exportJobID: UUID,
        status: String,
        expiresAt: String,
        downloadURL: String? = nil,
        encryption: [String: String] = [:]
    ) {
        self.schemaVersion = schemaVersion
        self.exportJobID = exportJobID
        self.status = status
        self.expiresAt = expiresAt
        self.downloadURL = downloadURL
        self.encryption = encryption
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case exportJobID = "export_job_id"
        case status
        case expiresAt = "expires_at"
        case downloadURL = "download_url"
        case encryption
    }
}

public struct DataControlConsentResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var id: UUID
    public var userID: UUID
    public var consentVersion: String
    public var healthCategoriesEnabled: [String]
    public var cloudProcessingEnabled: Bool
    public var externalLLMEnabled: Bool
    public var rawNoteProcessingEnabled: Bool
    public var timestamp: String
    public var revokedAt: String?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case id
        case userID = "user_id"
        case consentVersion = "consent_version"
        case healthCategoriesEnabled = "health_categories_enabled"
        case cloudProcessingEnabled = "cloud_processing_enabled"
        case externalLLMEnabled = "external_llm_enabled"
        case rawNoteProcessingEnabled = "raw_note_processing_enabled"
        case timestamp
        case revokedAt = "revoked_at"
    }
}

public struct ConsentHistoryResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var activeConsentVersion: String
    public var records: [DataControlConsentResponse]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case activeConsentVersion = "active_consent_version"
        case records
    }
}

public struct DisableExternalLLMRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var consentVersion: String?

    public init(consentVersion: String? = nil) {
        self.consentVersion = consentVersion
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case consentVersion = "consent_version"
    }
}

public struct ConsentRevocationRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var consentVersion: String?
    public var revokeCloudProcessing: Bool
    public var revokeExternalLLM: Bool
    public var revokeRawNoteProcessing: Bool
    public var revokeHealthCategories: [String]?

    public init(
        consentVersion: String? = nil,
        revokeCloudProcessing: Bool = true,
        revokeExternalLLM: Bool = true,
        revokeRawNoteProcessing: Bool = true,
        revokeHealthCategories: [String]? = nil
    ) {
        self.consentVersion = consentVersion
        self.revokeCloudProcessing = revokeCloudProcessing
        self.revokeExternalLLM = revokeExternalLLM
        self.revokeRawNoteProcessing = revokeRawNoteProcessing
        self.revokeHealthCategories = revokeHealthCategories
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case consentVersion = "consent_version"
        case revokeCloudProcessing = "revoke_cloud_processing"
        case revokeExternalLLM = "revoke_external_llm"
        case revokeRawNoteProcessing = "revoke_raw_note_processing"
        case revokeHealthCategories = "revoke_health_categories"
    }
}

public struct DataDeleteResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var deleted: [String: Int]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case deleted
    }
}

public enum FeedbackRating: String, Codable, CaseIterable, Identifiable, Sendable {
    case useful
    case somewhatUseful = "somewhat_useful"
    case notUseful = "not_useful"
    case unsafeOrWrong = "unsafe_or_wrong"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .useful:
            "Useful"
        case .somewhatUseful:
            "Somewhat useful"
        case .notUseful:
            "Not useful"
        case .unsafeOrWrong:
            "Unsafe or wrong"
        }
    }
}

public enum FeedbackActionTaken: String, Codable, CaseIterable, Identifiable, Sendable {
    case followed
    case partiallyFollowed = "partially_followed"
    case ignored
    case planned

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .followed:
            "Followed"
        case .partiallyFollowed:
            "Partially followed"
        case .ignored:
            "Ignored"
        case .planned:
            "Planned"
        }
    }
}

public struct RecommendationFeedbackRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var rating: FeedbackRating
    public var actionTaken: FeedbackActionTaken
    public var reason: String?
    public var outcomeNotes: String?

    public init(
        rating: FeedbackRating,
        actionTaken: FeedbackActionTaken,
        reason: String? = nil,
        outcomeNotes: String? = nil
    ) {
        self.rating = rating
        self.actionTaken = actionTaken
        self.reason = reason
        self.outcomeNotes = outcomeNotes
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case rating
        case actionTaken = "action_taken"
        case reason
        case outcomeNotes = "outcome_notes"
    }
}

public struct FeedbackContradictionAlert: Codable, Equatable, Sendable {
    public var contradictionKey: String
    public var count: Int
    public var message: String

    enum CodingKeys: String, CodingKey {
        case contradictionKey = "contradiction_key"
        case count
        case message
    }
}

public struct RecommendationFeedbackResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var feedbackID: UUID
    public var memoryUpdateStatus: String
    public var evalQueueStatus: String
    public var contradictionAlert: FeedbackContradictionAlert?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case feedbackID = "feedback_id"
        case memoryUpdateStatus = "memory_update_status"
        case evalQueueStatus = "eval_queue_status"
        case contradictionAlert = "contradiction_alert"
    }
}

public struct LLMSettingsResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var provider: String
    public var cheapModel: String
    public var strongModel: String
    public var fallbackModel: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case provider
        case cheapModel = "cheap_model"
        case strongModel = "strong_model"
        case fallbackModel = "fallback_model"
    }
}

public struct ModelDisclosureRecord: Codable, Equatable, Sendable {
    public var runID: UUID
    public var createdAt: String
    public var runType: String
    public var provider: String
    public var model: String
    public var promptVersion: String
    public var schemaVersion: String
    public var inputHash: String
    public var payloadMetadata: [String: PrivacyJSONValue]

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case createdAt = "created_at"
        case runType = "run_type"
        case provider
        case model
        case promptVersion = "prompt_version"
        case schemaVersion = "schema_version"
        case inputHash = "input_hash"
        case payloadMetadata = "payload_metadata"
    }
}

public struct ModelDisclosureResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var runs: [ModelDisclosureRecord]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case runs
    }
}

public enum BriefingPrivacyMode: String, Codable, Sendable {
    case localOnly = "local_only"
    case cloudAssisted = "cloud_assisted"
    case hybrid

    public init(_ privacyMode: PrivacyMode) {
        switch privacyMode {
        case .localOnly:
            self = .localOnly
        case .cloudAssisted:
            self = .cloudAssisted
        case .hybrid:
            self = .hybrid
        }
    }
}

public struct DailyAnalysisRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var date: String
    public var forceRecompute: Bool
    public var includeExternalKnowledge: Bool
    public var privacyMode: BriefingPrivacyMode

    public init(
        date: String,
        forceRecompute: Bool = false,
        includeExternalKnowledge: Bool = false,
        privacyMode: BriefingPrivacyMode
    ) {
        self.date = date
        self.forceRecompute = forceRecompute
        self.includeExternalKnowledge = includeExternalKnowledge
        self.privacyMode = privacyMode
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case date
        case forceRecompute = "force_recompute"
        case includeExternalKnowledge = "include_external_knowledge"
        case privacyMode = "privacy_mode"
    }
}

public struct DailyAnalysisResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var analysisJobID: UUID
    public var status: String
    public var estimatedCompletionSeconds: Int

    public init(
        schemaVersion: String = "v1",
        analysisJobID: UUID,
        status: String,
        estimatedCompletionSeconds: Int
    ) {
        self.schemaVersion = schemaVersion
        self.analysisJobID = analysisJobID
        self.status = status
        self.estimatedCompletionSeconds = estimatedCompletionSeconds
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case analysisJobID = "analysis_job_id"
        case status
        case estimatedCompletionSeconds = "estimated_completion_seconds"
    }
}

public struct DataFreshness: Codable, Equatable, Sendable {
    public var latestSampleAt: String?
    public var latestCheckInDate: String?
    public var staleSources: [String]

    public init(
        latestSampleAt: String? = nil,
        latestCheckInDate: String? = nil,
        staleSources: [String] = []
    ) {
        self.latestSampleAt = latestSampleAt
        self.latestCheckInDate = latestCheckInDate
        self.staleSources = staleSources
    }

    enum CodingKeys: String, CodingKey {
        case latestSampleAt = "latest_sample_at"
        case latestCheckInDate = "latest_checkin_date"
        case staleSources = "stale_sources"
    }
}

public enum BriefingValue: Codable, Equatable, Sendable {
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else {
            self = try .string(container.decode(String.self))
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .bool(let value):
            try container.encode(value)
        case .int(let value):
            try container.encode(value)
        case .double(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        }
    }

    public var displayText: String {
        switch self {
        case .bool(let value):
            value ? "true" : "false"
        case .int(let value):
            String(value)
        case .double(let value):
            value.formatted()
        case .string(let value):
            value
        }
    }
}

public struct PersonalEvidence: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(metric)-\(source ?? interpretation)" }
    public var metric: String
    public var value: BriefingValue
    public var interpretation: String
    public var source: String?

    public init(metric: String, value: BriefingValue, interpretation: String, source: String? = nil) {
        self.metric = metric
        self.value = value
        self.interpretation = interpretation
        self.source = source
    }
}

public struct MemoryObservation: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(observation)-\(period ?? relevance)" }
    public var observation: String
    public var relevance: String
    public var period: String?

    public init(observation: String, relevance: String, period: String? = nil) {
        self.observation = observation
        self.relevance = relevance
        self.period = period
    }
}

public struct ExternalCitation: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(title)-\(source)" }
    public var title: String
    public var source: String
    public var url: String?
    public var citedClaim: String

    public init(title: String, source: String, url: String? = nil, citedClaim: String) {
        self.title = title
        self.source = source
        self.url = url
        self.citedClaim = citedClaim
    }

    enum CodingKeys: String, CodingKey {
        case title
        case source
        case url
        case citedClaim = "cited_claim"
    }
}

public struct RecommendationSummary: Codable, Equatable, Sendable {
    public var primary: String
    public var avoid: String?

    public init(primary: String, avoid: String? = nil) {
        self.primary = primary
        self.avoid = avoid
    }
}

public struct CandidateOption: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(label)-\(recommendationBand)" }
    public var label: String
    public var recommendationBand: String
    public var rationale: String

    public init(label: String, recommendationBand: String, rationale: String) {
        self.label = label
        self.recommendationBand = recommendationBand
        self.rationale = rationale
    }

    enum CodingKeys: String, CodingKey {
        case label
        case recommendationBand = "recommendation_band"
        case rationale
    }
}

public struct GoalTradeoff: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(goal)-\(tradeoff)" }
    public var goal: String
    public var tradeoff: String
    public var indicatorStatus: String?
    public var evidenceRefs: [String]
    public var missingData: [String]

    public init(
        goal: String,
        tradeoff: String,
        indicatorStatus: String? = nil,
        evidenceRefs: [String] = [],
        missingData: [String] = []
    ) {
        self.goal = goal
        self.tradeoff = tradeoff
        self.indicatorStatus = indicatorStatus
        self.evidenceRefs = evidenceRefs
        self.missingData = missingData
    }

    enum CodingKeys: String, CodingKey {
        case goal
        case tradeoff
        case indicatorStatus = "indicator_status"
        case evidenceRefs = "evidence_refs"
        case missingData = "missing_data"
    }
}

public struct DataQualityNote: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(metric ?? "general")-\(note)" }
    public var metric: String?
    public var note: String
    public var severity: String

    public init(metric: String? = nil, note: String, severity: String = "info") {
        self.metric = metric
        self.note = note
        self.severity = severity
    }
}

public struct DataQualitySummary: Codable, Equatable, Sendable {
    public var status: String
    public var notes: [DataQualityNote]

    public init(status: String, notes: [DataQualityNote] = []) {
        self.status = status
        self.notes = notes
    }

    enum CodingKeys: String, CodingKey {
        case status
        case notes
    }
}

public struct RecommendationAlternative: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(label)-\(rationale)" }
    public var label: String
    public var rationale: String

    public init(label: String, rationale: String) {
        self.label = label
        self.rationale = rationale
    }
}

public struct FollowUpPrompt: Codable, Equatable, Sendable {
    public var question: String
    public var reason: String

    public init(question: String, reason: String) {
        self.question = question
        self.reason = reason
    }
}

public struct BriefingTraceInspection: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var traceID: UUID
    public var dataFreshness: DataFreshness?
    public var featureValues: [PersonalEvidence]
    public var rulesFired: [String]
    public var retrievedMemory: [MemoryObservation]
    public var externalSources: [ExternalCitation]
    public var modelMetadata: [String: String]

    public init(
        schemaVersion: String = "v1",
        traceID: UUID,
        dataFreshness: DataFreshness? = nil,
        featureValues: [PersonalEvidence] = [],
        rulesFired: [String] = [],
        retrievedMemory: [MemoryObservation] = [],
        externalSources: [ExternalCitation] = [],
        modelMetadata: [String: String] = [:]
    ) {
        self.schemaVersion = schemaVersion
        self.traceID = traceID
        self.dataFreshness = dataFreshness
        self.featureValues = featureValues
        self.rulesFired = rulesFired
        self.retrievedMemory = retrievedMemory
        self.externalSources = externalSources
        self.modelMetadata = modelMetadata
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case traceID = "trace_id"
        case dataFreshness = "data_freshness"
        case featureValues = "feature_values"
        case rulesFired = "rules_fired"
        case retrievedMemory = "retrieved_memory"
        case externalSources = "external_sources"
        case modelMetadata = "model_metadata"
    }
}

public struct DailyBriefingResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var date: String
    public var readinessState: String
    public var confidence: String
    public var dataFreshness: DataFreshness
    public var evidence: [PersonalEvidence]
    public var memoryObservations: [MemoryObservation]
    public var externalCitations: [ExternalCitation]
    public var riskFlags: [String]
    public var recommendation: RecommendationSummary
    public var recommendationBand: String
    public var candidateOptions: [CandidateOption]
    public var goalTradeoffs: [GoalTradeoff]
    public var uncertainty: [String]
    public var dataQualityNotes: [DataQualityNote]
    public var whatWouldChangeMyMind: [String]
    public var alternatives: [RecommendationAlternative]
    public var followUp: FollowUpPrompt?
    public var safetyStatus: String
    public var safetyNotes: [String]
    public var traceID: UUID
    public var generatedAt: String
    public var recommendationID: UUID?
    public var trace: BriefingTraceInspection?

    public init(
        schemaVersion: String = "v1",
        date: String,
        readinessState: String,
        confidence: String,
        dataFreshness: DataFreshness,
        evidence: [PersonalEvidence],
        memoryObservations: [MemoryObservation] = [],
        externalCitations: [ExternalCitation] = [],
        riskFlags: [String] = [],
        recommendation: RecommendationSummary,
        recommendationBand: String,
        candidateOptions: [CandidateOption] = [],
        goalTradeoffs: [GoalTradeoff] = [],
        uncertainty: [String],
        dataQualityNotes: [DataQualityNote] = [],
        whatWouldChangeMyMind: [String] = [],
        alternatives: [RecommendationAlternative] = [],
        followUp: FollowUpPrompt? = nil,
        safetyStatus: String = "passed",
        safetyNotes: [String],
        traceID: UUID,
        generatedAt: String,
        recommendationID: UUID? = nil,
        trace: BriefingTraceInspection? = nil
    ) {
        self.schemaVersion = schemaVersion
        self.date = date
        self.readinessState = readinessState
        self.confidence = confidence
        self.dataFreshness = dataFreshness
        self.evidence = evidence
        self.memoryObservations = memoryObservations
        self.externalCitations = externalCitations
        self.riskFlags = riskFlags
        self.recommendation = recommendation
        self.recommendationBand = recommendationBand
        self.candidateOptions = candidateOptions
        self.goalTradeoffs = goalTradeoffs
        self.uncertainty = uncertainty
        self.dataQualityNotes = dataQualityNotes
        self.whatWouldChangeMyMind = whatWouldChangeMyMind
        self.alternatives = alternatives
        self.followUp = followUp
        self.safetyStatus = safetyStatus
        self.safetyNotes = safetyNotes
        self.traceID = traceID
        self.generatedAt = generatedAt
        self.recommendationID = recommendationID
        self.trace = trace
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case date
        case readinessState = "readiness_state"
        case confidence
        case dataFreshness = "data_freshness"
        case evidence
        case memoryObservations = "memory_observations"
        case externalCitations = "external_citations"
        case riskFlags = "risk_flags"
        case recommendation
        case recommendationBand = "recommendation_band"
        case candidateOptions = "candidate_options"
        case goalTradeoffs = "goal_tradeoffs"
        case uncertainty
        case dataQualityNotes = "data_quality_notes"
        case whatWouldChangeMyMind = "what_would_change_my_mind"
        case alternatives
        case followUp = "follow_up"
        case safetyStatus = "safety_status"
        case safetyNotes = "safety_notes"
        case traceID = "trace_id"
        case generatedAt = "generated_at"
        case recommendationID = "recommendation_id"
        case trace
    }

    public var isDeterministicFallback: Bool {
        trace?.modelMetadata["briefing_generation_status"] == "degraded"
    }
}

public struct AssistantQueryRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var question: String
    public var dateContext: String?
    public var allowedDataScope: [String]
    public var includeExternalKnowledge: Bool
    public var privacyMode: BriefingPrivacyMode

    public init(
        question: String,
        dateContext: String?,
        allowedDataScope: [String] = ["briefing_trace", "recent_health"],
        includeExternalKnowledge: Bool = false,
        privacyMode: BriefingPrivacyMode
    ) {
        self.question = question
        self.dateContext = dateContext
        self.allowedDataScope = allowedDataScope
        self.includeExternalKnowledge = includeExternalKnowledge
        self.privacyMode = privacyMode
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case question
        case dateContext = "date_context"
        case allowedDataScope = "allowed_data_scope"
        case includeExternalKnowledge = "include_external_knowledge"
        case privacyMode = "privacy_mode"
    }
}

public struct AssistantQueryResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var answer: String
    public var personalEvidence: [PersonalEvidence]
    public var externalSources: [ExternalCitation]
    public var confidence: String
    public var uncertainty: [String]
    public var safetyStatus: String
    public var traceID: UUID

    public init(
        schemaVersion: String = "v1",
        answer: String,
        personalEvidence: [PersonalEvidence],
        externalSources: [ExternalCitation] = [],
        confidence: String,
        uncertainty: [String],
        safetyStatus: String,
        traceID: UUID
    ) {
        self.schemaVersion = schemaVersion
        self.answer = answer
        self.personalEvidence = personalEvidence
        self.externalSources = externalSources
        self.confidence = confidence
        self.uncertainty = uncertainty
        self.safetyStatus = safetyStatus
        self.traceID = traceID
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case answer
        case personalEvidence = "personal_evidence"
        case externalSources = "external_sources"
        case confidence
        case uncertainty
        case safetyStatus = "safety_status"
        case traceID = "trace_id"
    }
}

public enum SensitiveNotePolicy: String, Codable, Sendable {
    case excludeFromExternalLLM = "exclude_from_external_llm"
    case summarizeBeforeExternalLLM = "summarize_before_external_llm"
    case allowExternalLLM = "allow_external_llm"
}

public enum RedactionStatus: String, Codable, Sendable {
    case redacted
    case partial
    case none
}

public struct DailyCheckInFlags: Codable, Equatable, Sendable {
    public var alcohol: Bool
    public var caffeineNotes: String?
    public var illness: Bool
    public var injury: Bool
    public var travel: Bool

    public init(
        alcohol: Bool = false,
        caffeineNotes: String? = nil,
        illness: Bool = false,
        injury: Bool = false,
        travel: Bool = false
    ) {
        self.alcohol = alcohol
        self.caffeineNotes = caffeineNotes
        self.illness = illness
        self.injury = injury
        self.travel = travel
    }

    enum CodingKeys: String, CodingKey {
        case alcohol
        case caffeineNotes = "caffeine_notes"
        case illness
        case injury
        case travel
    }
}

public enum StructuredNoteValue: Codable, Equatable, Sendable {
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else {
            self = try .string(container.decode(String.self))
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .bool(let value):
            try container.encode(value)
        case .int(let value):
            try container.encode(value)
        case .double(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        }
    }
}

public struct DailyCheckInRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var date: String
    public var energyScore: Int?
    public var moodScore: Int?
    public var sorenessScore: Int?
    public var stressScore: Int?
    public var perceivedRecoveryScore: Int?
    public var foodQualityScore: Int?
    public var flags: DailyCheckInFlags
    public var structuredNotes: [String: StructuredNoteValue]
    public var freeTextNote: String?
    public var sensitiveNotePolicy: SensitiveNotePolicy
    public var encodesFlags: Bool
    public var encodesStructuredNotes: Bool
    public var encodesFreeTextNote: Bool

    public init(
        date: String,
        energyScore: Int? = nil,
        moodScore: Int? = nil,
        sorenessScore: Int? = nil,
        stressScore: Int? = nil,
        perceivedRecoveryScore: Int? = nil,
        foodQualityScore: Int? = nil,
        flags: DailyCheckInFlags = DailyCheckInFlags(),
        structuredNotes: [String: StructuredNoteValue] = [:],
        freeTextNote: String? = nil,
        sensitiveNotePolicy: SensitiveNotePolicy = .excludeFromExternalLLM,
        encodesFlags: Bool = true,
        encodesStructuredNotes: Bool = true,
        encodesFreeTextNote: Bool = false
    ) {
        self.date = date
        self.energyScore = energyScore
        self.moodScore = moodScore
        self.sorenessScore = sorenessScore
        self.stressScore = stressScore
        self.perceivedRecoveryScore = perceivedRecoveryScore
        self.foodQualityScore = foodQualityScore
        self.flags = flags
        self.structuredNotes = structuredNotes
        self.freeTextNote = freeTextNote
        self.sensitiveNotePolicy = sensitiveNotePolicy
        self.encodesFlags = encodesFlags
        self.encodesStructuredNotes = encodesStructuredNotes
        self.encodesFreeTextNote = encodesFreeTextNote
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case date
        case energyScore = "energy_score"
        case moodScore = "mood_score"
        case sorenessScore = "soreness_score"
        case stressScore = "stress_score"
        case perceivedRecoveryScore = "perceived_recovery_score"
        case foodQualityScore = "food_quality_score"
        case flags
        case structuredNotes = "structured_notes"
        case freeTextNote = "free_text_note"
        case sensitiveNotePolicy = "sensitive_note_policy"
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(String.self, forKey: .schemaVersion) ?? "v1"
        date = try container.decode(String.self, forKey: .date)
        energyScore = try container.decodeIfPresent(Int.self, forKey: .energyScore)
        moodScore = try container.decodeIfPresent(Int.self, forKey: .moodScore)
        sorenessScore = try container.decodeIfPresent(Int.self, forKey: .sorenessScore)
        stressScore = try container.decodeIfPresent(Int.self, forKey: .stressScore)
        perceivedRecoveryScore = try container.decodeIfPresent(
            Int.self,
            forKey: .perceivedRecoveryScore
        )
        foodQualityScore = try container.decodeIfPresent(Int.self, forKey: .foodQualityScore)
        flags = try container.decodeIfPresent(DailyCheckInFlags.self, forKey: .flags)
            ?? DailyCheckInFlags()
        structuredNotes = try container.decodeIfPresent(
            [String: StructuredNoteValue].self,
            forKey: .structuredNotes
        ) ?? [:]
        freeTextNote = try container.decodeIfPresent(String.self, forKey: .freeTextNote)
        sensitiveNotePolicy = try container.decodeIfPresent(
            SensitiveNotePolicy.self,
            forKey: .sensitiveNotePolicy
        ) ?? .excludeFromExternalLLM
        encodesFlags = true
        encodesStructuredNotes = true
        encodesFreeTextNote = false
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(date, forKey: .date)
        try container.encodeIfPresent(energyScore, forKey: .energyScore)
        try container.encodeIfPresent(moodScore, forKey: .moodScore)
        try container.encodeIfPresent(sorenessScore, forKey: .sorenessScore)
        try container.encodeIfPresent(stressScore, forKey: .stressScore)
        try container.encodeIfPresent(perceivedRecoveryScore, forKey: .perceivedRecoveryScore)
        try container.encodeIfPresent(foodQualityScore, forKey: .foodQualityScore)
        if encodesFlags {
            try container.encode(flags, forKey: .flags)
        }
        if encodesStructuredNotes {
            try container.encode(structuredNotes, forKey: .structuredNotes)
        }
        if encodesFreeTextNote {
            try container.encode(freeTextNote, forKey: .freeTextNote)
        } else {
            try container.encodeIfPresent(freeTextNote, forKey: .freeTextNote)
        }
        try container.encode(sensitiveNotePolicy, forKey: .sensitiveNotePolicy)
    }
}

public struct DailyCheckInResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var checkinID: UUID
    public var acceptedFields: [String]
    public var redactionStatus: RedactionStatus
    public var analysisJobID: UUID?

    public init(
        schemaVersion: String = "v1",
        checkinID: UUID,
        acceptedFields: [String],
        redactionStatus: RedactionStatus,
        analysisJobID: UUID? = nil
    ) {
        self.schemaVersion = schemaVersion
        self.checkinID = checkinID
        self.acceptedFields = acceptedFields
        self.redactionStatus = redactionStatus
        self.analysisJobID = analysisJobID
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case checkinID = "checkin_id"
        case acceptedFields = "accepted_fields"
        case redactionStatus = "redaction_status"
        case analysisJobID = "analysis_job_id"
    }
}

public struct DailyCheckInDetailResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var checkinID: UUID
    public var request: DailyCheckInRequest
    public var hasFreeTextNote: Bool

    public init(
        schemaVersion: String = "v1",
        checkinID: UUID,
        request: DailyCheckInRequest,
        hasFreeTextNote: Bool = false
    ) {
        self.schemaVersion = schemaVersion
        self.checkinID = checkinID
        self.request = request
        self.hasFreeTextNote = hasFreeTextNote
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case checkinID = "checkin_id"
        case request
        case hasFreeTextNote = "has_free_text_note"
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(String.self, forKey: .schemaVersion) ?? "v1"
        checkinID = try container.decode(UUID.self, forKey: .checkinID)
        request = try container.decode(DailyCheckInRequest.self, forKey: .request)
        hasFreeTextNote = try container.decodeIfPresent(Bool.self, forKey: .hasFreeTextNote) ?? false
    }
}

public enum GoalCategory: String, CaseIterable, Codable, Identifiable, Sendable {
    case cognitivePerformance = "cognitive_performance"
    case vo2Max = "vo2_max"
    case strength
    case recovery
    case sleep
    case longTermWellness = "long_term_wellness"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .cognitivePerformance:
            "Cognitive performance"
        case .vo2Max:
            "VO2 max"
        case .strength:
            "Strength"
        case .recovery:
            "Recovery"
        case .sleep:
            "Sleep"
        case .longTermWellness:
            "Long-term wellness"
        }
    }
}

public enum GoalTimeHorizon: String, CaseIterable, Codable, Identifiable, Sendable {
    case shortTerm = "short_term"
    case mediumTerm = "medium_term"
    case longTerm = "long_term"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .shortTerm:
            "Short-term"
        case .mediumTerm:
            "Medium-term"
        case .longTerm:
            "Long-term"
        }
    }
}

public struct GoalRequest: Codable, Equatable, Sendable {
    public var schemaVersion = "v1"
    public var category: GoalCategory
    public var priority: Int
    public var timeHorizon: GoalTimeHorizon
    public var successMetric: String
    public var constraints: [String: String]

    public init(
        category: GoalCategory,
        priority: Int,
        timeHorizon: GoalTimeHorizon,
        successMetric: String,
        constraints: [String: String] = [:]
    ) {
        self.category = category
        self.priority = priority
        self.timeHorizon = timeHorizon
        self.successMetric = successMetric
        self.constraints = constraints
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case category
        case priority
        case timeHorizon = "time_horizon"
        case successMetric = "success_metric"
        case constraints
    }
}

public struct GoalResponse: Codable, Equatable, Identifiable, Sendable {
    public var schemaVersion: String
    public var id: UUID
    public var category: GoalCategory
    public var priority: Int
    public var timeHorizon: GoalTimeHorizon
    public var successMetric: String
    public var constraints: [String: String]
    public var active: Bool

    public init(
        schemaVersion: String = "v1",
        id: UUID,
        category: GoalCategory,
        priority: Int,
        timeHorizon: GoalTimeHorizon,
        successMetric: String,
        constraints: [String: String] = [:],
        active: Bool = true
    ) {
        self.schemaVersion = schemaVersion
        self.id = id
        self.category = category
        self.priority = priority
        self.timeHorizon = timeHorizon
        self.successMetric = successMetric
        self.constraints = constraints
        self.active = active
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case id
        case category
        case priority
        case timeHorizon = "time_horizon"
        case successMetric = "success_metric"
        case constraints
        case active
    }
}

public struct HealthSyncResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var syncID: UUID
    public var acceptedCount: Int
    public var duplicateCount: Int
    public var rejectedCount: Int
    public var warnings: [String]
    public var nextAnchor: String
    public var dataQualitySummary: DataQualitySummary

    public init(
        schemaVersion: String = "v1",
        syncID: UUID,
        acceptedCount: Int,
        duplicateCount: Int,
        rejectedCount: Int,
        warnings: [String],
        nextAnchor: String,
        dataQualitySummary: DataQualitySummary
    ) {
        self.schemaVersion = schemaVersion
        self.syncID = syncID
        self.acceptedCount = acceptedCount
        self.duplicateCount = duplicateCount
        self.rejectedCount = rejectedCount
        self.warnings = warnings
        self.nextAnchor = nextAnchor
        self.dataQualitySummary = dataQualitySummary
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case syncID = "sync_id"
        case acceptedCount = "accepted_count"
        case duplicateCount = "duplicate_count"
        case rejectedCount = "rejected_count"
        case warnings
        case nextAnchor = "next_anchor"
        case dataQualitySummary = "data_quality_summary"
    }
}

public enum MemoryPeriodType: String, Codable, CaseIterable, Identifiable, Sendable {
    case daily
    case weekly
    case monthly
    case quarterly

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .daily:
            "Daily"
        case .weekly:
            "Weekly"
        case .monthly:
            "Monthly"
        case .quarterly:
            "Quarterly"
        }
    }
}

public struct MemorySummaryItem: Codable, Equatable, Identifiable, Sendable {
    public var id: UUID { memorySummaryID }
    public var memorySummaryID: UUID
    public var periodType: MemoryPeriodType
    public var startDate: String
    public var endDate: String
    public var summaryVersion: String
    public var confidence: Double
    public var observations: [MemorySummaryEntry]
    public var hypotheses: [MemorySummaryEntry]
    public var sourceRefs: [MemorySourceRef]
    public var sensitiveFieldsExcluded: [String]

    public init(
        memorySummaryID: UUID,
        periodType: MemoryPeriodType,
        startDate: String,
        endDate: String,
        summaryVersion: String,
        confidence: Double,
        observations: [MemorySummaryEntry] = [],
        hypotheses: [MemorySummaryEntry] = [],
        sourceRefs: [MemorySourceRef] = [],
        sensitiveFieldsExcluded: [String] = []
    ) {
        self.memorySummaryID = memorySummaryID
        self.periodType = periodType
        self.startDate = startDate
        self.endDate = endDate
        self.summaryVersion = summaryVersion
        self.confidence = confidence
        self.observations = observations
        self.hypotheses = hypotheses
        self.sourceRefs = sourceRefs
        self.sensitiveFieldsExcluded = sensitiveFieldsExcluded
    }

    enum CodingKeys: String, CodingKey {
        case memorySummaryID = "memory_summary_id"
        case periodType = "period_type"
        case startDate = "start_date"
        case endDate = "end_date"
        case summaryVersion = "summary_version"
        case confidence
        case observations
        case hypotheses
        case sourceRefs = "source_refs"
        case sensitiveFieldsExcluded = "sensitive_fields_excluded"
    }
}

public struct MemorySummaryEntry: Codable, Equatable, Sendable {
    public var text: String?
    public var metric: String?
    public var summary: String?
    public var confidence: Double?
    public var sourceRefs: [MemorySourceRef]?

    public init(
        text: String? = nil,
        metric: String? = nil,
        summary: String? = nil,
        confidence: Double? = nil,
        sourceRefs: [MemorySourceRef]? = nil
    ) {
        self.text = text
        self.metric = metric
        self.summary = summary
        self.confidence = confidence
        self.sourceRefs = sourceRefs
    }
}

public struct MemorySourceRef: Codable, Equatable, Sendable {
    public var table: String?
    public var id: String?
    public var field: String?

    public init(table: String? = nil, id: String? = nil, field: String? = nil) {
        self.table = table
        self.id = id
        self.field = field
    }
}

public struct MemorySummaryListResponse: Codable, Equatable, Sendable {
    public var schemaVersion: String
    public var summaries: [MemorySummaryItem]

    public init(schemaVersion: String = "v1", summaries: [MemorySummaryItem] = []) {
        self.schemaVersion = schemaVersion
        self.summaries = summaries
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case summaries
    }
}

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

public struct ConsentRecord: Codable, Equatable, Sendable {
    public static let currentVersion = "p1-04-v1"

    public var consentVersion: String
    public var grantedAt: Date
    public var enabledCategories: [HealthCategory]
    public var deniedCategories: [HealthCategory]
    public var processingMode: PrivacyMode

    public init(
        consentVersion: String = ConsentRecord.currentVersion,
        grantedAt: Date = Date(),
        enabledCategories: [HealthCategory],
        deniedCategories: [HealthCategory] = [],
        processingMode: PrivacyMode
    ) {
        self.consentVersion = consentVersion
        self.grantedAt = grantedAt
        self.enabledCategories = enabledCategories
        self.deniedCategories = deniedCategories
        self.processingMode = processingMode
    }

    enum CodingKeys: String, CodingKey {
        case consentVersion
        case grantedAt
        case enabledCategories
        case deniedCategories
        case processingMode
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        consentVersion = try container.decode(String.self, forKey: .consentVersion)
        grantedAt = try container.decode(Date.self, forKey: .grantedAt)
        enabledCategories = try container.decode([HealthCategory].self, forKey: .enabledCategories)
        deniedCategories = try container.decodeIfPresent([HealthCategory].self, forKey: .deniedCategories) ?? []
        processingMode = try container.decode(PrivacyMode.self, forKey: .processingMode)
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

    public init(
        schemaVersion: String = "v1",
        syncID: UUID,
        acceptedCount: Int,
        duplicateCount: Int,
        rejectedCount: Int,
        warnings: [String],
        nextAnchor: String
    ) {
        self.schemaVersion = schemaVersion
        self.syncID = syncID
        self.acceptedCount = acceptedCount
        self.duplicateCount = duplicateCount
        self.rejectedCount = rejectedCount
        self.warnings = warnings
        self.nextAnchor = nextAnchor
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case syncID = "sync_id"
        case acceptedCount = "accepted_count"
        case duplicateCount = "duplicate_count"
        case rejectedCount = "rejected_count"
        case warnings
        case nextAnchor = "next_anchor"
    }
}

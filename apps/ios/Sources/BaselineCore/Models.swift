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

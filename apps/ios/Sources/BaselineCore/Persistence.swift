import Foundation

public struct CategoryAnchor: Codable, Equatable, Sendable {
    public var healthKitAnchorData: Data?
    public var serverAnchor: String?
    public var lastSyncedAt: Date?

    public init(
        healthKitAnchorData: Data? = nil,
        serverAnchor: String? = nil,
        lastSyncedAt: Date? = nil
    ) {
        self.healthKitAnchorData = healthKitAnchorData
        self.serverAnchor = serverAnchor
        self.lastSyncedAt = lastSyncedAt
    }
}

public struct PendingSyncBatch: Codable, Equatable, Sendable {
    public var request: HealthSyncRequest
    public var anchorsAfterQuery: [HealthCategory: Data?]
    public var skippedCategories: [HealthCategory]
    public var createdAt: Date

    public init(
        request: HealthSyncRequest,
        anchorsAfterQuery: [HealthCategory: Data?],
        skippedCategories: [HealthCategory] = [],
        createdAt: Date = Date()
    ) {
        self.request = request
        self.anchorsAfterQuery = anchorsAfterQuery
        self.skippedCategories = skippedCategories
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case request
        case anchorsAfterQuery
        case skippedCategories
        case createdAt
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        request = try container.decode(HealthSyncRequest.self, forKey: .request)
        anchorsAfterQuery = try container.decode([HealthCategory: Data?].self, forKey: .anchorsAfterQuery)
        skippedCategories = try container.decodeIfPresent([HealthCategory].self, forKey: .skippedCategories) ?? []
        createdAt = try container.decode(Date.self, forKey: .createdAt)
    }
}

public protocol AnchorPersisting: Sendable {
    func loadAnchor(for category: HealthCategory) throws -> CategoryAnchor?
    func saveAnchor(_ anchor: CategoryAnchor, for category: HealthCategory) throws
    func loadPendingBatch() throws -> PendingSyncBatch?
    func savePendingBatch(_ batch: PendingSyncBatch) throws
    func clearPendingBatch() throws
}

public protocol ConsentPersisting: Sendable {
    func loadConsent() throws -> ConsentRecord?
    func saveConsent(_ consent: ConsentRecord) throws
}

public protocol BriefingPersisting: Sendable {
    func loadLatestBriefing() throws -> DailyBriefingResponse?
    func saveLatestBriefing(_ briefing: DailyBriefingResponse) throws
}

public struct RestoredLaunchState: Equatable, Sendable {
    public var consent: ConsentRecord
    public var lastSyncedAt: Date?

    public init(consent: ConsentRecord, lastSyncedAt: Date?) {
        self.consent = consent
        self.lastSyncedAt = lastSyncedAt
    }
}

public enum LaunchStateRestorer {
    public static func restore(
        consentStore: any ConsentPersisting,
        anchorStore: any AnchorPersisting
    ) throws -> RestoredLaunchState? {
        guard let consent = try consentStore.loadConsent() else {
            return nil
        }
        let lastSyncedAt = try consent.enabledCategories.compactMap { category in
            try anchorStore.loadAnchor(for: category)?.lastSyncedAt
        }.max()
        return RestoredLaunchState(consent: consent, lastSyncedAt: lastSyncedAt)
    }
}

public final class FileAnchorStore: AnchorPersisting {
    private let rootURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(rootURL: URL) throws {
        self.rootURL = rootURL
        encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        try FileManager.default.createDirectory(
            at: rootURL,
            withIntermediateDirectories: true
        )
        try protectFile(at: rootURL)
    }

    public func loadAnchor(for category: HealthCategory) throws -> CategoryAnchor? {
        try load(CategoryAnchor.self, from: anchorURL(for: category))
    }

    public func saveAnchor(_ anchor: CategoryAnchor, for category: HealthCategory) throws {
        try save(anchor, to: anchorURL(for: category))
    }

    public func loadPendingBatch() throws -> PendingSyncBatch? {
        try load(PendingSyncBatch.self, from: pendingURL)
    }

    public func savePendingBatch(_ batch: PendingSyncBatch) throws {
        try save(batch, to: pendingURL)
    }

    public func clearPendingBatch() throws {
        guard FileManager.default.fileExists(atPath: pendingURL.path) else {
            return
        }
        try FileManager.default.removeItem(at: pendingURL)
    }

    private var pendingURL: URL {
        rootURL.appendingPathComponent("pending-sync.json", isDirectory: false)
    }

    private func anchorURL(for category: HealthCategory) -> URL {
        rootURL.appendingPathComponent("\(category.rawValue)-anchor.json", isDirectory: false)
    }

    private func load<T: Decodable>(_ type: T.Type, from url: URL) throws -> T? {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        let data = try Data(contentsOf: url)
        return try decoder.decode(type, from: data)
    }

    private func save<T: Encodable>(_ value: T, to url: URL) throws {
        let data = try encoder.encode(value)
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtection])
        #else
        try data.write(to: url, options: [.atomic])
        #endif
        try protectFile(at: url)
    }
}

public final class FileConsentStore: ConsentPersisting {
    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(rootURL: URL) throws {
        fileURL = rootURL.appendingPathComponent("consent.json", isDirectory: false)
        encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        try FileManager.default.createDirectory(
            at: rootURL,
            withIntermediateDirectories: true
        )
        try protectFile(at: rootURL)
    }

    public func loadConsent() throws -> ConsentRecord? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return nil
        }
        let data = try Data(contentsOf: fileURL)
        return try decoder.decode(ConsentRecord.self, from: data)
    }

    public func saveConsent(_ consent: ConsentRecord) throws {
        let data = try encoder.encode(consent)
        #if os(iOS)
        try data.write(to: fileURL, options: [.atomic, .completeFileProtection])
        #else
        try data.write(to: fileURL, options: [.atomic])
        #endif
        try protectFile(at: fileURL)
    }
}

public final class FileBriefingStore: BriefingPersisting {
    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(rootURL: URL) throws {
        fileURL = rootURL.appendingPathComponent("latest-briefing.json", isDirectory: false)
        encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        decoder = JSONDecoder()
        try FileManager.default.createDirectory(
            at: rootURL,
            withIntermediateDirectories: true
        )
        try protectFile(at: rootURL)
    }

    public func loadLatestBriefing() throws -> DailyBriefingResponse? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return nil
        }
        let data = try Data(contentsOf: fileURL)
        return try decoder.decode(DailyBriefingResponse.self, from: data)
    }

    public func saveLatestBriefing(_ briefing: DailyBriefingResponse) throws {
        let data = try encoder.encode(briefing)
        #if os(iOS)
        try data.write(to: fileURL, options: [.atomic, .completeFileProtection])
        #else
        try data.write(to: fileURL, options: [.atomic])
        #endif
        try protectFile(at: fileURL)
    }
}

private func protectFile(at url: URL) throws {
    #if os(iOS)
    try FileManager.default.setAttributes(
        [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
        ofItemAtPath: url.path
    )
    #endif
}

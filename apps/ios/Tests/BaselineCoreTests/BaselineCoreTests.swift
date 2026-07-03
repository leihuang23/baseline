import Foundation
import XCTest
@testable import BaselineCore

final class BaselineCoreTests: XCTestCase {
    func testFileAnchorStorePersistsAnchorsAndPendingBatch() throws {
        let directory = try temporaryDirectory()
        let store = try FileAnchorStore(rootURL: directory)
        let anchor = CategoryAnchor(
            healthKitAnchorData: Data("hk-anchor".utf8),
            serverAnchor: "server-anchor",
            lastSyncedAt: Date(timeIntervalSince1970: 1_800)
        )

        try store.saveAnchor(anchor, for: .heartRateVariability)

        XCTAssertEqual(try store.loadAnchor(for: .heartRateVariability), anchor)
        XCTAssertNil(try store.loadAnchor(for: .sleep))

        let request = HealthSyncRequest(
            clientSyncID: "sync-1",
            deviceID: "device-1",
            timezone: "UTC",
            samples: [sample("hrv-1", category: .heartRateVariability)],
            lastAnchor: "server-anchor",
            consentVersion: "p1-04-v1"
        )
        let pending = PendingSyncBatch(
            request: request,
            anchorsAfterQuery: [.heartRateVariability: Data("hk-next".utf8)],
            skippedCategories: [.sleep],
            createdAt: Date(timeIntervalSince1970: 2_000)
        )

        try store.savePendingBatch(pending)
        XCTAssertEqual(try store.loadPendingBatch(), pending)
        XCTAssertEqual(try store.loadPendingBatch()?.skippedCategories, [.sleep])

        try store.clearPendingBatch()
        XCTAssertNil(try store.loadPendingBatch())

        let consentStore = try FileConsentStore(rootURL: directory)
        let consent = ConsentRecord(
            consentVersion: "consent-v1",
            grantedAt: Date(timeIntervalSince1970: 3_000),
            enabledCategories: [.sleep, .steps],
            processingMode: .hybrid
        )
        try consentStore.saveConsent(consent)
        XCTAssertEqual(try consentStore.loadConsent(), consent)
    }

    func testLaunchStateRestorerRestoresConsentAndLatestSyncTime() throws {
        let store = InMemoryAnchorStore()
        let consentStore = InMemoryConsentStore(
            consent: ConsentRecord(
                consentVersion: "consent-v1",
                grantedAt: Date(timeIntervalSince1970: 3_000),
                enabledCategories: [.sleep, .steps],
                deniedCategories: [.vo2Max],
                processingMode: .hybrid
            )
        )
        try store.saveAnchor(
            CategoryAnchor(lastSyncedAt: Date(timeIntervalSince1970: 4_000)),
            for: .sleep
        )
        try store.saveAnchor(
            CategoryAnchor(lastSyncedAt: Date(timeIntervalSince1970: 5_000)),
            for: .steps
        )

        let state = try LaunchStateRestorer.restore(
            consentStore: consentStore,
            anchorStore: store
        )

        XCTAssertEqual(state?.consent, consentStore.consent)
        XCTAssertEqual(state?.consent.deniedCategories, [.vo2Max])
        XCTAssertEqual(state?.lastSyncedAt, Date(timeIntervalSince1970: 5_000))
    }

    func testConsentRecordDecodesLegacyConsentWithoutDeniedCategories() throws {
        let data = try JSONSerialization.data(withJSONObject: [
            "consentVersion": "consent-v1",
            "grantedAt": 3_000.0,
            "enabledCategories": ["sleep"],
            "processingMode": "hybrid",
        ])

        let consent = try JSONDecoder().decode(ConsentRecord.self, from: data)

        XCTAssertEqual(consent.enabledCategories, [.sleep])
        XCTAssertEqual(consent.deniedCategories, [])
    }

    func testBatchBuilderSendsConsentVersionAndOnlyReadSamples() {
        let builder = HealthSyncBatchBuilder()
        let older = sample("steps-1", category: .steps, start: Date(timeIntervalSince1970: 10))
        let newer = sample("hrv-1", category: .heartRateVariability, start: Date(timeIntervalSince1970: 20))
        let consent = ConsentRecord(
            consentVersion: "consent-v1",
            grantedAt: Date(timeIntervalSince1970: 1),
            enabledCategories: [.heartRateVariability, .steps],
            processingMode: .hybrid
        )

        let batch = builder.buildBatch(
            reads: [
                HealthKitReadResult(
                    category: .heartRateVariability,
                    samples: [newer],
                    newAnchorData: Data("hrv-next".utf8)
                ),
                HealthKitReadResult(
                    category: .steps,
                    samples: [older],
                    newAnchorData: Data("steps-next".utf8)
                ),
            ],
            anchors: [
                .heartRateVariability: CategoryAnchor(serverAnchor: "anchor-b"),
                .steps: CategoryAnchor(serverAnchor: "anchor-a"),
            ],
            consent: consent,
            deviceID: "phone",
            timezone: TimeZone(identifier: "UTC")!,
            clientSyncID: "sync-fixed"
        )

        XCTAssertEqual(batch.request.clientSyncID, "sync-fixed")
        XCTAssertEqual(batch.request.deviceID, "phone")
        XCTAssertEqual(batch.request.timezone, "UTC")
        XCTAssertEqual(batch.request.consentVersion, "consent-v1")
        XCTAssertEqual(batch.request.lastAnchor, "anchor-b")
        XCTAssertEqual(batch.request.samples.map(\.sourceSampleID), ["steps-1", "hrv-1"])
        XCTAssertEqual(batch.anchorsAfterQuery[.heartRateVariability]!, Data("hrv-next".utf8))
        XCTAssertEqual(batch.anchorsAfterQuery[.steps]!, Data("steps-next".utf8))
    }

    func testHealthSyncClientBuildsVersionedEndpointURL() throws {
        let baseURL = try XCTUnwrap(URL(string: "https://api.example.test/base"))

        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.healthSyncURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/health/sync"
        )
    }

    func testPermissionFlowAllowsFullGrant() async throws {
        let client = MockAuthorizationClient(granted: Set(HealthCategory.allCases))
        let coordinator = PermissionCoordinator(healthAuthorizationClient: client)

        let result = try await coordinator.requestPermissions(for: [.sleep, .steps])

        XCTAssertEqual(result.requested, [.sleep, .steps])
        XCTAssertEqual(Set(result.granted), [.sleep, .steps])
        XCTAssertTrue(result.denied.isEmpty)
        XCTAssertFalse(result.isDegraded)
        XCTAssertEqual(result.rationales[.sleep], HealthCategory.sleep.permissionRationale)
    }

    func testPermissionFlowAllowsPartialGrant() async throws {
        let client = MockAuthorizationClient(granted: [.sleep])
        let coordinator = PermissionCoordinator(healthAuthorizationClient: client)

        let result = try await coordinator.requestPermissions(for: [.sleep, .steps])

        XCTAssertEqual(Set(result.granted), [.sleep])
        XCTAssertEqual(Set(result.denied), [.steps])
        XCTAssertTrue(result.isDegraded)
        XCTAssertEqual(result.rationales[.steps], HealthCategory.steps.permissionRationale)

        let consent = result.consentRecord(processingMode: .hybrid)
        XCTAssertEqual(consent.enabledCategories, [.sleep])
        XCTAssertEqual(consent.deniedCategories, [.steps])
    }

    func testSleepAnalysisMetadataOnlyIncludesAsleepStages() {
        XCTAssertNil(HealthKitSleepAnalysisMetadata.asleepMetadata(forRawValue: 0))
        XCTAssertNil(HealthKitSleepAnalysisMetadata.asleepMetadata(forRawValue: 2))

        XCTAssertEqual(
            HealthKitSleepAnalysisMetadata.asleepMetadata(forRawValue: 1),
            [
                "healthkit_sleep_analysis_value": "1",
                "healthkit_sleep_stage": "asleep_unspecified",
            ]
        )
        XCTAssertEqual(
            HealthKitSleepAnalysisMetadata.asleepMetadata(forRawValue: 3)?["healthkit_sleep_stage"],
            "asleep_core"
        )
        XCTAssertEqual(
            HealthKitSleepAnalysisMetadata.asleepMetadata(forRawValue: 4)?["healthkit_sleep_stage"],
            "asleep_deep"
        )
        XCTAssertEqual(
            HealthKitSleepAnalysisMetadata.asleepMetadata(forRawValue: 5)?["healthkit_sleep_stage"],
            "asleep_rem"
        )
    }

    func testDemoHealthDataUsesOnlySyntheticSourceSamples() {
        XCTAssertFalse(DemoHealthData.samples.isEmpty)
        XCTAssertEqual(
            Set(DemoHealthData.samples.compactMap { $0.sourceMetadata["source"] }),
            ["demo"]
        )
        XCTAssertEqual(
            Set(DemoHealthData.samples.map(\.sampleType)),
            [
                HealthCategory.heartRateVariability.apiSampleType,
                HealthCategory.sleep.apiSampleType,
                HealthCategory.steps.apiSampleType,
            ]
        )
    }

    func testInterruptedSyncResumesSavedBatchWithoutReadingHealthKitAgain() async throws {
        let store = InMemoryAnchorStore()
        let reader = MockHealthKitReader(reads: [
            .steps: HealthKitReadResult(
                category: .steps,
                samples: [sample("steps-1", category: .steps)],
                newAnchorData: Data("steps-next".utf8)
            ),
        ])
        let response = HealthSyncResponse(
            syncID: UUID(),
            acceptedCount: 1,
            duplicateCount: 0,
            rejectedCount: 0,
            warnings: [],
            nextAnchor: "server-next"
        )
        let api = MockSyncAPIClient(results: [
            .failure(TestError.interrupted),
            .success(response),
        ])
        let engine = HealthSyncEngine(
            anchorStore: store,
            healthKitReader: reader,
            apiClient: api,
            clock: { Date(timeIntervalSince1970: 10_000) }
        )
        let consent = ConsentRecord(enabledCategories: [.steps], processingMode: .cloudAssisted)

        do {
            _ = try await engine.syncNow(consent: consent, deviceID: "phone")
            XCTFail("Expected first sync to be interrupted")
        } catch TestError.interrupted {}

        let pendingAfterFailure = try store.loadPendingBatch()
        XCTAssertNotNil(pendingAfterFailure)

        let outcome = try await engine.syncNow(consent: consent, deviceID: "phone")

        XCTAssertEqual(outcome.response, response)
        XCTAssertEqual(reader.readCount, 1)
        XCTAssertEqual(api.requests.count, 2)
        XCTAssertEqual(api.requests[0].clientSyncID, api.requests[1].clientSyncID)
        XCTAssertEqual(api.requests[0].samples.map(\.sourceSampleID), ["steps-1"])
        XCTAssertNil(try store.loadPendingBatch())
        XCTAssertEqual(
            try store.loadAnchor(for: .steps),
            CategoryAnchor(
                healthKitAnchorData: Data("steps-next".utf8),
                serverAnchor: "server-next",
                lastSyncedAt: Date(timeIntervalSince1970: 10_000)
            )
        )
    }

    func testSyncContinuesWithoutAdvancingFailedCategory() async throws {
        let store = InMemoryAnchorStore()
        let reader = MockHealthKitReader(
            reads: [
                .steps: HealthKitReadResult(
                    category: .steps,
                    samples: [sample("steps-1", category: .steps)],
                    newAnchorData: Data("steps-next".utf8)
                ),
            ],
            failingCategories: [.sleep]
        )
        let response = HealthSyncResponse(
            syncID: UUID(),
            acceptedCount: 1,
            duplicateCount: 0,
            rejectedCount: 0,
            warnings: [],
            nextAnchor: "server-next"
        )
        let api = MockSyncAPIClient(results: [.success(response)])
        let engine = HealthSyncEngine(
            anchorStore: store,
            healthKitReader: reader,
            apiClient: api
        )
        let consent = ConsentRecord(
            enabledCategories: [.sleep, .steps],
            processingMode: .hybrid
        )

        let outcome = try await engine.syncNow(consent: consent, deviceID: "phone")

        XCTAssertEqual(outcome.response, response)
        XCTAssertEqual(outcome.skippedCategories, [.sleep])
        XCTAssertEqual(reader.readCount, 2)
        XCTAssertEqual(api.requests.single?.samples.map(\.sourceSampleID), ["steps-1"])
        XCTAssertEqual(try store.loadAnchor(for: .steps)?.serverAnchor, "server-next")
        XCTAssertEqual(try store.loadAnchor(for: .steps)?.lastSyncedAt != nil, true)
        XCTAssertNil(try store.loadAnchor(for: .sleep))
    }

    func testSyncDoesNotPostOrAdvanceWhenAllCategoryReadsFail() async throws {
        let store = InMemoryAnchorStore()
        let reader = MockHealthKitReader(
            reads: [:],
            failingCategories: [.sleep, .steps]
        )
        let api = MockSyncAPIClient(results: [])
        let engine = HealthSyncEngine(
            anchorStore: store,
            healthKitReader: reader,
            apiClient: api
        )
        let consent = ConsentRecord(
            enabledCategories: [.sleep, .steps],
            processingMode: .hybrid
        )

        do {
            _ = try await engine.syncNow(consent: consent, deviceID: "phone")
            XCTFail("Expected sync to fail when every HealthKit category read fails")
        } catch HealthSyncEngineError.noReadableCategories(let categories) {
            XCTAssertEqual(categories, [.sleep, .steps])
        }

        XCTAssertEqual(reader.readCount, 2)
        XCTAssertTrue(api.requests.isEmpty)
        XCTAssertNil(try store.loadPendingBatch())
        XCTAssertNil(try store.loadAnchor(for: .sleep))
        XCTAssertNil(try store.loadAnchor(for: .steps))
    }

    func testNoSecretsInIOSBundleResources() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let resources = packageRoot
            .appendingPathComponent("App")
        let files = try FileManager.default
            .contentsOfDirectory(at: resources, includingPropertiesForKeys: nil)
            .filter { !$0.hasDirectoryPath }

        XCTAssertFalse(files.isEmpty)
        for file in files {
            let content = try String(contentsOf: file)
            XCTAssertFalse(content.contains("BEGIN PRIVATE KEY"), file.lastPathComponent)
            XCTAssertFalse(content.contains("api_key"), file.lastPathComponent)
            XCTAssertFalse(content.contains("API_KEY"), file.lastPathComponent)
            XCTAssertFalse(content.contains("Bearer "), file.lastPathComponent)
            XCTAssertFalse(content.contains("sk-"), file.lastPathComponent)
        }
    }

    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}

private enum TestError: Error {
    case interrupted
}

private func sample(
    _ id: String,
    category: HealthCategory,
    start: Date = Date(timeIntervalSince1970: 1_000)
) -> HealthSample {
    HealthSample(
        sourceSampleID: id,
        sampleType: category.apiSampleType,
        startTime: start,
        endTime: start.addingTimeInterval(60),
        value: 1,
        unit: "count",
        sourceMetadata: ["source": "unit-test"]
    )
}

private final class MockAuthorizationClient: HealthAuthorizationClient {
    let granted: Set<HealthCategory>

    init(granted: Set<HealthCategory>) {
        self.granted = granted
    }

    func requestAuthorization(for categories: [HealthCategory]) async throws -> Set<HealthCategory> {
        granted.intersection(categories)
    }
}

private final class MockHealthKitReader: HealthKitReading, @unchecked Sendable {
    private let reads: [HealthCategory: HealthKitReadResult]
    private let failingCategories: Set<HealthCategory>
    private(set) var readCount = 0

    init(
        reads: [HealthCategory: HealthKitReadResult],
        failingCategories: Set<HealthCategory> = []
    ) {
        self.reads = reads
        self.failingCategories = failingCategories
    }

    func readSamples(
        for category: HealthCategory,
        anchorData: Data?
    ) async throws -> HealthKitReadResult {
        readCount += 1
        if failingCategories.contains(category) {
            throw TestError.interrupted
        }
        return reads[category] ?? HealthKitReadResult(
            category: category,
            samples: [],
            newAnchorData: anchorData
        )
    }
}

private extension Array {
    var single: Element? {
        count == 1 ? self[0] : nil
    }
}

private final class MockSyncAPIClient: HealthSyncAPIClient, @unchecked Sendable {
    private var results: [Result<HealthSyncResponse, Error>]
    private(set) var requests: [HealthSyncRequest] = []

    init(results: [Result<HealthSyncResponse, Error>]) {
        self.results = results
    }

    func postHealthSync(_ request: HealthSyncRequest) async throws -> HealthSyncResponse {
        requests.append(request)
        let result = results.removeFirst()
        return try result.get()
    }
}

private final class InMemoryAnchorStore: AnchorPersisting, @unchecked Sendable {
    private var anchors: [HealthCategory: CategoryAnchor] = [:]
    private var pending: PendingSyncBatch?

    func loadAnchor(for category: HealthCategory) throws -> CategoryAnchor? {
        anchors[category]
    }

    func saveAnchor(_ anchor: CategoryAnchor, for category: HealthCategory) throws {
        anchors[category] = anchor
    }

    func loadPendingBatch() throws -> PendingSyncBatch? {
        pending
    }

    func savePendingBatch(_ batch: PendingSyncBatch) throws {
        pending = batch
    }

    func clearPendingBatch() throws {
        pending = nil
    }
}

private final class InMemoryConsentStore: ConsentPersisting, @unchecked Sendable {
    private(set) var consent: ConsentRecord?

    init(consent: ConsentRecord? = nil) {
        self.consent = consent
    }

    func loadConsent() throws -> ConsentRecord? {
        consent
    }

    func saveConsent(_ consent: ConsentRecord) throws {
        self.consent = consent
    }
}

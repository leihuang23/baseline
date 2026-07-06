import Foundation
import CryptoKit
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
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dailyCheckInURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/checkins/daily"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dailyCheckInURL(
                baseURL: baseURL,
                date: "2026-07-03"
            ).absoluteString,
            "https://api.example.test/base/v1/checkins/daily/by-date/2026-07-03"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.goalsURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/goals"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dailyAnalysisURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/analysis/daily"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dailyAnalysisJobURL(
                baseURL: baseURL,
                id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
            ).absoluteString,
            "https://api.example.test/base/v1/analysis/daily/00000000-0000-0000-0000-000000000001"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.analysisTraceURL(
                baseURL: baseURL,
                traceID: UUID(uuidString: "00000000-0000-0000-0000-000000000002")!
            ).absoluteString,
            "https://api.example.test/base/v1/analysis/traces/00000000-0000-0000-0000-000000000002"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dailyBriefingURL(
                baseURL: baseURL,
                date: "2026-07-04"
            ).absoluteString,
            "https://api.example.test/base/v1/briefings/2026-07-04"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dailyBriefingURL(
                baseURL: baseURL,
                date: "2026-07-04",
                offlineLast: true
            ).absoluteString,
            "https://api.example.test/base/v1/briefings/2026-07-04?offline_last=true"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.assistantQueryURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/assistant/query"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dataExportURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/data/export"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.dataExportDownloadURL(
                baseURL: baseURL,
                downloadURL: "/v1/data/export/00000000-0000-0000-0000-000000000001/file"
            ).absoluteString,
            "https://api.example.test/v1/data/export/00000000-0000-0000-0000-000000000001/file"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.deleteAllDataURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/data/all"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.consentHistoryURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/data/consent/history"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.consentURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/data/consent"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.disableExternalLLMURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/data/consent/disable-external-llm"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.revokeConsentURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/data/consent/revoke"
        )
        XCTAssertEqual(
            URLSessionHealthSyncAPIClient.modelDisclosuresURL(baseURL: baseURL).absoluteString,
            "https://api.example.test/base/v1/data/model-disclosures"
        )
    }

    func testConsentRecordRequestMapsHealthKitCategoriesToBackendConsentBuckets() throws {
        let consent = ConsentRecord(
            consentVersion: "server-v1",
            enabledCategories: [.sleep, .workouts, .steps, .heartRateVariability, .vo2Max],
            processingMode: .hybrid
        )

        let payload = try jsonDictionary(from: JSONEncoder().encode(ConsentRecordRequest(consent: consent)))

        XCTAssertEqual(payload["consent_version"] as? String, "server-v1")
        XCTAssertEqual(payload["privacy_mode"] as? String, "hybrid")
        XCTAssertEqual(payload["cloud_processing_enabled"] as? Bool, true)
        XCTAssertEqual(payload["external_llm_enabled"] as? Bool, false)
        XCTAssertEqual(
            payload["health_categories_enabled"] as? [String],
            ["activity", "heart_rate", "sleep"]
        )
    }

    func testFileBriefingStorePersistsLatestBriefingForOfflineUse() throws {
        let directory = try temporaryDirectory()
        let store = try FileBriefingStore(rootURL: directory)
        let briefing = sampleBriefing()

        try store.saveLatestBriefing(briefing)

        XCTAssertEqual(try store.loadLatestBriefing(), briefing)
    }

    func testDataExportDownloadReturnsBinaryBytes() async throws {
        let expected = Data([0, 1, 2, 3])
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BinaryExportURLProtocol.self]
        BinaryExportURLProtocol.handler = { request in
            XCTAssertEqual(
                request.url?.absoluteString,
                "https://api.example.test/v1/data/export/00000000-0000-0000-0000-000000000001/file"
            )
            XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "application/octet-stream")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
            let response = HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/octet-stream"]
            )
            return (try XCTUnwrap(response), expected)
        }
        defer { BinaryExportURLProtocol.handler = nil }
        let client = URLSessionHealthSyncAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://api.example.test/base")),
            session: URLSession(configuration: configuration),
            apiAuthToken: "test-token"
        )

        let data = try await client.downloadDataExport(
            from: "/v1/data/export/00000000-0000-0000-0000-000000000001/file"
        )

        XCTAssertEqual(data, expected)
    }

    func testDataExportDownloadDoesNotAttachBearerTokenToExternalURLs() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BinaryExportURLProtocol.self]
        BinaryExportURLProtocol.handler = { request in
            XCTAssertEqual(
                request.url?.absoluteString,
                "https://exports.example.test/export.bin"
            )
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            let response = HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/octet-stream"]
            )
            return (try XCTUnwrap(response), Data([1, 2, 3]))
        }
        defer { BinaryExportURLProtocol.handler = nil }
        let client = URLSessionHealthSyncAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://api.example.test/base")),
            session: URLSession(configuration: configuration),
            apiAuthToken: "test-token"
        )

        let data = try await client.downloadDataExport(from: "https://exports.example.test/export.bin")

        XCTAssertEqual(data, Data([1, 2, 3]))
    }

    func testDataExportDownloadCanDecryptEncryptedBytes() async throws {
        let plaintext = Data("scoped export payload".utf8)
        let key = SymmetricKey(size: .bits256)
        let keyData = key.withUnsafeBytes { Data($0) }
        let nonce = try AES.GCM.Nonce(data: Data(repeating: 7, count: 12))
        let magic = Data("BASELINE-EXPORT-AES256GCM-V1".utf8)
        let sealed = try AES.GCM.seal(plaintext, using: key, nonce: nonce, authenticating: magic)
        var encrypted = magic
        encrypted.append(sealed.nonce.withUnsafeBytes { Data($0) })
        encrypted.append(sealed.tag)
        encrypted.append(sealed.ciphertext)

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BinaryExportURLProtocol.self]
        BinaryExportURLProtocol.handler = { request in
            let response = HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/octet-stream"]
            )
            return (try XCTUnwrap(response), encrypted)
        }
        defer { BinaryExportURLProtocol.handler = nil }
        let client = URLSessionHealthSyncAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://api.example.test/base")),
            session: URLSession(configuration: configuration)
        )
        let response = DataExportResponse(
            exportJobID: try XCTUnwrap(UUID(uuidString: "00000000-0000-0000-0000-000000000001")),
            status: "ready",
            expiresAt: "2026-07-04T08:00:00Z",
            downloadURL: "/v1/data/export/00000000-0000-0000-0000-000000000001/file",
            encryption: [
                "algorithm": "AES-256-GCM",
                "key_base64": keyData.base64EncodedString(),
            ]
        )

        let data = try await client.downloadDecryptedDataExport(response)

        XCTAssertEqual(data, plaintext)
    }

    func testAPIClientAttachesBearerTokenToEnvelopeRequests() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BinaryExportURLProtocol.self]
        BinaryExportURLProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
            XCTAssertEqual(request.url?.absoluteString, "https://api.example.test/base/v1/health/sync")
            let response = HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )
            let data = try JSONSerialization.data(withJSONObject: [
                "status": "success",
                "data": [
                    "schema_version": "v1",
                    "sync_id": "00000000-0000-0000-0000-000000000001",
                    "accepted_count": 0,
                    "duplicate_count": 0,
                    "rejected_count": 0,
                    "warnings": [],
                    "next_anchor": "anchor-1",
                    "data_quality_summary": [
                        "status": "degraded",
                        "notes": [
                            ["metric": "hrv", "note": "Sparse HRV samples.", "severity": "warning"],
                        ],
                    ],
                ],
            ])
            return (try XCTUnwrap(response), data)
        }
        defer { BinaryExportURLProtocol.handler = nil }
        let client = URLSessionHealthSyncAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://api.example.test/base")),
            session: URLSession(configuration: configuration),
            apiAuthToken: "test-token"
        )

        let response = try await client.postHealthSync(
            HealthSyncRequest(
                clientSyncID: "sync-1",
                deviceID: "device-1",
                timezone: "UTC",
                samples: [],
                lastAnchor: nil,
                consentVersion: "consent-v1"
            )
        )

        XCTAssertEqual(response.nextAnchor, "anchor-1")
        XCTAssertEqual(response.dataQualitySummary.status, "degraded")
        XCTAssertEqual(response.dataQualitySummary.notes.count, 1)
        XCTAssertEqual(response.dataQualitySummary.notes.first?.metric, "hrv")
    }

    func testAPIClientAttachesBearerTokenToDeleteRequests() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BinaryExportURLProtocol.self]
        BinaryExportURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
            XCTAssertEqual(
                request.url?.absoluteString,
                "https://api.example.test/base/v1/checkins/daily/00000000-0000-0000-0000-000000000001"
            )
            let response = HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 204,
                httpVersion: nil,
                headerFields: nil
            )
            return (try XCTUnwrap(response), Data())
        }
        defer { BinaryExportURLProtocol.handler = nil }
        let client = URLSessionHealthSyncAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://api.example.test/base")),
            session: URLSession(configuration: configuration),
            apiAuthToken: "test-token"
        )

        try await client.deleteDailyCheckIn(
            id: try XCTUnwrap(UUID(uuidString: "00000000-0000-0000-0000-000000000001"))
        )
    }

    func testAPIClientOmitsBearerTokenOverHTTPToRemoteHost() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BinaryExportURLProtocol.self]
        BinaryExportURLProtocol.handler = { request in
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            XCTAssertEqual(request.url?.absoluteString, "http://api.example.test/base/v1/health/sync")
            let response = HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )
            let data = try JSONSerialization.data(withJSONObject: [
                "status": "success",
                "data": [
                    "schema_version": "v1",
                    "sync_id": "00000000-0000-0000-0000-000000000001",
                    "accepted_count": 0,
                    "duplicate_count": 0,
                    "rejected_count": 0,
                    "warnings": [],
                    "next_anchor": "anchor-1",
                    "data_quality_summary": [
                        "status": "ok",
                        "notes": [],
                    ],
                ],
            ])
            return (try XCTUnwrap(response), data)
        }
        defer { BinaryExportURLProtocol.handler = nil }
        let client = URLSessionHealthSyncAPIClient(
            baseURL: try XCTUnwrap(URL(string: "http://api.example.test/base")),
            session: URLSession(configuration: configuration),
            apiAuthToken: "test-token"
        )

        _ = try await client.postHealthSync(
            HealthSyncRequest(
                clientSyncID: "sync-1",
                deviceID: "device-1",
                timezone: "UTC",
                samples: [],
                lastAnchor: nil,
                consentVersion: "consent-v1"
            )
        )
    }

    func testDataControlPayloadsEncodeAndDecode() throws {
        let encoder = JSONEncoder()
        let exportRequest = DataExportRequest(
            exportScope: .health,
            format: .csv,
            includeRawData: true,
            includeModelTraces: true
        )
        let exportPayload = try jsonDictionary(from: encoder.encode(exportRequest))

        XCTAssertEqual(exportPayload["schema_version"] as? String, "v1")
        XCTAssertEqual(exportPayload["export_scope"] as? String, "health")
        XCTAssertEqual(exportPayload["format"] as? String, "csv")
        XCTAssertEqual(exportPayload["include_raw_data"] as? Bool, true)
        XCTAssertEqual(exportPayload["include_model_traces"] as? Bool, true)

        let revocationRequest = ConsentRevocationRequest(
            consentVersion: "v2",
            revokeCloudProcessing: false,
            revokeExternalLLM: true,
            revokeRawNoteProcessing: true,
            revokeHealthCategories: ["steps"]
        )
        let revocationPayload = try jsonDictionary(from: encoder.encode(revocationRequest))
        XCTAssertEqual(revocationPayload["consent_version"] as? String, "v2")
        XCTAssertEqual(revocationPayload["revoke_cloud_processing"] as? Bool, false)
        XCTAssertEqual(revocationPayload["revoke_external_llm"] as? Bool, true)
        XCTAssertEqual(revocationPayload["revoke_raw_note_processing"] as? Bool, true)
        XCTAssertEqual(revocationPayload["revoke_health_categories"] as? [String], ["steps"])

        let decoder = JSONDecoder()
        let consentRecord: [String: Any] = [
            "schema_version": "v1",
            "id": "00000000-0000-0000-0000-000000000001",
            "user_id": "00000000-0000-0000-0000-000000000002",
            "consent_version": "v2",
            "health_categories_enabled": ["sleep"],
            "cloud_processing_enabled": true,
            "external_llm_enabled": false,
            "raw_note_processing_enabled": false,
            "timestamp": "2026-07-04T08:00:00Z",
            "revoked_at": NSNull(),
        ]
        let historyData = try JSONSerialization.data(withJSONObject: [
            "schema_version": "v1",
            "active_consent_version": "v2",
            "records": [consentRecord],
        ])
        let history = try decoder.decode(ConsentHistoryResponse.self, from: historyData)
        XCTAssertEqual(history.activeConsentVersion, "v2")
        XCTAssertEqual(history.records.single?.healthCategoriesEnabled, ["sleep"])
        XCTAssertEqual(history.records.single?.cloudProcessingEnabled, true)

        let deleteData = try JSONSerialization.data(withJSONObject: [
            "schema_version": "v1",
            "deleted": ["daily_check_ins": 1, "model_runs": 2],
        ])
        let deleteResponse = try decoder.decode(DataDeleteResponse.self, from: deleteData)
        XCTAssertEqual(deleteResponse.deleted["daily_check_ins"], 1)
        XCTAssertEqual(deleteResponse.deleted["model_runs"], 2)

        let disclosureData = try JSONSerialization.data(withJSONObject: [
            "schema_version": "v1",
            "runs": [
                [
                    "run_id": "00000000-0000-0000-0000-000000000003",
                    "created_at": "2026-07-04T09:00:00Z",
                    "run_type": "explanation",
                    "provider": "external-provider",
                    "model": "model-a",
                    "prompt_version": "prompt-v1",
                    "schema_version": "schema-v1",
                    "input_hash": "input-hash",
                    "payload_metadata": [
                        "message_count": 1,
                        "disclosure_payload": [
                            "messages": [
                                [
                                    "role": "user",
                                    "content": [
                                        "task_type": "simple_explanation",
                                        "sleep_debt_hours": 0.3,
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ])
        let disclosure = try decoder.decode(ModelDisclosureResponse.self, from: disclosureData)
        XCTAssertEqual(disclosure.runs.single?.provider, "external-provider")
        XCTAssertEqual(disclosure.runs.single?.payloadMetadata["message_count"], .double(1))
        guard case .object(let disclosurePayload)? = disclosure.runs.single?.payloadMetadata["disclosure_payload"] else {
            XCTFail("Expected disclosure payload object")
            return
        }
        XCTAssertNotNil(disclosurePayload["messages"])
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
            nextAnchor: "server-next",
            dataQualitySummary: DataQualitySummary(status: "ok", notes: [])
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
            nextAnchor: "server-next",
            dataQualitySummary: DataQualitySummary(status: "ok", notes: [])
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

    func testLocalOnlySyncClearsPendingBatchWithoutReadingOrPosting() async throws {
        let store = InMemoryAnchorStore()
        try store.savePendingBatch(
            PendingSyncBatch(
                request: HealthSyncRequest(
                    clientSyncID: "pending",
                    deviceID: "phone",
                    timezone: "UTC",
                    samples: [sample("steps-1", category: .steps)],
                    lastAnchor: nil,
                    consentVersion: "consent-v1"
                ),
                anchorsAfterQuery: [.steps: Data("steps-next".utf8)]
            )
        )
        let reader = MockHealthKitReader(reads: [
            .steps: HealthKitReadResult(
                category: .steps,
                samples: [sample("steps-2", category: .steps)],
                newAnchorData: Data("steps-next".utf8)
            ),
        ])
        let api = MockSyncAPIClient(results: [])
        let engine = HealthSyncEngine(
            anchorStore: store,
            healthKitReader: reader,
            apiClient: api
        )
        let consent = ConsentRecord(enabledCategories: [.steps], processingMode: .localOnly)

        do {
            _ = try await engine.syncNow(consent: consent, deviceID: "phone")
            XCTFail("Expected local-only sync to be disabled")
        } catch HealthSyncEngineError.localOnlySyncDisabled {}

        XCTAssertEqual(reader.readCount, 0)
        XCTAssertTrue(api.requests.isEmpty)
        XCTAssertNil(try store.loadPendingBatch())
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

private func sampleBriefing() -> DailyBriefingResponse {
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
        recommendation: RecommendationSummary(primary: "Keep training moderate."),
        recommendationBand: "moderate_or_upper_body",
        uncertainty: ["No soreness check-in yet."],
        safetyNotes: ["This is wellness decision support, not medical advice."],
        traceID: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
        generatedAt: "2026-07-04T06:40:00Z"
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

private func jsonDictionary(from data: Data) throws -> [String: Any] {
    try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
}

private final class MockSyncAPIClient: HealthSyncAPIClient, @unchecked Sendable {
    private var results: [Result<HealthSyncResponse, Error>]
    private(set) var requests: [HealthSyncRequest] = []
    private(set) var consentRequests: [ConsentRecordRequest] = []

    init(results: [Result<HealthSyncResponse, Error>]) {
        self.results = results
    }

    func postHealthSync(_ request: HealthSyncRequest) async throws -> HealthSyncResponse {
        requests.append(request)
        let result = results.removeFirst()
        return try result.get()
    }

    func recordConsent(_ request: ConsentRecordRequest) async throws -> DataControlConsentResponse {
        consentRequests.append(request)
        return DataControlConsentResponse(
            schemaVersion: "v1",
            id: UUID(),
            userID: UUID(),
            consentVersion: request.consentVersion,
            healthCategoriesEnabled: request.healthCategoriesEnabled,
            cloudProcessingEnabled: request.cloudProcessingEnabled,
            externalLLMEnabled: request.externalLLMEnabled,
            rawNoteProcessingEnabled: request.rawNoteProcessingEnabled,
            timestamp: "2026-07-04T08:00:00Z",
            revokedAt: nil
        )
    }
}

private final class BinaryExportURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
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

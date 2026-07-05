import Foundation
import CryptoKit

public enum BaselineAPIError: Error, Equatable, Sendable {
    case invalidResponse
    case unsuccessfulStatus(Int)
    case missingDataEnvelope
    case missingExportDownloadURL
    case invalidExportEncryptionMetadata
}

public typealias HealthSyncAPIError = BaselineAPIError

public final class URLSessionHealthSyncAPIClient: HealthSyncAPIClient, CheckInAPIClient, GoalsAPIClient,
    DailyBriefingAPIClient, DataControlsAPIClient
{
    private let baseURL: URL
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder
    private let apiAuthToken: String?
    private static let exportMagic = Data("BASELINE-EXPORT-AES256GCM-V1".utf8)

    public init(baseURL: URL, session: URLSession = .shared, apiAuthToken: String? = nil) {
        self.baseURL = baseURL
        self.session = session
        self.apiAuthToken = apiAuthToken
        encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
    }

    public func postHealthSync(_ request: HealthSyncRequest) async throws -> HealthSyncResponse {
        try await sendEnvelope(method: "POST", url: Self.healthSyncURL(baseURL: baseURL), body: request)
    }

    public func submitDailyCheckIn(_ request: DailyCheckInRequest) async throws -> DailyCheckInResponse {
        try await sendEnvelope(method: "POST", url: Self.dailyCheckInURL(baseURL: baseURL), body: request)
    }

    public func fetchDailyCheckIn(date: String) async throws -> DailyCheckInDetailResponse {
        try await sendEnvelope(
            method: "GET",
            url: Self.dailyCheckInURL(baseURL: baseURL, date: date),
            body: Optional<String>.none
        )
    }

    public func updateDailyCheckIn(
        id: UUID,
        request: DailyCheckInRequest
    ) async throws -> DailyCheckInResponse {
        try await sendEnvelope(
            method: "PUT",
            url: Self.dailyCheckInURL(baseURL: baseURL, id: id),
            body: request
        )
    }

    public func deleteDailyCheckIn(id: UUID) async throws {
        var urlRequest = URLRequest(url: Self.dailyCheckInURL(baseURL: baseURL, id: id))
        urlRequest.httpMethod = "DELETE"
        applyAuthHeader(to: &urlRequest)
        let (_, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw BaselineAPIError.invalidResponse
        }
        guard 200 ..< 300 ~= httpResponse.statusCode else {
            throw BaselineAPIError.unsuccessfulStatus(httpResponse.statusCode)
        }
    }

    public func listGoals() async throws -> [GoalResponse] {
        try await sendEnvelope(method: "GET", url: Self.goalsURL(baseURL: baseURL), body: Optional<String>.none)
    }

    public func createGoal(_ request: GoalRequest) async throws -> GoalResponse {
        try await sendEnvelope(method: "POST", url: Self.goalsURL(baseURL: baseURL), body: request)
    }

    public func pauseGoal(id: UUID) async throws -> GoalResponse {
        try await sendEnvelope(
            method: "POST",
            url: Self.pauseGoalURL(baseURL: baseURL, id: id),
            body: Optional<String>.none
        )
    }

    public func generateDailyAnalysis(_ request: DailyAnalysisRequest) async throws -> DailyAnalysisResponse {
        try await sendEnvelope(method: "POST", url: Self.dailyAnalysisURL(baseURL: baseURL), body: request)
    }

    public func fetchDailyAnalysisJob(id: UUID) async throws -> DailyAnalysisResponse {
        try await sendEnvelope(
            method: "GET",
            url: Self.dailyAnalysisJobURL(baseURL: baseURL, id: id),
            body: Optional<String>.none
        )
    }

    public func fetchDailyBriefing(date: String, offlineLast: Bool = false) async throws -> DailyBriefingResponse {
        try await sendEnvelope(
            method: "GET",
            url: Self.dailyBriefingURL(baseURL: baseURL, date: date, offlineLast: offlineLast),
            body: Optional<String>.none
        )
    }

    public func fetchBriefingTrace(traceID: UUID) async throws -> BriefingTraceInspection {
        try await sendEnvelope(
            method: "GET",
            url: Self.analysisTraceURL(baseURL: baseURL, traceID: traceID),
            body: Optional<String>.none
        )
    }

    public func submitAssistantQuery(_ request: AssistantQueryRequest) async throws -> AssistantQueryResponse {
        try await sendEnvelope(method: "POST", url: Self.assistantQueryURL(baseURL: baseURL), body: request)
    }

    public func requestDataExport(_ request: DataExportRequest) async throws -> DataExportResponse {
        try await sendEnvelope(method: "POST", url: Self.dataExportURL(baseURL: baseURL), body: request)
    }

    public func downloadDataExport(from downloadURL: String) async throws -> Data {
        var urlRequest = URLRequest(url: Self.dataExportDownloadURL(baseURL: baseURL, downloadURL: downloadURL))
        urlRequest.httpMethod = "GET"
        applyAuthHeader(to: &urlRequest)
        urlRequest.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw BaselineAPIError.invalidResponse
        }
        guard 200 ..< 300 ~= httpResponse.statusCode else {
            throw BaselineAPIError.unsuccessfulStatus(httpResponse.statusCode)
        }
        return data
    }

    public func downloadDecryptedDataExport(_ response: DataExportResponse) async throws -> Data {
        guard let downloadURL = response.downloadURL else {
            throw BaselineAPIError.missingExportDownloadURL
        }
        let encrypted = try await downloadDataExport(from: downloadURL)
        return try Self.decryptDataExport(encrypted, encryption: response.encryption)
    }

    public static func decryptDataExport(_ encrypted: Data, encryption: [String: String]) throws -> Data {
        guard encryption["algorithm"] == "AES-256-GCM",
              let keyBase64 = encryption["key_base64"],
              let keyData = Data(base64Encoded: keyBase64),
              keyData.count == 32,
              encrypted.starts(with: exportMagic)
        else {
            throw BaselineAPIError.invalidExportEncryptionMetadata
        }

        let headerLength = exportMagic.count
        let nonceLength = 12
        let tagLength = 16
        guard encrypted.count >= headerLength + nonceLength + tagLength else {
            throw BaselineAPIError.invalidExportEncryptionMetadata
        }

        let nonceStart = headerLength
        let tagStart = nonceStart + nonceLength
        let ciphertextStart = tagStart + tagLength
        do {
            let nonce = try AES.GCM.Nonce(data: Data(encrypted[nonceStart ..< tagStart]))
            let sealedBox = try AES.GCM.SealedBox(
                nonce: nonce,
                ciphertext: Data(encrypted[ciphertextStart...]),
                tag: Data(encrypted[tagStart ..< ciphertextStart])
            )
            return try AES.GCM.open(
                sealedBox,
                using: SymmetricKey(data: keyData),
                authenticating: exportMagic
            )
        } catch {
            throw BaselineAPIError.invalidExportEncryptionMetadata
        }
    }

    public func deleteAllData() async throws -> DataDeleteResponse {
        try await sendEnvelope(method: "DELETE", url: Self.deleteAllDataURL(baseURL: baseURL), body: Optional<String>.none)
    }

    public func disableExternalLLM(_ request: DisableExternalLLMRequest) async throws -> DataControlConsentResponse {
        try await sendEnvelope(method: "POST", url: Self.disableExternalLLMURL(baseURL: baseURL), body: request)
    }

    public func disableCloudProcessing(_ request: ConsentRevocationRequest) async throws -> DataControlConsentResponse {
        try await sendEnvelope(method: "POST", url: Self.revokeConsentURL(baseURL: baseURL), body: request)
    }

    public func fetchConsentHistory() async throws -> ConsentHistoryResponse {
        try await sendEnvelope(method: "GET", url: Self.consentHistoryURL(baseURL: baseURL), body: Optional<String>.none)
    }

    public func fetchModelDisclosures() async throws -> ModelDisclosureResponse {
        try await sendEnvelope(method: "GET", url: Self.modelDisclosuresURL(baseURL: baseURL), body: Optional<String>.none)
    }

    public static func healthSyncURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/health/sync")
    }

    public static func dailyCheckInURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/checkins/daily")
    }

    public static func dailyCheckInURL(baseURL: URL, id: UUID) -> URL {
        dailyCheckInURL(baseURL: baseURL).appendingPathComponent(id.uuidString)
    }

    public static func dailyCheckInURL(baseURL: URL, date: String) -> URL {
        dailyCheckInURL(baseURL: baseURL)
            .appendingPathComponent("by-date")
            .appendingPathComponent(date)
    }

    public static func goalsURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/goals")
    }

    public static func pauseGoalURL(baseURL: URL, id: UUID) -> URL {
        goalsURL(baseURL: baseURL).appendingPathComponent(id.uuidString).appendingPathComponent("pause")
    }

    public static func dailyAnalysisURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/analysis/daily")
    }

    public static func dailyAnalysisJobURL(baseURL: URL, id: UUID) -> URL {
        dailyAnalysisURL(baseURL: baseURL).appendingPathComponent(id.uuidString)
    }

    public static func analysisTraceURL(baseURL: URL, traceID: UUID) -> URL {
        baseURL.appendingPathComponent("v1/analysis/traces").appendingPathComponent(traceID.uuidString)
    }

    public static func dailyBriefingURL(baseURL: URL, date: String, offlineLast: Bool = false) -> URL {
        let url = baseURL.appendingPathComponent("v1/briefings").appendingPathComponent(date)
        guard offlineLast else {
            return url
        }
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "offline_last", value: "true")]
        return components?.url ?? url
    }

    public static func assistantQueryURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/assistant/query")
    }

    public static func dataExportURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/data/export")
    }

    public static func dataExportDownloadURL(baseURL: URL, downloadURL: String) -> URL {
        if let url = URL(string: downloadURL), url.scheme != nil {
            return url
        }
        if downloadURL.hasPrefix("/"), let url = URL(string: downloadURL, relativeTo: baseURL) {
            return url.absoluteURL
        }
        return baseURL.appendingPathComponent(downloadURL)
    }

    public static func deleteAllDataURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/data/all")
    }

    public static func consentHistoryURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/data/consent/history")
    }

    public static func disableExternalLLMURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/data/consent/disable-external-llm")
    }

    public static func revokeConsentURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/data/consent/revoke")
    }

    public static func modelDisclosuresURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/data/model-disclosures")
    }

    private func sendEnvelope<Response: Codable & Sendable, Body: Encodable>(
        method: String,
        url: URL,
        body: Body?
    ) async throws -> Response {
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = method
        applyAuthHeader(to: &urlRequest)
        if let body {
            urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
            urlRequest.httpBody = try encoder.encode(body)
        }

        let (data, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw BaselineAPIError.invalidResponse
        }
        guard 200 ..< 300 ~= httpResponse.statusCode else {
            throw BaselineAPIError.unsuccessfulStatus(httpResponse.statusCode)
        }
        let envelope = try decoder.decode(APIEnvelope<Response>.self, from: data)
        guard let response = envelope.data else {
            throw BaselineAPIError.missingDataEnvelope
        }
        return response
    }

    private func applyAuthHeader(to request: inout URLRequest) {
        guard let apiAuthToken, !apiAuthToken.isEmpty, isSameOrigin(request.url) else {
            return
        }
        request.setValue("Bearer \(apiAuthToken)", forHTTPHeaderField: "Authorization")
    }

    private func isSameOrigin(_ url: URL?) -> Bool {
        guard let url else {
            return false
        }
        return url.scheme == baseURL.scheme
            && url.host == baseURL.host
            && url.port == baseURL.port
    }

}

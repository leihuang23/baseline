import Foundation

public enum BaselineAPIError: Error, Equatable, Sendable {
    case invalidResponse
    case unsuccessfulStatus(Int)
    case missingDataEnvelope
}

public typealias HealthSyncAPIError = BaselineAPIError

public final class URLSessionHealthSyncAPIClient: HealthSyncAPIClient, CheckInAPIClient, GoalsAPIClient {
    private let baseURL: URL
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
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

    private func sendEnvelope<Response: Codable & Sendable, Body: Encodable>(
        method: String,
        url: URL,
        body: Body?
    ) async throws -> Response {
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = method
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
}

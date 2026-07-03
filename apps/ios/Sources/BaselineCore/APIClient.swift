import Foundation

public enum HealthSyncAPIError: Error, Equatable, Sendable {
    case invalidResponse
    case unsuccessfulStatus(Int)
    case missingDataEnvelope
}

public final class URLSessionHealthSyncAPIClient: HealthSyncAPIClient {
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
        var urlRequest = URLRequest(url: Self.healthSyncURL(baseURL: baseURL))
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.httpBody = try encoder.encode(request)

        let (data, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw HealthSyncAPIError.invalidResponse
        }
        guard 200 ..< 300 ~= httpResponse.statusCode else {
            throw HealthSyncAPIError.unsuccessfulStatus(httpResponse.statusCode)
        }
        let envelope = try decoder.decode(APIEnvelope<HealthSyncResponse>.self, from: data)
        guard let syncResponse = envelope.data else {
            throw HealthSyncAPIError.missingDataEnvelope
        }
        return syncResponse
    }

    public static func healthSyncURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/health/sync")
    }
}

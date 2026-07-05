import Foundation

enum BaselineAppConfigurationError: Error, Equatable {
    case invalidAPIBaseURL(String)
}

struct BaselineAppConfiguration: Equatable {
    static let environmentKey = "BASELINE_API_BASE_URL"
    static let apiAuthTokenEnvironmentKey = "BASELINE_API_AUTH_TOKEN"
    static let infoPlistKey = "BaselineAPIBaseURL"
    static let apiAuthTokenInfoPlistKey = "BaselineAPIAuthToken"
    static let localDevelopmentAPIBaseURL: URL = {
        var components = URLComponents()
        components.scheme = "http"
        components.host = "127.0.0.1"
        components.port = 8000
        return components.url ?? URL(fileURLWithPath: "/")
    }()

    var apiBaseURL: URL
    var apiAuthToken: String?

    static func current(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        infoDictionary: [String: Any] = Bundle.main.infoDictionary ?? [:]
    ) throws -> BaselineAppConfiguration {
        let rawValue = environment[environmentKey]
            ?? infoDictionary[infoPlistKey] as? String
            ?? localDevelopmentAPIBaseURL.absoluteString
        guard let url = URL(string: rawValue), url.scheme != nil, url.host != nil else {
            throw BaselineAppConfigurationError.invalidAPIBaseURL(rawValue)
        }
        let token = environment[apiAuthTokenEnvironmentKey]
            ?? infoDictionary[apiAuthTokenInfoPlistKey] as? String
        return BaselineAppConfiguration(apiBaseURL: url, apiAuthToken: token)
    }

    static func resolvedCurrentAPIBaseURL() -> URL {
        (try? current().apiBaseURL) ?? localDevelopmentAPIBaseURL
    }

    static func resolvedCurrentAPIAuthToken() -> String? {
        try? current().apiAuthToken
    }
}

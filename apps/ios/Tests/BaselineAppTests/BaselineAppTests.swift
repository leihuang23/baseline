import Foundation
import XCTest
@testable import BaselineApp

final class BaselineAppTests: XCTestCase {
    func testAPIBaseURLUsesEnvironmentFirst() throws {
        let configuration = try BaselineAppConfiguration.current(
            environment: [
                BaselineAppConfiguration.environmentKey: "https://api.example.test",
            ],
            infoDictionary: [
                BaselineAppConfiguration.infoPlistKey: "https://bundle.example.test",
            ]
        )

        XCTAssertEqual(configuration.apiBaseURL.absoluteString, "https://api.example.test")
    }

    func testAPIBaseURLFallsBackToInfoPlist() throws {
        let configuration = try BaselineAppConfiguration.current(
            environment: [:],
            infoDictionary: [
                BaselineAppConfiguration.infoPlistKey: "https://bundle.example.test",
            ]
        )

        XCTAssertEqual(configuration.apiBaseURL.absoluteString, "https://bundle.example.test")
    }

    func testAPIBaseURLRejectsInvalidValue() {
        XCTAssertThrowsError(
            try BaselineAppConfiguration.current(
                environment: [
                    BaselineAppConfiguration.environmentKey: "not a url",
                ],
                infoDictionary: [:]
            )
        ) { error in
            XCTAssertEqual(
                error as? BaselineAppConfigurationError,
                .invalidAPIBaseURL("not a url")
            )
        }
    }
}

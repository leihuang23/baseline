import Foundation

public struct PermissionFlowResult: Equatable, Sendable {
    public var requested: [HealthCategory]
    public var granted: [HealthCategory]
    public var denied: [HealthCategory]
    public var rationales: [HealthCategory: String]

    public init(
        requested: [HealthCategory],
        granted: [HealthCategory],
        denied: [HealthCategory],
        rationales: [HealthCategory: String]
    ) {
        self.requested = requested
        self.granted = granted
        self.denied = denied
        self.rationales = rationales
    }

    public var isDegraded: Bool {
        !denied.isEmpty
    }

    public func consentRecord(
        processingMode: PrivacyMode,
        wakeTime: WakeTime = WakeTime()
    ) -> ConsentRecord {
        ConsentRecord(
            enabledCategories: granted,
            deniedCategories: denied,
            processingMode: processingMode,
            wakeTime: wakeTime
        )
    }
}

public protocol HealthAuthorizationClient: Sendable {
    func requestAuthorization(for categories: [HealthCategory]) async throws -> Set<HealthCategory>
}

public final class PermissionCoordinator: Sendable {
    private let healthAuthorizationClient: any HealthAuthorizationClient

    public init(healthAuthorizationClient: any HealthAuthorizationClient) {
        self.healthAuthorizationClient = healthAuthorizationClient
    }

    public func requestPermissions(
        for enabledCategories: [HealthCategory]
    ) async throws -> PermissionFlowResult {
        let requested = Array(Set(enabledCategories)).sorted { $0.rawValue < $1.rawValue }
        let grantedSet = try await healthAuthorizationClient.requestAuthorization(for: requested)
        let granted = requested.filter { grantedSet.contains($0) }
        let denied = requested.filter { !grantedSet.contains($0) }
        let rationales = Dictionary(
            uniqueKeysWithValues: requested.map { ($0, $0.permissionRationale) }
        )
        return PermissionFlowResult(
            requested: requested,
            granted: granted,
            denied: denied,
            rationales: rationales
        )
    }
}

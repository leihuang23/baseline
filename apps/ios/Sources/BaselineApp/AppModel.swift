#if os(iOS)
import BaselineCore
import Foundation
import UIKit

@MainActor
final class BaselineAppModel: ObservableObject {
    @Published var onboardingComplete = false
    @Published var isDemoMode = false
    @Published var privacyMode: PrivacyMode = .hybrid
    @Published var enabledCategories = Set(HealthCategory.allCases)
    @Published var grantedCategories: Set<HealthCategory> = []
    @Published var deniedCategories: Set<HealthCategory> = []
    @Published var demoSamples: [HealthSample] = []
    @Published var lastSyncedAt: Date?
    @Published var syncMessage = "Not synced yet"
    @Published var isSyncing = false

    private let permissionCoordinator: PermissionCoordinator
    private let anchorStore: any AnchorPersisting
    private let consentStore: any ConsentPersisting
    private let healthKitClient: HealthKitClient
    private let apiBaseURL: URL
    private var syncEngine: HealthSyncEngine?

    var currentAPIBaseURL: URL {
        apiBaseURL
    }

    init(apiBaseURL: URL = BaselineAppConfiguration.resolvedCurrentAPIBaseURL()) {
        self.apiBaseURL = apiBaseURL
        healthKitClient = HealthKitClient()
        permissionCoordinator = PermissionCoordinator(healthAuthorizationClient: healthKitClient)
        let supportDirectory = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0].appendingPathComponent("Baseline", isDirectory: true)
        anchorStore = try! FileAnchorStore(rootURL: supportDirectory)
        consentStore = try! FileConsentStore(rootURL: supportDirectory)
        if let restored = try? LaunchStateRestorer.restore(
            consentStore: consentStore,
            anchorStore: anchorStore
        ) {
            privacyMode = restored.consent.processingMode
            grantedCategories = Set(restored.consent.enabledCategories)
            deniedCategories = Set(restored.consent.deniedCategories)
            enabledCategories = grantedCategories.union(deniedCategories)
            onboardingComplete = true
            lastSyncedAt = restored.lastSyncedAt
            syncMessage = restored.lastSyncedAt == nil
                ? "Ready to sync selected HealthKit categories."
                : "Last sync restored from saved anchors."
        }
        BackgroundRefreshScheduler.register { [weak self] in
            await self?.syncInBackground() ?? false
        }
        BackgroundRefreshScheduler.schedule()
    }

    var consentRecord: ConsentRecord {
        let hasPermissionDecision = !grantedCategories.isEmpty || !deniedCategories.isEmpty
        let syncableCategories = hasPermissionDecision
            ? grantedCategories.intersection(enabledCategories)
            : enabledCategories
        ConsentRecord(
            enabledCategories: Array(syncableCategories).sorted { $0.rawValue < $1.rawValue },
            deniedCategories: Array(deniedCategories).sorted { $0.rawValue < $1.rawValue },
            processingMode: privacyMode
        )
    }

    func enterDemoMode() {
        isDemoMode = true
        onboardingComplete = true
        grantedCategories = []
        deniedCategories = []
        demoSamples = DemoHealthData.samples
        lastSyncedAt = Date()
        syncMessage = "Demo mode loaded \(demoSamples.count) synthetic sample(s). HealthKit is not accessed."
    }

    func completeOnboarding() async {
        do {
            let result = try await permissionCoordinator.requestPermissions(
                for: Array(enabledCategories)
            )
            grantedCategories = Set(result.granted)
            deniedCategories = Set(result.denied)
            try? consentStore.saveConsent(result.consentRecord(processingMode: privacyMode))
            onboardingComplete = true
            syncMessage = result.isDegraded
                ? "Some HealthKit categories are unavailable; Baseline will sync the rest."
                : "HealthKit permissions are ready."
        } catch {
            grantedCategories = []
            deniedCategories = enabledCategories
            try? consentStore.saveConsent(
                ConsentRecord(
                    enabledCategories: [],
                    deniedCategories: Array(enabledCategories).sorted { $0.rawValue < $1.rawValue },
                    processingMode: privacyMode
                )
            )
            onboardingComplete = true
            syncMessage = "HealthKit authorization failed; you can continue in degraded mode."
        }
    }

    func syncNow() async {
        guard !isDemoMode else {
            lastSyncedAt = Date()
            syncMessage = "Demo data refreshed."
            return
        }
        let categories = grantedCategories.intersection(enabledCategories)
        guard !categories.isEmpty else {
            syncMessage = "No HealthKit categories are enabled for sync."
            return
        }
        isSyncing = true
        defer { isSyncing = false }

        do {
            let engine = try buildSyncEngine(for: categories)
            let outcome = try await engine.syncNow(
                consent: consentRecord,
                deviceID: UIDevice.current.identifierForVendor?.uuidString ?? "ios-device"
            )
            syncEngine = engine
            lastSyncedAt = outcome.syncedAt
            if outcome.skippedCategories.isEmpty {
                syncMessage = "Synced \(outcome.sentSampleCount) sample(s)."
            } else {
                let skipped = outcome.skippedCategories.map(\.title).joined(separator: ", ")
                syncMessage = "Synced \(outcome.sentSampleCount) sample(s). Skipped: \(skipped)."
            }
        } catch HealthSyncEngineError.noReadableCategories {
            syncMessage = "Sync could not read selected HealthKit categories. Check permissions and try again."
        } catch {
            syncMessage = "Sync could not finish. Baseline will retry the saved batch next time."
        }
    }

    func syncInBackground() async -> Bool {
        guard onboardingComplete, !isDemoMode else {
            return false
        }
        await syncNow()
        return lastSyncedAt != nil
    }

    private func buildSyncEngine(for categories: Set<HealthCategory>) throws -> HealthSyncEngine {
        let apiClient = URLSessionHealthSyncAPIClient(baseURL: apiBaseURL)
        healthKitClient.enabledCategories = categories
        return HealthSyncEngine(
            anchorStore: anchorStore,
            healthKitReader: healthKitClient,
            apiClient: apiClient
        )
    }
}
#endif

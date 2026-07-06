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
    @Published var wakeTime: WakeTime = WakeTime()
    @Published var demoSamples: [HealthSample] = []
    @Published var lastSyncedAt: Date?
    @Published var syncMessage = "Not synced yet"
    @Published var isSyncing = false

    private let permissionCoordinator: PermissionCoordinator
    private let anchorStore: any AnchorPersisting
    private let consentStore: any ConsentPersisting
    private let healthKitClient: HealthKitClient
    private let healthKitReader: any HealthKitReading
    private let apiClient: any HealthSyncAPIClient
    private let apiBaseURL: URL
    private let apiAuthToken: String?
    private var syncEngine: HealthSyncEngine?
    private var acceptedConsent: ConsentRecord?

    var currentAPIBaseURL: URL {
        apiBaseURL
    }

    var currentAPIAuthToken: String? {
        apiAuthToken
    }

    init(
        apiBaseURL: URL = BaselineAppConfiguration.resolvedCurrentAPIBaseURL(),
        apiAuthToken: String? = BaselineAppConfiguration.resolvedCurrentAPIAuthToken(),
        authorizationClient: (any HealthAuthorizationClient)? = nil,
        apiClient: (any HealthSyncAPIClient)? = nil,
        anchorStore: (any AnchorPersisting)? = nil,
        consentStore: (any ConsentPersisting)? = nil,
        healthKitReader: (any HealthKitReading)? = nil
    ) {
        self.apiBaseURL = apiBaseURL
        self.apiAuthToken = apiAuthToken
        self.apiClient = apiClient ?? URLSessionHealthSyncAPIClient(
            baseURL: apiBaseURL,
            apiAuthToken: apiAuthToken
        )
        healthKitClient = HealthKitClient()
        self.healthKitReader = healthKitReader ?? healthKitClient
        permissionCoordinator = PermissionCoordinator(
            healthAuthorizationClient: authorizationClient ?? healthKitClient
        )
        let resolvedAnchorStore: any AnchorPersisting
        let resolvedConsentStore: any ConsentPersisting
        if let anchorStore, let consentStore {
            resolvedAnchorStore = anchorStore
            resolvedConsentStore = consentStore
        } else {
            let supportDirectory = FileManager.default.urls(
                for: .applicationSupportDirectory,
                in: .userDomainMask
            )[0].appendingPathComponent("Baseline", isDirectory: true)
            resolvedAnchorStore = try! FileAnchorStore(rootURL: supportDirectory)
            resolvedConsentStore = try! FileConsentStore(rootURL: supportDirectory)
        }
        self.anchorStore = resolvedAnchorStore
        self.consentStore = resolvedConsentStore
        if let restored = try? LaunchStateRestorer.restore(
            consentStore: resolvedConsentStore,
            anchorStore: resolvedAnchorStore
        ) {
            privacyMode = restored.consent.processingMode
            acceptedConsent = restored.consent
            grantedCategories = Set(restored.consent.enabledCategories)
            deniedCategories = Set(restored.consent.deniedCategories)
            enabledCategories = grantedCategories.union(deniedCategories)
            wakeTime = restored.consent.wakeTime
            onboardingComplete = true
            lastSyncedAt = restored.lastSyncedAt
            syncMessage = restored.lastSyncedAt == nil
                ? "Ready to sync selected HealthKit categories."
                : "Last sync restored from saved anchors."
        }
        Task {
            await BackgroundRefreshScheduler.register(
                syncHandler: { [weak self] in
                    await self?.syncInBackground() ?? false
                },
                wakeTimeProvider: { [weak self] in
                    self?.wakeTime ?? WakeTime()
                }
            )
            BackgroundRefreshScheduler.schedule()
            _ = await BackgroundRefreshScheduler.requestNotificationAuthorization()
            await BackgroundRefreshScheduler.scheduleMorningReminder()
        }
    }

    var consentRecord: ConsentRecord {
        let hasPermissionDecision = !grantedCategories.isEmpty || !deniedCategories.isEmpty
        let syncableCategories = hasPermissionDecision
            ? grantedCategories.intersection(enabledCategories)
            : enabledCategories
        ConsentRecord(
            enabledCategories: Array(syncableCategories).sorted { $0.rawValue < $1.rawValue },
            deniedCategories: Array(deniedCategories).sorted { $0.rawValue < $1.rawValue },
            processingMode: privacyMode,
            wakeTime: wakeTime
        )
    }

    func enterDemoMode() {
        isDemoMode = true
        onboardingComplete = true
        acceptedConsent = nil
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
            let localConsent = result.consentRecord(processingMode: privacyMode)
            let successMessage = result.isDegraded
                ? "Some HealthKit categories are unavailable; Baseline will sync the rest."
                : "HealthKit permissions are ready."
            await persistOnboardingConsent(localConsent, successMessage: successMessage)
        } catch {
            grantedCategories = []
            deniedCategories = enabledCategories
            let localConsent = ConsentRecord(
                enabledCategories: [],
                deniedCategories: Array(enabledCategories).sorted { $0.rawValue < $1.rawValue },
                processingMode: privacyMode
            )
            await persistOnboardingConsent(
                localConsent,
                successMessage: "HealthKit authorization failed; you can continue in degraded mode."
            )
        }
    }

    func syncNow() async {
        guard !isDemoMode else {
            lastSyncedAt = Date()
            syncMessage = "Demo data refreshed."
            return
        }
        let consent = currentAcceptedConsent()
        guard consent.processingMode != .localOnly else {
            try? anchorStore.clearPendingBatch()
            syncMessage = "Local-only mode keeps HealthKit data on device."
            return
        }
        let categories = Set(consent.enabledCategories).intersection(enabledCategories)
        guard !categories.isEmpty else {
            syncMessage = "No HealthKit categories are enabled for sync."
            return
        }
        isSyncing = true
        defer { isSyncing = false }

        do {
            let engine = try buildSyncEngine(for: categories)
            let outcome = try await engine.syncNow(
                consent: consent,
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
        } catch HealthSyncEngineError.localOnlySyncDisabled {
            try? anchorStore.clearPendingBatch()
            syncMessage = "Local-only mode keeps HealthKit data on device."
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
        healthKitClient.enabledCategories = categories
        return HealthSyncEngine(
            anchorStore: anchorStore,
            healthKitReader: healthKitReader,
            apiClient: apiClient
        )
    }

    private func recordServerConsentIfNeeded(_ consent: ConsentRecord) async throws -> ConsentRecord {
        guard consent.processingMode != .localOnly else {
            return consent
        }
        let response = try await apiClient.recordConsent(ConsentRecordRequest(consent: consent))
        return ConsentRecord(
            consentVersion: response.consentVersion,
            grantedAt: consent.grantedAt,
            enabledCategories: consent.enabledCategories,
            deniedCategories: consent.deniedCategories,
            processingMode: consent.processingMode
        )
    }

    private func persistOnboardingConsent(
        _ consent: ConsentRecord,
        successMessage: String
    ) async {
        do {
            let persistedConsent = try await recordServerConsentIfNeeded(consent)
            try? consentStore.saveConsent(persistedConsent)
            acceptedConsent = persistedConsent
            onboardingComplete = true
            syncMessage = successMessage
        } catch {
            onboardingComplete = false
            syncMessage = "Consent could not be recorded; check connection and retry."
        }
    }

    private func currentAcceptedConsent() -> ConsentRecord {
        if let stored = try? consentStore.loadConsent() {
            acceptedConsent = stored
            return stored
        }
        return acceptedConsent ?? consentRecord
    }

}
#endif

#if os(iOS)
import BaselineCore
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: BaselineAppModel

    var body: some View {
        NavigationStack {
            if model.onboardingComplete {
                SyncSettingsView()
            } else {
                OnboardingView()
            }
        }
    }
}

struct OnboardingView: View {
    @EnvironmentObject private var model: BaselineAppModel

    var body: some View {
        List {
            Section("Product boundary") {
                Text("Baseline provides wellness and fitness decision support, not medical diagnosis or treatment.")
                    .font(.body)
                Button("Use demo mode") {
                    model.enterDemoMode()
                }
            }

            Section("Privacy mode") {
                Picker("Processing mode", selection: $model.privacyMode) {
                    ForEach(PrivacyMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                Text(model.privacyMode.consentSummary)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("HealthKit data") {
                ForEach(HealthCategory.allCases) { category in
                    Toggle(
                        isOn: Binding(
                            get: { model.enabledCategories.contains(category) },
                            set: { isEnabled in
                                if isEnabled {
                                    model.enabledCategories.insert(category)
                                } else {
                                    model.enabledCategories.remove(category)
                                }
                            }
                        )
                    ) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(category.title)
                            Text(category.permissionRationale)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            Section {
                Button {
                    Task { await model.completeOnboarding() }
                } label: {
                    Text("Continue")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.enabledCategories.isEmpty)
            }
        }
        .navigationTitle("Baseline")
    }
}

struct SyncSettingsView: View {
    @EnvironmentObject private var model: BaselineAppModel

    var body: some View {
        List {
            Section("Sync") {
                HStack {
                    Text("Last synced")
                    Spacer()
                    Text(lastSyncedText)
                        .foregroundStyle(.secondary)
                }
                Button {
                    Task { await model.syncNow() }
                } label: {
                    if model.isSyncing {
                        ProgressView()
                    } else {
                        Text("Sync now")
                    }
                }
                .disabled(model.isSyncing)
                Text(model.syncMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            if !model.deniedCategories.isEmpty {
                Section("Degraded categories") {
                    ForEach(Array(model.deniedCategories).sorted { $0.rawValue < $1.rawValue }) { category in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(category.title)
                            Text("Not available. Baseline will continue without this signal.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            if model.isDemoMode {
                Section("Demo data") {
                    Text("\(model.demoSamples.count) synthetic sample(s)")
                    Text("HealthKit is not accessed in demo mode.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Consent") {
                Text("Consent version: \(ConsentRecord.currentVersion)")
                Text("Mode: \(model.privacyMode.title)")
                Text("Categories: \(model.consentRecord.enabledCategories.map(\.title).joined(separator: ", "))")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle(model.isDemoMode ? "Demo Baseline" : "Baseline Sync")
    }

    private var lastSyncedText: String {
        guard let lastSyncedAt = model.lastSyncedAt else {
            return "Never"
        }
        return lastSyncedAt.formatted(date: .abbreviated, time: .shortened)
    }
}
#endif

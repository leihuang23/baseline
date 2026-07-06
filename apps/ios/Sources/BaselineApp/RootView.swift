#if os(iOS)
import BaselineCore
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: BaselineAppModel

    var body: some View {
        NavigationStack {
            if model.onboardingComplete {
                BaselineHomeView(
                    apiBaseURL: model.currentAPIBaseURL,
                    apiAuthToken: model.currentAPIAuthToken,
                    privacyMode: { model.privacyMode }
                )
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

            Section("Morning routine") {
                WakeTimePicker(wakeTime: $model.wakeTime)
                Text("Background refresh and the optional morning reminder are scheduled near this time.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
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
        .onChange(of: model.wakeTime) { _, _ in
            BackgroundRefreshScheduler.schedule()
            Task {
                await BackgroundRefreshScheduler.scheduleMorningReminder()
            }
        }
    }
}

struct BaselineHomeView: View {
    @EnvironmentObject private var appModel: BaselineAppModel
    @StateObject private var briefingModel: DailyBriefingViewModel
    @StateObject private var checkInModel: DailyCheckInViewModel
    @StateObject private var goalsModel: GoalsViewModel
    @StateObject private var settingsModel: SettingsViewModel
    private let privacyMode: () -> PrivacyMode
    private let apiClient: URLSessionHealthSyncAPIClient

    init(apiBaseURL: URL, apiAuthToken: String?, privacyMode: @escaping () -> PrivacyMode) {
        let apiClient = URLSessionHealthSyncAPIClient(
            baseURL: apiBaseURL,
            apiAuthToken: apiAuthToken
        )
        let supportDirectory = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0].appendingPathComponent("Baseline", isDirectory: true)
        self.privacyMode = privacyMode
        self.apiClient = apiClient
        _briefingModel = StateObject(
            wrappedValue: DailyBriefingViewModel(
                apiClient: apiClient,
                briefingStore: try! FileBriefingStore(rootURL: supportDirectory),
                privacyMode: privacyMode
            )
        )
        _checkInModel = StateObject(
            wrappedValue: DailyCheckInViewModel(apiClient: apiClient, privacyMode: privacyMode)
        )
        _goalsModel = StateObject(wrappedValue: GoalsViewModel(apiClient: apiClient))
        _settingsModel = StateObject(wrappedValue: SettingsViewModel(apiClient: apiClient))
    }

    var body: some View {
        TabView {
            DailyBriefingView(viewModel: briefingModel)
                .tabItem {
                    Label("Briefing", systemImage: "sunrise")
                }
            DailyCheckInView(viewModel: checkInModel, privacyMode: privacyMode)
                .tabItem {
                    Label("Check-in", systemImage: "checkmark.circle")
                }
            GoalsView(viewModel: goalsModel)
                .tabItem {
                    Label("Goals", systemImage: "target")
                }
            SyncSettingsView()
                .tabItem {
                    Label("Sync", systemImage: "arrow.triangle.2.circlepath")
                }
            SettingsView(viewModel: settingsModel, appModel: appModel)
                .tabItem {
                    Label("Settings", systemImage: "gear")
                }
        }
        .task {
            briefingModel.setSyncAction {
                await appModel.syncNow()
            }
        }
    }
}

struct GoalsView: View {
    @ObservedObject var viewModel: GoalsViewModel

    var body: some View {
        List {
            Section("New goal") {
                Picker("Category", selection: $viewModel.selectedCategory) {
                    ForEach(GoalCategory.allCases) { category in
                        Text(category.title).tag(category)
                    }
                }
                Stepper("Priority \(viewModel.priority)", value: $viewModel.priority, in: 1...5)
                Picker("Horizon", selection: $viewModel.selectedHorizon) {
                    ForEach(GoalTimeHorizon.allCases) { horizon in
                        Text(horizon.title).tag(horizon)
                    }
                }
                TextField("Success indicator", text: $viewModel.successIndicator)
                TextField("Constraints", text: $viewModel.constraints, axis: .vertical)
                    .lineLimit(1...3)
                Button {
                    Task { await viewModel.createGoal() }
                } label: {
                    if viewModel.isSaving {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Text("Create goal")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(viewModel.isSaving)
            }

            Section("Goals") {
                if viewModel.goals.isEmpty {
                    Text("No goals yet.")
                        .foregroundStyle(.secondary)
                }
                ForEach(viewModel.goals) { goal in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text(goal.category.title)
                                .font(.headline)
                            Spacer()
                            Text(goal.active ? "Active" : "Paused")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Text(goal.successMetric)
                        Text("Priority \(goal.priority) | \(goal.timeHorizon.title)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if let notes = goal.constraints["notes"], !notes.isEmpty {
                            Text(notes)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if goal.active {
                            Button("Pause goal") {
                                Task { await viewModel.pauseGoal(id: goal.id) }
                            }
                        }
                    }
                }
            }

            Section {
                Text(viewModel.statusMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }
        }
        .navigationTitle("Goals")
        .task {
            await viewModel.loadGoals()
        }
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

            Section("Morning routine") {
                WakeTimePicker(wakeTime: $model.wakeTime)
                Text("Background refresh and the optional morning reminder are scheduled near this time.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
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
        .onChange(of: model.wakeTime) { _, _ in
            BackgroundRefreshScheduler.schedule()
            Task {
                await BackgroundRefreshScheduler.scheduleMorningReminder()
            }
        }
    }

    private var lastSyncedText: String {
        guard let lastSyncedAt = model.lastSyncedAt else {
            return "Never"
        }
        return lastSyncedAt.formatted(date: .abbreviated, time: .shortened)
    }
}

private struct WakeTimePicker: View {
    @Binding var wakeTime: WakeTime

    var body: some View {
        DatePicker(
            "Wake time",
            selection: Binding(
                get: { wakeTime.date() },
                set: { wakeTime = WakeTime(date: $0) }
            ),
            displayedComponents: .hourAndMinute
        )
    }
}

private extension WakeTime {
    func date(calendar: Calendar = .current) -> Date {
        var components = DateComponents()
        components.hour = hour
        components.minute = minute
        return calendar.date(from: components) ?? Date()
    }

    init(date: Date, calendar: Calendar = .current) {
        let components = calendar.dateComponents([.hour, .minute], from: date)
        self.init(hour: components.hour ?? 7, minute: components.minute ?? 0)
    }
}
#endif

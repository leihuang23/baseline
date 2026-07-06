#if os(iOS)
import BaselineCore
import SwiftUI

typealias SettingsAPIClient = HealthSyncAPIClient & CheckInAPIClient & DataControlsAPIClient & DailyBriefingAPIClient

@MainActor
final class SettingsViewModel: ObservableObject {
    @Published var exportScope: DataExportScope = .all
    @Published var exportFormat: DataExportFormat = .json
    @Published var includeRawData: Bool = false
    @Published var includeModelTraces: Bool = false
    @Published private(set) var exportResponse: DataExportResponse?
    @Published private(set) var isRequestingExport = false
    @Published private(set) var exportError: String?
    @Published private(set) var exportFileURL: URL?

    @Published private(set) var isDeletingAll = false
    @Published var showDeleteAllConfirmation = false
    @Published private(set) var deleteAllResult: DataDeleteResponse?
    @Published private(set) var deleteAllError: String?

    @Published var checkInIDToDelete = ""
    @Published private(set) var checkInDeleteError: String?
    @Published private(set) var checkInNoteDeleteError: String?

    @Published private(set) var llmSettings: LLMSettingsResponse?
    @Published private(set) var llmSettingsError: String?

    let apiClient: any SettingsAPIClient

    init(apiClient: any SettingsAPIClient) {
        self.apiClient = apiClient
    }

    var canRequestExport: Bool {
        !isRequestingExport
    }

    var canDownloadExport: Bool {
        exportResponse?.downloadURL != nil && !isRequestingExport
    }

    func requestExport() async {
        guard !isRequestingExport else { return }
        isRequestingExport = true
        exportError = nil
        exportFileURL = nil
        defer { isRequestingExport = false }

        do {
            exportResponse = try await apiClient.requestDataExport(
                DataExportRequest(
                    exportScope: exportScope,
                    format: exportFormat,
                    includeRawData: includeRawData,
                    includeModelTraces: includeModelTraces
                )
            )
        } catch {
            exportError = "Export request failed. Try again."
        }
    }

    func downloadAndShare() async {
        guard let exportResponse else { return }
        exportError = nil
        exportFileURL = nil
        do {
            let decrypted = try await apiClient.downloadDecryptedDataExport(exportResponse)
            let url = try writeExportFile(decrypted, response: exportResponse)
            exportFileURL = url
        } catch {
            exportError = "Export download or decryption failed."
        }
    }

    func clearExportFile() {
        if let url = exportFileURL {
            try? FileManager.default.removeItem(at: url)
            exportFileURL = nil
        }
    }

    func deleteAll() async {
        guard !isDeletingAll else { return }
        isDeletingAll = true
        deleteAllError = nil
        deleteAllResult = nil
        defer { isDeletingAll = false }

        do {
            deleteAllResult = try await apiClient.deleteAllData()
        } catch {
            deleteAllError = "Delete all failed. Try again."
        }
    }

    func deleteCheckIn() async {
        guard let id = parseCheckInID() else { return }
        checkInDeleteError = nil
        do {
            try await apiClient.deleteDailyCheckIn(id: id)
            checkInIDToDelete = ""
        } catch {
            checkInDeleteError = "Check-in could not be deleted."
        }
    }

    func deleteCheckInNote() async {
        guard let id = parseCheckInID() else { return }
        checkInNoteDeleteError = nil
        do {
            try await apiClient.deleteDailyCheckInNote(id: id)
            checkInIDToDelete = ""
        } catch {
            checkInNoteDeleteError = "Check-in note could not be deleted."
        }
    }

    func fetchLLMSettings() async {
        llmSettingsError = nil
        do {
            llmSettings = try await apiClient.fetchLLMSettings()
        } catch {
            llmSettingsError = "LLM settings could not be loaded."
        }
    }

    private func parseCheckInID() -> UUID? {
        let trimmed = checkInIDToDelete.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let id = UUID(uuidString: trimmed) else {
            checkInDeleteError = "Enter a valid check-in ID."
            return nil
        }
        return id
    }

    private func writeExportFile(_ data: Data, response: DataExportResponse) throws -> URL {
        let ext = exportFormat == .csv ? "csv" : "json"
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("baseline-export-\(response.exportJobID.uuidString).\(ext)")
        try data.write(to: url, options: .atomic)
        return url
    }
}

struct SettingsView: View {
    @ObservedObject private var appModel: BaselineAppModel
    @ObservedObject private var viewModel: SettingsViewModel
    @State private var showConsentManagement = false

    init(viewModel: SettingsViewModel, appModel: BaselineAppModel) {
        self.viewModel = viewModel
        self.appModel = appModel
    }

    var body: some View {
        List {
            privacyModeSection
            llmSettingsSection
            exportSection
            dataManagementSection
            consentSection
        }
        .navigationTitle("Settings")
        .task {
            await viewModel.fetchLLMSettings()
        }
        .sheet(isPresented: $showConsentManagement) {
            NavigationStack {
                ConsentManagementView(apiClient: viewModel.apiClient)
            }
        }
    }

    private var privacyModeSection: some View {
        Section("Privacy mode") {
            Picker("Processing mode", selection: $appModel.privacyMode) {
                ForEach(PrivacyMode.allCases) { mode in
                    Text(mode.title).tag(mode)
                }
            }
            Text(appModel.privacyMode.consentSummary)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Button("Record mode change") {
                Task {
                    await appModel.updatePrivacyMode(appModel.privacyMode)
                }
            }
            .disabled(viewModel.isRequestingExport || viewModel.isDeletingAll)
        }
    }

    private var llmSettingsSection: some View {
        Section("LLM provider") {
            if let settings = viewModel.llmSettings {
                LabeledContent("Provider", value: settings.provider)
                LabeledContent("Cheap model", value: settings.cheapModel)
                LabeledContent("Strong model", value: settings.strongModel)
                LabeledContent("Fallback model", value: settings.fallbackModel)
            } else if let error = viewModel.llmSettingsError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            } else {
                ProgressView("Loading LLM settings")
            }
            Text("Runtime changes are operator-controlled via server configuration.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var exportSection: some View {
        Section("Export") {
            Picker("Scope", selection: $viewModel.exportScope) {
                ForEach(DataExportScope.allCases) { scope in
                    Text(scope.title).tag(scope)
                }
            }
            Picker("Format", selection: $viewModel.exportFormat) {
                ForEach(DataExportFormat.allCases) { format in
                    Text(format.title).tag(format)
                }
            }
            Toggle("Include raw data", isOn: $viewModel.includeRawData)
            Toggle("Include model traces", isOn: $viewModel.includeModelTraces)

            Button {
                Task { await viewModel.requestExport() }
            } label: {
                if viewModel.isRequestingExport {
                    ProgressView()
                } else {
                    Text("Request export")
                }
            }
            .disabled(!viewModel.canRequestExport)

            if let response = viewModel.exportResponse {
                LabeledContent("Status", value: response.status)
                if response.downloadURL != nil {
                    Button {
                        Task { await viewModel.downloadAndShare() }
                    } label: {
                        Text("Download and share")
                    }
                }
            }

            if let fileURL = viewModel.exportFileURL {
                ShareLink(item: fileURL) {
                    Label("Share decrypted export", systemImage: "square.and.arrow.up")
                }
            }

            if let error = viewModel.exportError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
    }

    private var dataManagementSection: some View {
        Section("Data management") {
            TextField("Check-in ID", text: $viewModel.checkInIDToDelete)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
            Button("Delete check-in", role: .destructive) {
                Task { await viewModel.deleteCheckIn() }
            }
            if let error = viewModel.checkInDeleteError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
            Button("Delete check-in note", role: .destructive) {
                Task { await viewModel.deleteCheckInNote() }
            }
            if let error = viewModel.checkInNoteDeleteError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }

            Button("Delete all Baseline data", role: .destructive) {
                viewModel.showDeleteAllConfirmation = true
            }
            .disabled(viewModel.isDeletingAll)
            .confirmationDialog(
                "Delete all data?",
                isPresented: $viewModel.showDeleteAllConfirmation,
                titleVisibility: .visible
            ) {
                Button("Delete all", role: .destructive) {
                    Task { await viewModel.deleteAll() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This permanently removes all synced health data, check-ins, briefings, and goals.")
            }

            if let result = viewModel.deleteAllResult {
                Text("Deleted: \(result.deleted.map { "\($0.key): \($0.value)" }.joined(separator: ", "))")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            if let error = viewModel.deleteAllError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
    }

    private var consentSection: some View {
        Section("Consent") {
            Button("Manage consent and disclosures") {
                showConsentManagement = true
            }
            Text("Version: \(ConsentRecord.currentVersion)")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }
}

private extension DataExportScope {
    var title: String {
        switch self {
        case .all:
            "All"
        case .health:
            "Health"
        case .checkins:
            "Check-ins"
        case .briefings:
            "Briefings"
        case .recommendations:
            "Recommendations"
        case .memory:
            "Memory"
        case .consent:
            "Consent"
        }
    }
}

private extension DataExportFormat {
    var title: String {
        switch self {
        case .json:
            "JSON"
        case .csv:
            "CSV"
        }
    }
}
#endif

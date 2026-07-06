#if os(iOS)
import BaselineCore
import SwiftUI

@MainActor
final class ConsentManagementViewModel: ObservableObject {
    @Published private(set) var consentHistory: ConsentHistoryResponse?
    @Published private(set) var modelDisclosures: ModelDisclosureResponse?
    @Published private(set) var statusMessage: String?
    @Published private(set) var isLoading = false
    @Published var categoriesToRevoke: Set<HealthCategory> = []

    private let apiClient: any DataControlsAPIClient

    init(apiClient: any DataControlsAPIClient) {
        self.apiClient = apiClient
    }

    func disableExternalLLM() async {
        isLoading = true
        defer { isLoading = false }
        do {
            _ = try await apiClient.disableExternalLLM(
                DisableExternalLLMRequest(consentVersion: ConsentRecord.currentVersion)
            )
            statusMessage = "External LLM disabled."
        } catch {
            statusMessage = "Could not disable external LLM."
        }
    }

    func revokeCloudProcessing() async {
        isLoading = true
        defer { isLoading = false }
        do {
            _ = try await apiClient.disableCloudProcessing(
                ConsentRevocationRequest(
                    consentVersion: ConsentRecord.currentVersion,
                    revokeCloudProcessing: true,
                    revokeExternalLLM: true,
                    revokeRawNoteProcessing: true,
                    revokeHealthCategories: nil
                )
            )
            statusMessage = "Cloud processing revoked."
        } catch {
            statusMessage = "Could not revoke cloud processing."
        }
    }

    func revokeRawNoteProcessing() async {
        isLoading = true
        defer { isLoading = false }
        do {
            _ = try await apiClient.disableCloudProcessing(
                ConsentRevocationRequest(
                    consentVersion: ConsentRecord.currentVersion,
                    revokeCloudProcessing: false,
                    revokeExternalLLM: false,
                    revokeRawNoteProcessing: true,
                    revokeHealthCategories: nil
                )
            )
            statusMessage = "Raw note processing revoked."
        } catch {
            statusMessage = "Could not revoke raw note processing."
        }
    }

    func revokeSelectedCategories() async {
        guard !categoriesToRevoke.isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        let backendCategories = categoriesToRevoke.flatMap(backendCategory(for:))
        do {
            _ = try await apiClient.disableCloudProcessing(
                ConsentRevocationRequest(
                    consentVersion: ConsentRecord.currentVersion,
                    revokeCloudProcessing: false,
                    revokeExternalLLM: false,
                    revokeRawNoteProcessing: false,
                    revokeHealthCategories: Array(Set(backendCategories))
                )
            )
            statusMessage = "Selected health categories revoked."
            categoriesToRevoke.removeAll()
        } catch {
            statusMessage = "Could not revoke health categories."
        }
    }

    func fetchConsentHistory() async {
        isLoading = true
        defer { isLoading = false }
        do {
            consentHistory = try await apiClient.fetchConsentHistory()
        } catch {
            statusMessage = "Could not load consent history."
        }
    }

    func fetchModelDisclosures() async {
        isLoading = true
        defer { isLoading = false }
        do {
            modelDisclosures = try await apiClient.fetchModelDisclosures()
        } catch {
            statusMessage = "Could not load model disclosures."
        }
    }

    private func backendCategory(for category: HealthCategory) -> [String] {
        switch category {
        case .sleep:
            ["sleep"]
        case .workouts, .steps, .vo2Max:
            ["activity"]
        case .heartRateVariability, .restingHeartRate:
            ["heart_rate"]
        }
    }
}

struct ConsentManagementView: View {
    @StateObject private var viewModel: ConsentManagementViewModel

    init(apiClient: any DataControlsAPIClient) {
        _viewModel = StateObject(
            wrappedValue: ConsentManagementViewModel(apiClient: apiClient)
        )
    }

    var body: some View {
        List {
            Section("Revocation") {
                Button("Disable external LLM") {
                    Task { await viewModel.disableExternalLLM() }
                }
                .disabled(viewModel.isLoading)

                Button("Revoke cloud processing") {
                    Task { await viewModel.revokeCloudProcessing() }
                }
                .disabled(viewModel.isLoading)

                Button("Revoke raw note processing") {
                    Task { await viewModel.revokeRawNoteProcessing() }
                }
                .disabled(viewModel.isLoading)

                ForEach(HealthCategory.allCases) { category in
                    Toggle(
                        isOn: Binding(
                            get: { viewModel.categoriesToRevoke.contains(category) },
                            set: { isOn in
                                if isOn {
                                    viewModel.categoriesToRevoke.insert(category)
                                } else {
                                    viewModel.categoriesToRevoke.remove(category)
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

                Button("Revoke selected health categories") {
                    Task { await viewModel.revokeSelectedCategories() }
                }
                .disabled(viewModel.isLoading || viewModel.categoriesToRevoke.isEmpty)
            }

            Section("Consent history") {
                Button {
                    Task { await viewModel.fetchConsentHistory() }
                } label: {
                    if viewModel.isLoading && viewModel.consentHistory == nil {
                        ProgressView()
                    } else {
                        Text("Load consent history")
                    }
                }

                if let history = viewModel.consentHistory {
                    Text("Active version: \(history.activeConsentVersion)")
                        .font(.footnote)
                    ForEach(history.records, id: \.id) { record in
                        ConsentRecordRow(record: record)
                    }
                }
            }

            Section("Model disclosures") {
                Button {
                    Task { await viewModel.fetchModelDisclosures() }
                } label: {
                    if viewModel.isLoading && viewModel.modelDisclosures == nil {
                        ProgressView()
                    } else {
                        Text("Load model disclosures")
                    }
                }

                if let disclosures = viewModel.modelDisclosures {
                    ForEach(disclosures.runs, id: \.runID) { run in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("\(run.provider) / \(run.model)")
                            Text(run.runType)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text("Prompt \(run.promptVersion)")
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                    }
                }
            }

            if let message = viewModel.statusMessage {
                Section {
                    Text(message)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Consent")
    }
}

private struct ConsentRecordRow: View {
    let record: DataControlConsentResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(record.consentVersion)
                .font(.subheadline)
            Text("Cloud: \(record.cloudProcessingEnabled ? "on" : "off") | LLM: \(record.externalLLMEnabled ? "on" : "off") | Notes: \(record.rawNoteProcessingEnabled ? "on" : "off")")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("Categories: \(record.healthCategoriesEnabled.joined(separator: ", "))")
                .font(.caption)
                .foregroundStyle(.secondary)
            if record.revokedAt != nil {
                Text("Revoked")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
    }
}
#endif

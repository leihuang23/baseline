import BaselineCore
import SwiftUI

struct DailyCheckInView: View {
    @ObservedObject var viewModel: DailyCheckInViewModel
    private let privacyMode: () -> PrivacyMode

    init(
        viewModel: DailyCheckInViewModel,
        privacyMode: @escaping () -> PrivacyMode
    ) {
        self.viewModel = viewModel
        self.privacyMode = privacyMode
    }

    var body: some View {
        List {
            Section("Today") {
                scoreSlider(DailyCheckInLayoutSnapshot.scoreLabels[0], value: $viewModel.energy)
                scoreSlider(DailyCheckInLayoutSnapshot.scoreLabels[1], value: $viewModel.mood)
                scoreSlider(DailyCheckInLayoutSnapshot.scoreLabels[2], value: $viewModel.soreness)
                scoreSlider(DailyCheckInLayoutSnapshot.scoreLabels[3], value: $viewModel.stress)
                scoreSlider(
                    DailyCheckInLayoutSnapshot.scoreLabels[4],
                    value: $viewModel.perceivedRecovery
                )
                scoreSlider(DailyCheckInLayoutSnapshot.scoreLabels[5], value: $viewModel.foodQuality)
            }

            Section("Optional context") {
                Toggle(DailyCheckInLayoutSnapshot.optionalContextLabels[0], isOn: $viewModel.alcohol)
                Toggle(DailyCheckInLayoutSnapshot.optionalContextLabels[1], isOn: $viewModel.caffeine)
                Toggle(DailyCheckInLayoutSnapshot.optionalContextLabels[2], isOn: $viewModel.illness)
                Toggle(DailyCheckInLayoutSnapshot.optionalContextLabels[3], isOn: $viewModel.injury)
                Toggle(DailyCheckInLayoutSnapshot.optionalContextLabels[4], isOn: $viewModel.travel)
            }

            Section("Private optional indicators") {
                Toggle(
                    DailyCheckInLayoutSnapshot.privateIndicatorLabel,
                    isOn: $viewModel.privateLifestyleIndicator
                )
                Text("Optional and off by default. Stored as a high-level private signal, not a required detail.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Note") {
                TextField(DailyCheckInLayoutSnapshot.noteLabel, text: $viewModel.note, axis: .vertical)
                    .lineLimit(2...4)
                if viewModel.hasHiddenSavedNote {
                    Text("Saved private note hidden.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Clear saved note") {
                        viewModel.clearSavedNote()
                    }
                }
                Text(privacyCopy)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                Button {
                    Task { await viewModel.saveCurrent() }
                } label: {
                    if viewModel.isSaving {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Text(DailyCheckInLayoutSnapshot.primarySubmitLabel)
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(viewModel.isSaving)

                Button(DailyCheckInLayoutSnapshot.fastSubmitLabel) {
                    Task { await viewModel.saveCurrent(includeDefaults: false) }
                }
                .disabled(viewModel.isSaving)
                .accessibilityIdentifier(DailyCheckInView.fastSubmitAccessibilityID)

                Button(DailyCheckInLayoutSnapshot.reloadSavedLabel) {
                    Task { await viewModel.loadExistingForSelectedDate() }
                }
                .disabled(viewModel.isSaving)
                .accessibilityIdentifier(DailyCheckInView.reloadSavedAccessibilityID)

                if viewModel.existingCheckInID != nil {
                    Button(DailyCheckInLayoutSnapshot.updateSavedLabel) {
                        Task { await viewModel.updateExisting() }
                    }
                    .disabled(viewModel.isSaving)

                    Button(DailyCheckInLayoutSnapshot.deleteSavedLabel, role: .destructive) {
                        Task { await viewModel.deleteExisting() }
                    }
                    .disabled(viewModel.isSaving)
                }

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
        .navigationTitle("Morning")
        .task {
            await viewModel.loadExistingForSelectedDate()
        }
    }

    static let fastSubmitAccessibilityID = "daily-checkin-fast-submit"
    static let reloadSavedAccessibilityID = "daily-checkin-reload-saved"
    static let oneMinuteInteractionBudget = DailyCheckInLayoutSnapshot.oneMinuteInteractionBudget
    static let requiredFastSubmitFields = DailyCheckInLayoutSnapshot.requiredFastSubmitFields

    private var privacyCopy: String {
        switch privacyMode() {
        case .localOnly:
            "Local-only: raw notes stay on device and are not sent."
        case .hybrid:
            "Hybrid: structured fields may sync; notes may be summarized before cloud processing."
        case .cloudAssisted:
            "Cloud-assisted: structured fields sync; notes may be summarized before processing."
        }
    }

    private func scoreSlider(_ title: String, value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title)
                Spacer()
                Text("\(Int(value.wrappedValue.rounded()))")
                    .foregroundStyle(.secondary)
            }
            Slider(value: value, in: 1...10, step: 1)
        }
        .accessibilityElement(children: .combine)
    }
}

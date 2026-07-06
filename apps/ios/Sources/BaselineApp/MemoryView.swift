#if os(iOS)
import BaselineCore
import SwiftUI

@MainActor
final class MemoryViewModel: ObservableObject {
    @Published private(set) var summaries: [MemorySummaryItem] = []
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published var selectedPeriod: MemoryPeriodType? = nil

    private let apiClient: any MemoryAPIClient

    init(apiClient: any MemoryAPIClient) {
        self.apiClient = apiClient
    }

    func loadSummaries() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let response = try await apiClient.fetchMemorySummaries(periodType: selectedPeriod)
            summaries = response.summaries
        } catch {
            errorMessage = "Could not load memory summaries."
        }
    }

    func deleteSummary(id: UUID) async {
        do {
            try await apiClient.deleteMemorySummary(id: id)
            summaries.removeAll { $0.id == id }
        } catch {
            errorMessage = "Could not delete memory summary."
        }
    }

    var weeklySummaries: [MemorySummaryItem] {
        summaries.filter { $0.periodType == .weekly }
    }

    var trendComparison: TrendComparison? {
        let items = weeklySummaries.sorted { $0.endDate > $1.endDate }
        guard items.count >= 2,
              let latest = items.first,
              let previous = items.dropFirst().first else {
            return nil
        }
        return TrendComparison(latest: latest, previous: previous)
    }
}

struct TrendComparison {
    let latest: MemorySummaryItem
    let previous: MemorySummaryItem

    var latestObservations: [String] {
        latest.observations.compactMap { $0.text ?? $0.summary }
    }

    var previousObservations: [String] {
        previous.observations.compactMap { $0.text ?? $0.summary }
    }

    var newObservations: [String] {
        latestObservations.filter { !previousObservations.contains($0) }
    }
}

struct MemoryView: View {
    @ObservedObject private var viewModel: MemoryViewModel

    init(viewModel: MemoryViewModel) {
        self.viewModel = viewModel
    }

    var body: some View {
        List {
            trendsSection
            periodFilterSection
            summariesSection
        }
        .navigationTitle("Memory")
        .task {
            await viewModel.loadSummaries()
        }
        .refreshable {
            await viewModel.loadSummaries()
        }
    }

    private var trendsSection: some View {
        Section("Trends") {
            if let trend = viewModel.trendComparison {
                VStack(alignment: .leading, spacing: 8) {
                    Text("This week vs last week")
                        .font(.subheadline)
                    Text("\(trend.latest.startDate) to \(trend.latest.endDate)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if trend.newObservations.isEmpty {
                        Text("No new observations since last week.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(trend.newObservations, id: \.self) { observation in
                            Label(observation, systemImage: "chart.line.uptrend.xyaxis")
                                .font(.footnote)
                        }
                    }
                }
            } else {
                Text("Compare weeks after more weekly summaries are available.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var periodFilterSection: some View {
        Section("Period") {
            Picker("Period", selection: $viewModel.selectedPeriod) {
                Text("All").tag(Optional<MemoryPeriodType>.none)
                ForEach(MemoryPeriodType.allCases) { period in
                    Text(period.title).tag(Optional(period))
                }
            }
            .pickerStyle(.segmented)
            .onChange(of: viewModel.selectedPeriod) { _, _ in
                Task { await viewModel.loadSummaries() }
            }
        }
    }

    private var summariesSection: some View {
        Section("Summaries") {
            if viewModel.isLoading && viewModel.summaries.isEmpty {
                ProgressView("Loading memory...")
            } else if viewModel.summaries.isEmpty {
                Text("No memory summaries yet.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(viewModel.summaries) { summary in
                    MemorySummaryRow(summary: summary)
                }
                .onDelete { indexSet in
                    Task {
                        for index in indexSet {
                            await viewModel.deleteSummary(id: viewModel.summaries[index].id)
                        }
                    }
                }
            }
            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
    }
}

private struct MemorySummaryRow: View {
    let summary: MemorySummaryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(summary.periodType.title)
                    .font(.headline)
                Spacer()
                Text("\(Int(summary.confidence * 100))% confidence")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("\(summary.startDate) to \(summary.endDate)")
                .font(.caption)
                .foregroundStyle(.secondary)

            if !summary.observations.isEmpty {
                Text("Observations")
                    .font(.subheadline)
                ForEach(summary.observations, id: \.text) { observation in
                    Text("• \(observation.text ?? observation.summary ?? "No detail")")
                        .font(.footnote)
                }
            }

            if !summary.hypotheses.isEmpty {
                Text("Hypotheses")
                    .font(.subheadline)
                ForEach(summary.hypotheses, id: \.text) { hypothesis in
                    Text("• \(hypothesis.text ?? hypothesis.summary ?? "No detail")")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            if !summary.sensitiveFieldsExcluded.isEmpty {
                Text("Excluded: \(summary.sensitiveFieldsExcluded.joined(separator: ", "))")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 4)
    }
}
#endif

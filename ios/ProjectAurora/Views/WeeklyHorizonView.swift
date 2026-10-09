import SwiftUI

public struct WeeklyHorizonView: View {
    @ObservedObject public var viewModel: WeeklyHorizonViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var isShowingPlanSheet: Bool = false
    @State private var isShowingRetrospectiveSheet: Bool = false

    public init(viewModel: WeeklyHorizonViewModel) {
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            ZStack(alignment: .bottom) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        if viewModel.isLoading && viewModel.horizon == nil {
                            loadingView
                        } else if let horizon = viewModel.horizon {
                            // Date Range & Timezone Header
                            headerSection(horizon: horizon)

                            // Weekly Focus & Metrics
                            metricsSection(horizon: horizon)

                            // 7-Day Capacity Rail
                            capacityRailSection(days: horizon.daySummaries)

                            // Commitment Health Section
                            if !horizon.commitmentHealth.isEmpty {
                                commitmentHealthSection(commitments: horizon.commitmentHealth)
                            }

                            // Strategic Recommendations Section
                            if !horizon.strategicRecommendations.isEmpty {
                                recommendationsSection(recommendations: horizon.strategicRecommendations)
                            }

                            // Bottom spacer for action bar
                            Spacer()
                                .frame(height: 80)
                        } else if case .failedPresentation(let message) = viewModel.sheetState {
                            errorView(message: message)
                        } else {
                            emptyView
                        }
                    }
                    .padding()
                }
                .refreshable {
                    await viewModel.loadWeeklyHorizon()
                }

                // Governed Bottom Floating Action Bar
                floatingActionBar
            }
            .navigationTitle("Weekly Horizon")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .sheet(isPresented: $isShowingPlanSheet) {
                WeeklyPlanSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $isShowingRetrospectiveSheet) {
                WeeklyRetrospectiveSheet(viewModel: viewModel)
            }
            .task {
                if viewModel.horizon == nil {
                    await viewModel.loadWeeklyHorizon()
                }
            }
        }
    }

    // MARK: - Subviews

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text("Synthesizing 7-day strategic horizon...")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private func errorView(message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.red)
                .font(.system(size: 36))
            Text("Failed to Load Horizon")
                .font(.headline)
            Text(message)
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            Button("Retry") {
                Task {
                    await viewModel.loadWeeklyHorizon()
                }
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 200)
    }

    private var emptyView: some View {
        VStack(spacing: 12) {
            Image(systemName: "calendar.badge.clock")
                .font(.system(size: 40))
                .foregroundColor(.secondary)
            Text("No Weekly Horizon Data")
                .font(.headline)
            Text("Tap to synthesize your 7-day rolling projection.")
                .font(.caption)
                .foregroundColor(.secondary)
            Button("Synthesize") {
                Task {
                    await viewModel.loadWeeklyHorizon()
                }
            }
            .buttonStyle(.bordered)
        }
        .frame(maxWidth: .infinity, minHeight: 200)
    }

    private func headerSection(horizon: WeeklyStrategicHorizon) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("\(horizon.horizonStartDate) — \(horizon.horizonEndDate)")
                    .font(.title3)
                    .fontWeight(.bold)
                Spacer()
                Text(horizon.timezone)
                    .font(.caption)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color(.tertiarySystemFill))
                    .cornerRadius(6)
            }
            Text("7-Day Rolling Strategic Allocation")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 4)
    }

    private func metricsSection(horizon: WeeklyStrategicHorizon) -> some View {
        HStack(spacing: 12) {
            metricCard(
                title: "Free Focus",
                value: "\(horizon.totalFreeFocusMinutes / 60)h \(horizon.totalFreeFocusMinutes % 60)m",
                icon: "sparkles",
                color: .blue
            )
            metricCard(
                title: "Overcommitted",
                value: "\(horizon.overcommittedDaysCount) days",
                icon: "exclamationmark.circle",
                color: horizon.overcommittedDaysCount > 0 ? .red : .green
            )
            metricCard(
                title: "Orphan Cmt",
                value: "\(horizon.orphanCommitmentsCount)",
                icon: "flag.badge.ellipsis",
                color: horizon.orphanCommitmentsCount > 0 ? .orange : .secondary
            )
        }
    }

    private func metricCard(title: String, value: String, icon: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: icon)
                    .foregroundColor(color)
                    .font(.caption)
                Spacer()
            }
            Text(value)
                .font(.headline)
                .fontWeight(.bold)
            Text(title)
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground))
        .cornerRadius(10)
    }

    private func capacityRailSection(days: [DayCapacitySummary]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("7-Day Capacity Rail")
                .font(.headline)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(days) { day in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(day.dayOfWeek)
                                    .font(.subheadline)
                                    .fontWeight(.bold)
                                Spacer()
                                congestionBadge(day.congestionLevel)
                            }
                            Text(day.dateStr)
                                .font(.caption2)
                                .foregroundColor(.secondary)

                            Divider()

                            HStack {
                                Text("Focus:")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                                Spacer()
                                Text("\(day.freeFocusMinutes)m free")
                                    .font(.caption2)
                                    .fontWeight(.semibold)
                            }

                            HStack {
                                Text("Tasks / Events:")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                                Spacer()
                                Text("\(day.tasksCount) / \(day.eventsCount)")
                                    .font(.caption2)
                            }

                            if day.isOvercommitted {
                                HStack {
                                    Image(systemName: "exclamationmark.triangle.fill")
                                        .foregroundColor(.red)
                                        .font(.caption2)
                                    Text("Overbooked")
                                        .font(.caption2)
                                        .foregroundColor(.red)
                                }
                            }
                        }
                        .padding(10)
                        .frame(width: 140)
                        .background(Color(.secondarySystemBackground))
                        .cornerRadius(10)
                    }
                }
            }
        }
    }

    private func congestionBadge(_ level: WeeklyHorizonCongestionLevel) -> some View {
        let (color, text) = {
            switch level {
            case .light: return (Color.green, "Light")
            case .balanced: return (Color.blue, "Balanced")
            case .congested: return (Color.orange, "Congested")
            }
        }()

        return Text(text)
            .font(.system(size: 9, weight: .bold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundColor(color)
            .cornerRadius(4)
    }

    private func commitmentHealthSection(commitments: [CommitmentHealthItem]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Commitment Health")
                .font(.headline)

            ForEach(commitments) { item in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(item.title)
                            .font(.subheadline)
                            .fontWeight(.semibold)
                        Spacer()
                        if item.isOrphan {
                            Text("Orphan")
                                .font(.caption2)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.orange.opacity(0.15))
                                .foregroundColor(.orange)
                                .cornerRadius(4)
                        }
                    }

                    HStack(spacing: 12) {
                        Text("Linked Tasks: \(item.completedTasksCount)/\(item.linkedTasksCount)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        if let targetDate = item.targetDate {
                            Text("Target: \(targetDate)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    if let warning = item.overrunWarning {
                        HStack(spacing: 4) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundColor(.red)
                                .font(.caption2)
                            Text(warning)
                                .font(.caption2)
                                .foregroundColor(.red)
                        }
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.secondarySystemBackground))
                .cornerRadius(8)
            }
        }
    }

    private func recommendationsSection(recommendations: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Strategic Recommendations")
                .font(.headline)

            ForEach(recommendations, id: \.self) { rec in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "lightbulb.fill")
                        .foregroundColor(.yellow)
                        .font(.caption)
                        .padding(.top, 2)
                    Text(rec)
                        .font(.subheadline)
                }
                .padding(.vertical, 2)
            }
        }
    }

    private var floatingActionBar: some View {
        HStack(spacing: 12) {
            Button {
                isShowingPlanSheet = true
            } label: {
                HStack {
                    Image(systemName: "calendar.badge.plus")
                    Text("Plan Week")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(Color.blue)
                .foregroundColor(.white)
                .cornerRadius(12)
            }

            Button {
                isShowingRetrospectiveSheet = true
            } label: {
                HStack {
                    Image(systemName: "clock.arrow.circlepath")
                    Text("Retrospective")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(Color.purple)
                .foregroundColor(.white)
                .cornerRadius(12)
            }
        }
        .padding(.horizontal)
        .padding(.bottom, 8)
        .background(
            Color(.systemBackground)
                .opacity(0.85)
                .blur(radius: 8)
        )
    }
}

//
//  ScheduleCoordinationSheet.swift
//  ProjectAurora
//
//  Phase 6Q: Schedule Coordination Presentation Layer (Parent-Observed Architecture)
//

import SwiftUI

public struct ScheduleCoordinationSheet: View {
    @ObservedObject public var viewModel: ScheduleCoordinationViewModel
    @Environment(\.dismiss) private var dismiss

    private let targetDate: Date

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone.current
        return f
    }()

    public init(
        viewModel: ScheduleCoordinationViewModel? = nil,
        targetDate: Date = Date()
    ) {
        self.viewModel = viewModel ?? ScheduleCoordinationViewModel()
        self.targetDate = targetDate
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Header status
                headerView
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                    .background(Color(.secondarySystemBackground))

                Divider()

                // State-driven content
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        switch viewModel.state {
                        case .idle:
                            idleView
                        case .analysisLoaded(let analysis):
                            analysisView(analysis: analysis)
                        case .submittingProposal(let stratId):
                            submittingView(strategyId: stratId)
                        case .awaitingConfirmation(let strategy, _, _, _, _, _):
                            awaitingConfirmationView(strategy: strategy)
                        case .executingMutation:
                            executingMutationView
                        case .completed(let summary):
                            completedView(summary: summary)
                        case .rejected:
                            rejectedView
                        case .mutationOutcomeUnknown(_, _, _, _, _, let error):
                            mutationOutcomeUnknownView(error: error)
                        case .recovering:
                            recoveringView
                        case .recoveryUncertain(_, _, let reason):
                            recoveryUncertainView(reason: reason)
                        case .failed(let message):
                            failedView(message: message)
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Schedule Coordination")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if viewModel.canStartFresh() {
                        Button("Start Over") {
                            viewModel.startFresh()
                        }
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .task {
                if case .idle = viewModel.state {
                    let dateStr = Self.dateFormatter.string(from: targetDate)
                    await viewModel.analyzeConflicts(date: dateStr)
                }
            }
        }
    }

    // MARK: - Header View

    @ViewBuilder
    private var headerView: some View {
        HStack {
            Image(systemName: "clock.arrow.2.circlepath")
                .foregroundColor(.accentColor)
            Text("Target: \(Self.dateFormatter.string(from: targetDate))")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Spacer()
            if viewModel.isStaging || viewModel.isMutating || viewModel.isRecovering {
                ProgressView()
                    .scaleEffect(0.8)
            }
        }
    }

    // MARK: - Subviews by State

    @ViewBuilder
    private var idleView: some View {
        VStack(spacing: 16) {
            Image(systemName: "calendar.badge.clock")
                .font(.system(size: 48))
                .foregroundColor(.accentColor)
            Text("Analyzing Schedule Conflicts")
                .font(.headline)
            Text("Scanning your tasks and calendar events for collisions and planning opportunities.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            Button("Scan Conflicts") {
                Task {
                    let dateStr = Self.dateFormatter.string(from: targetDate)
                    await viewModel.analyzeConflicts(date: dateStr)
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    @ViewBuilder
    private func analysisView(analysis: ResolveConflictsResponsePayload) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Found \(analysis.conflicts.count) Conflicts • \(analysis.strategyOptions.count) Strategies")
                .font(.headline)

            if analysis.strategyOptions.isEmpty {
                Text("No coordination strategies are currently needed. Your schedule is clear!")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
            } else {
                ForEach(analysis.strategyOptions) { strategy in
                    strategyCard(strategy: strategy)
                }
            }
        }
    }

    @ViewBuilder
    private func strategyCard(strategy: StrategyOptionPayload) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(strategy.name)
                    .font(.headline)
                Spacer()
                Text(strategy.category.rawValue.capitalized)
                    .font(.caption)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.accentColor.opacity(0.15))
                    .cornerRadius(6)
            }

            Text(strategy.description)
                .font(.subheadline)
                .foregroundColor(.secondary)

            // Operations preview
            if !strategy.previewOperations.isEmpty {
                Divider()
                Text("Planned Operations:")
                    .font(.caption)
                    .foregroundColor(.secondary)
                ForEach(strategy.previewOperations) { op in
                    HStack(spacing: 6) {
                        Image(systemName: "arrow.right.circle.fill")
                            .foregroundColor(.accentColor)
                            .font(.caption)
                        Text(op.displaySummary)
                            .font(.caption)
                    }
                }
            }

            HStack {
                Spacer()
                Button("Stage Strategy") {
                    viewModel.stageProposal(strategy: strategy)
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(12)
    }

    @ViewBuilder
    private func submittingView(strategyId: String) -> some View {
        VStack(spacing: 16) {
            ProgressView()
            Text("Staging Proposal...")
                .font(.headline)
            Text("Preparing execution plan on server for strategy \(strategyId).")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    @ViewBuilder
    private func awaitingConfirmationView(strategy: StrategyOptionPayload) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "exclamationmark.shield")
                    .foregroundColor(.orange)
                Text("Confirmation Required")
                    .font(.headline)
            }

            Text("Please review the proposed schedule modifications before applying them.")
                .font(.subheadline)
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 8) {
                Text(strategy.name)
                    .font(.headline)
                Text(strategy.description)
                    .font(.subheadline)

                Divider()

                ForEach(strategy.previewOperations) { op in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark.circle")
                            .foregroundColor(.accentColor)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(op.displaySummary)
                                .font(.subheadline)
                        }
                    }
                }
            }
            .padding()
            .background(Color(.secondarySystemBackground))
            .cornerRadius(12)

            HStack(spacing: 12) {
                Button("Decline") {
                    viewModel.confirmProposal(approved: false)
                }
                .buttonStyle(.bordered)
                .frame(maxWidth: .infinity)

                Button("Confirm & Apply") {
                    viewModel.confirmProposal(approved: true)
                }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
            }
        }
    }

    @ViewBuilder
    private var executingMutationView: some View {
        VStack(spacing: 16) {
            ProgressView()
            Text("Applying Schedule Changes...")
                .font(.headline)
            Text("Dispatched to server. Domain mutations in progress.")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    @ViewBuilder
    private func completedView(summary: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 48))
                .foregroundColor(.green)
            Text("Schedule Coordinated")
                .font(.headline)
            Text(summary)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            Button("Done") {
                dismiss()
            }
            .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    @ViewBuilder
    private var rejectedView: some View {
        VStack(spacing: 16) {
            Image(systemName: "xmark.circle")
                .font(.system(size: 48))
                .foregroundColor(.secondary)
            Text("Proposal Declined")
                .font(.headline)
            Text("No changes were applied to your schedule.")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Button("Start Over") {
                viewModel.startFresh()
            }
            .buttonStyle(.bordered)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    @ViewBuilder
    private func mutationOutcomeUnknownView(error: String) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "wifi.exclamationmark")
                    .foregroundColor(.orange)
                    .font(.title2)
                Text("Connection Lost During Execution")
                    .font(.headline)
            }

            Text("A network disconnection occurred while waiting for the server's response. The schedule change may already have been applied.")
                .font(.subheadline)
                .foregroundColor(.secondary)

            Text("Error details: \(error)")
                .font(.caption)
                .foregroundColor(.secondary)

            Text("Automatic re-application is strictly blocked to protect against duplicate task or reminder shifts.")
                .font(.caption)
                .foregroundColor(.orange)

            Button {
                viewModel.performSingleRecoveryAttempt()
            } label: {
                HStack {
                    Image(systemName: "arrow.clockwise")
                    Text("Check Status")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(Color.orange.opacity(0.1))
        .cornerRadius(12)
    }

    @ViewBuilder
    private var recoveringView: some View {
        VStack(spacing: 16) {
            ProgressView()
            Text("Verifying Execution Status...")
                .font(.headline)
            Text("Querying authoritative server conversation history to verify whether this execution completed.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    @ViewBuilder
    private func recoveryUncertainView(reason: RecoveryUncertainReason) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "shield.slash")
                    .foregroundColor(.red)
                    .font(.title2)
                Text("Execution Outcome Uncertain")
                    .font(.headline)
            }

            Text(reason.localizedDescription)
                .font(.subheadline)
                .foregroundColor(.secondary)

            Text("Automatic re-application is prohibited. Please check your Agenda and Tasks directly before attempting new modifications.")
                .font(.footnote)
                .foregroundColor(.red)

            Button("Done") {
                dismiss()
            }
            .buttonStyle(.bordered)
            .frame(maxWidth: .infinity)
        }
        .padding()
        .background(Color.red.opacity(0.1))
        .cornerRadius(12)
    }

    @ViewBuilder
    private func failedView(message: String) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "exclamationmark.triangle")
                    .foregroundColor(.red)
                Text("Action Failed")
                    .font(.headline)
            }

            Text(message)
                .font(.subheadline)
                .foregroundColor(.secondary)

            if viewModel.canStartFresh() {
                Button("Try Again") {
                    viewModel.startFresh()
                }
                .buttonStyle(.bordered)
            }
        }
        .padding()
        .background(Color.red.opacity(0.1))
        .cornerRadius(12)
    }
}

import SwiftUI

public struct EveningReviewSheet: View {
    @ObservedObject var viewModel: AgendaViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var selectedRolloverTaskIds: Set<String> = []
    @State private var reflectionNotes: String = ""

    public init(viewModel: AgendaViewModel) {
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                switch viewModel.diurnalSheetState {
                case .idle, .loadingSynthesis:
                    loadingSynthesisView

                case .synthesisLoaded, .generatingProposal:
                    synthesisView

                case .proposalReady, .applying, .aborting:
                    proposalView

                case .completedPresentation:
                    completedView

                case .partiallyExecutedPresentation:
                    partiallyExecutedView

                case .proposalFailedPresentation(let reason, let ops):
                    proposalFailedView(reason: reason, operations: ops)

                case .abortedPresentation:
                    abortedView

                case .failedPresentation(let message):
                    failedView(message: message)

                case .mutationOutcomeUnknown(let proposalId):
                    mutationOutcomeUnknownView(proposalId: proposalId)
                }
            }
            .navigationTitle("Evening Horizon Review")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Close") {
                        dismiss()
                    }
                }
            }
            .task {
                if viewModel.activeEveningReview == nil {
                    await viewModel.loadEveningHorizonReview()
                }
            }
        }
    }

    // MARK: - State Views

    private var loadingSynthesisView: some View {
        VStack(spacing: 16) {
            Spacer()
            ProgressView("Synthesizing Evening Horizon Review...")
                .progressViewStyle(.circular)
            Text("Reviewing accomplishments, overdue tasks, and statistics...")
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
        }
        .padding()
    }

    private var synthesisView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let review = viewModel.activeEveningReview {
                    // Executive Summary
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Executive Summary")
                            .font(.headline)
                        Text(review.executiveSummary)
                            .font(.body)
                            .foregroundColor(.primary)
                            .padding(12)
                            .background(Color(.secondarySystemGroupedBackground))
                            .cornerRadius(10)
                    }

                    // Completion Statistics
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Day In Review")
                            .font(.headline)
                        HStack(spacing: 12) {
                            statCard(title: "Completed", value: "\(review.completionStats.completedTaskCount)", color: .green)
                            statCard(title: "Overdue", value: "\(review.completionStats.overdueTaskCount)", color: .orange)
                            if let rate = review.completionStats.completionRatePercent {
                                statCard(title: "Rate", value: "\(Int(rate))%", color: .blue)
                            }
                        }
                    }

                    // Accomplishments
                    if !review.accomplishments.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Key Accomplishments")
                                .font(.headline)
                            ForEach(review.accomplishments, id: \.self) { item in
                                HStack(alignment: .top, spacing: 10) {
                                    Image(systemName: "star.fill")
                                        .foregroundColor(.yellow)
                                        .font(.caption)
                                        .padding(.top, 4)
                                    Text(item)
                                        .font(.subheadline)
                                    Spacer()
                                }
                                .padding(10)
                                .background(Color(.secondarySystemGroupedBackground))
                                .cornerRadius(8)
                            }
                        }
                    }

                    // Overdue Tasks & Rollover Selection
                    if !review.overdueTasks.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Overdue Tasks (Select to Rollover)")
                                .font(.headline)
                            ForEach(review.overdueTasks) { task in
                                HStack {
                                    Image(systemName: selectedRolloverTaskIds.contains(task.taskId) ? "checkmark.square.fill" : "square")
                                        .foregroundColor(selectedRolloverTaskIds.contains(task.taskId) ? .accentColor : .secondary)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(task.title)
                                            .font(.subheadline)
                                        if let due = task.dueAt {
                                            Text("Due: \(formatTime(due))")
                                                .font(.caption2)
                                                .foregroundColor(.secondary)
                                        }
                                    }
                                    Spacer()
                                }
                                .padding(10)
                                .background(selectedRolloverTaskIds.contains(task.taskId) ? Color.accentColor.opacity(0.1) : Color(.secondarySystemGroupedBackground))
                                .cornerRadius(8)
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    if selectedRolloverTaskIds.contains(task.taskId) {
                                        selectedRolloverTaskIds.remove(task.taskId)
                                    } else {
                                        selectedRolloverTaskIds.insert(task.taskId)
                                    }
                                }
                            }
                        }
                    }

                    // Reflection Editor
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Evening Reflection Notes")
                            .font(.headline)
                        TextEditor(text: $reflectionNotes)
                            .frame(minHeight: 80)
                            .padding(4)
                            .background(Color(.secondarySystemGroupedBackground))
                            .cornerRadius(8)
                    }

                    // Propose Evening Review Action
                    Button {
                        Task {
                            await viewModel.createEveningReviewProposal(
                                rolloverTaskIds: selectedRolloverTaskIds.isEmpty ? nil : Array(selectedRolloverTaskIds),
                                reflectionNotes: reflectionNotes.isEmpty ? nil : reflectionNotes
                            )
                        }
                    } label: {
                        HStack {
                            if case .generatingProposal = viewModel.diurnalSheetState {
                                ProgressView()
                                    .progressViewStyle(.circular)
                                    .tint(.white)
                            }
                            Text(viewModel.diurnalSheetState == .generatingProposal ? "Generating Review Proposal..." : "Generate Evening Review Proposal")
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(viewModel.diurnalSheetState == .generatingProposal ? Color.gray : Color.accentColor)
                        .foregroundColor(.white)
                        .cornerRadius(10)
                    }
                    .disabled(viewModel.diurnalSheetState == .generatingProposal)
                }
            }
            .padding()
        }
    }

    private var proposalView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let proposal = viewModel.activeEveningReviewProposal {
                    // Header Banner
                    HStack {
                        Image(systemName: "moon.stars.fill")
                            .font(.title2)
                            .foregroundColor(.purple)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Evening Review Proposal")
                                .font(.headline)
                            Text("ID: \(proposal.proposalId)")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                    }
                    .padding(12)
                    .background(Color.purple.opacity(0.1))
                    .cornerRadius(10)

                    // Presentational TTL Countdown
                    ttlCountdownBanner(expiresAtString: proposal.expiresAt)

                    // Ordered Operations
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Proposed Operations (\(proposal.orderedOperations.count))")
                            .font(.headline)

                        if proposal.orderedOperations.isEmpty {
                            Text("No mutations proposed. Evening review complete.")
                                .font(.subheadline)
                                .foregroundColor(.secondary)
                                .padding()
                        } else {
                            ForEach(proposal.orderedOperations) { op in
                                HStack(alignment: .top, spacing: 10) {
                                    Image(systemName: iconForOperationType(op.operationType))
                                        .foregroundColor(.purple)
                                        .padding(.top, 2)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(op.operationType.rawValue.replacingOccurrences(of: "_", with: " ").capitalized)
                                            .font(.subheadline)
                                            .fontWeight(.medium)
                                        if let domain = op.targetDomain {
                                            Text("Domain: \(domain)")
                                                .font(.caption2)
                                                .foregroundColor(.secondary)
                                        }
                                        if let error = op.failureError ?? op.error {
                                            Text(error)
                                                .font(.caption2)
                                                .foregroundColor(.red)
                                        }
                                    }
                                    Spacer()
                                    Text(op.status.rawValue.uppercased())
                                        .font(.caption2)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(statusBackgroundColor(op.status))
                                        .cornerRadius(4)
                                }
                                .padding(10)
                                .background(Color(.secondarySystemGroupedBackground))
                                .cornerRadius(8)
                            }
                        }
                    }

                    // Action Buttons (Mutually Exclusive, Disabled while in-flight)
                    let isMutating: Bool = {
                        if case .applying = viewModel.diurnalSheetState { return true }
                        if case .aborting = viewModel.diurnalSheetState { return true }
                        return false
                    }()

                    VStack(spacing: 12) {
                        Button {
                            viewModel.applyActiveEveningReview(approved: true)
                        } label: {
                            HStack {
                                if case .applying = viewModel.diurnalSheetState {
                                    ProgressView().tint(.white)
                                }
                                Text(isMutating ? "Applying..." : "Confirm & Apply")
                                    .fontWeight(.semibold)
                            }
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(isMutating ? Color.gray : Color.purple)
                            .foregroundColor(.white)
                            .cornerRadius(10)
                        }
                        .disabled(isMutating)

                        Button {
                            viewModel.applyActiveEveningReview(approved: false)
                        } label: {
                            HStack {
                                if case .aborting = viewModel.diurnalSheetState {
                                    ProgressView().tint(.white)
                                }
                                Text("Discard Proposal")
                                    .fontWeight(.semibold)
                            }
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(isMutating ? Color.gray.opacity(0.3) : Color.red.opacity(0.12))
                            .foregroundColor(.red)
                            .cornerRadius(10)
                        }
                        .disabled(isMutating)
                    }
                    .padding(.top, 10)
                }
            }
            .padding()
        }
    }

    // MARK: - Outcome Views

    private var completedView: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 64))
                .foregroundColor(.green)
            Text("Review Applied")
                .font(.title2)
                .fontWeight(.bold)
            Text("Your evening review mutations and rollover tasks were executed by the server.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button("Done") {
                dismiss()
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding()
    }

    private var partiallyExecutedView: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 64))
                .foregroundColor(.orange)
            Text("Partially Executed")
                .font(.title2)
                .fontWeight(.bold)
            Text("Some review operations succeeded, but one or more encountered an issue.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            if let proposal = viewModel.activeEveningReviewProposal {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(proposal.orderedOperations) { op in
                        HStack {
                            Text(op.operationType.rawValue)
                                .font(.caption)
                            Spacer()
                            Text(op.status.rawValue.uppercased())
                                .font(.caption2)
                                .foregroundColor(op.status == .applied ? .green : .orange)
                        }
                    }
                }
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(8)
                .padding(.horizontal)
            }

            Button("Done") {
                dismiss()
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding()
    }

    private func proposalFailedView(reason: String?, operations: [HorizonOperationPayload]) -> some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "xmark.circle.fill")
                .font(.system(size: 64))
                .foregroundColor(.red)
            Text("Review Failed")
                .font(.title2)
                .fontWeight(.bold)
            if let r = reason {
                Text(r)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }
            Button("Start Fresh") {
                Task {
                    await viewModel.startFreshEveningReview()
                }
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding()
    }

    private var abortedView: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "minus.circle.fill")
                .font(.system(size: 64))
                .foregroundColor(.secondary)
            Text("Review Discarded")
                .font(.title2)
                .fontWeight(.bold)
            Text("The proposal was aborted. No schedule changes were applied.")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Button("Done") {
                dismiss()
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding()
    }

    private func failedView(message: String) -> some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 48))
                .foregroundColor(.red)
            Text("Operation Error")
                .font(.headline)
            Text(message)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button("Start Fresh") {
                Task {
                    await viewModel.startFreshEveningReview()
                }
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding()
    }

    // MARK: - Mutation Outcome Unknown (Presentational/Navigational Containment Only)

    private func mutationOutcomeUnknownView(proposalId: String) -> some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "questionmark.triangle.fill")
                .font(.system(size: 64))
                .foregroundColor(.orange)

            Text("Outcome Unknown")
                .font(.title2)
                .fontWeight(.bold)

            VStack(alignment: .leading, spacing: 8) {
                Text("Connection was lost while communicating with the server.")
                    .font(.subheadline)
                    .fontWeight(.medium)
                Text("The final execution status of proposal '\(proposalId)' could not be confirmed. The client will not make assumptions or retry automatically.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(14)
            .background(Color.orange.opacity(0.12))
            .cornerRadius(10)
            .padding(.horizontal)

            VStack(spacing: 12) {
                // "Check Agenda": PRESENTATIONAL / NAVIGATIONAL ONLY. Zero API calls, zero reconciliation!
                Button {
                    dismiss()
                } label: {
                    Text("Check Agenda")
                        .fontWeight(.semibold)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.accentColor)
                        .foregroundColor(.white)
                        .cornerRadius(10)
                }

                // "Start Fresh": discards local presentation state, requests fresh synthesis
                Button {
                    Task {
                        await viewModel.startFreshEveningReview()
                    }
                } label: {
                    Text("Start Fresh")
                        .fontWeight(.medium)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color(.systemGray5))
                        .foregroundColor(.primary)
                        .cornerRadius(10)
                }
            }
            .padding(.horizontal)
            Spacer()
        }
        .padding()
    }

    // MARK: - Presentational TTL Countdown (Presentation Only, Server Decides Expiration)

    private func ttlCountdownBanner(expiresAtString: String) -> some View {
        let isExpired = checkPresentationalExpiry(expiresAtString)

        return HStack {
            Image(systemName: isExpired ? "clock.badge.exclamationmark" : "timer")
                .foregroundColor(isExpired ? .orange : .purple)
            VStack(alignment: .leading, spacing: 2) {
                if isExpired {
                    Text("Proposal may have expired")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.orange)
                    Text("Server decides final expiration upon confirmation.")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                } else {
                    Text("Active Proposal Window")
                        .font(.caption)
                        .fontWeight(.semibold)
                    Text("Expires at: \(formatTime(expiresAtString))")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
            Spacer()
        }
        .padding(10)
        .background(isExpired ? Color.orange.opacity(0.1) : Color(.secondarySystemGroupedBackground))
        .cornerRadius(8)
    }

    private func checkPresentationalExpiry(_ isoDate: String) -> Bool {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = formatter.date(from: isoDate) {
            return d < Date()
        }
        let fallback = ISO8601DateFormatter()
        if let d = fallback.date(from: isoDate) {
            return d < Date()
        }
        return false
    }

    // MARK: - Helpers

    private func statCard(title: String, value: String, color: Color) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.title3)
                .fontWeight(.bold)
                .foregroundColor(color)
            Text(title)
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(8)
    }

    private func formatTime(_ isoString: String) -> String {
        if let tIndex = isoString.firstIndex(of: "T") {
            let afterT = isoString[isoString.index(after: tIndex)...]
            let parts = afterT.split(separator: ":")
            if parts.count >= 2 {
                return "\(parts[0]):\(parts[1])"
            }
        }
        return isoString
    }

    private func iconForOperationType(_ type: HorizonOperationType) -> String {
        switch type {
        case .createReminder, .updateReminder, .cancelReminder:
            return "bell.fill"
        case .createTask, .updateTask, .completeTask, .rescheduleTask, .rolloverTask:
            return "checkmark.circle.fill"
        case .createCommitment, .completeCommitment:
            return "hand.raised.fill"
        case .createMemory:
            return "brain.head.profile"
        case .noop:
            return "circle"
        }
    }

    private func statusBackgroundColor(_ status: HorizonOperationStatus) -> Color {
        switch status {
        case .applied: return Color.green.opacity(0.15)
        case .pending: return Color.blue.opacity(0.15)
        case .failed: return Color.red.opacity(0.15)
        case .skipped: return Color.gray.opacity(0.15)
        }
    }
}

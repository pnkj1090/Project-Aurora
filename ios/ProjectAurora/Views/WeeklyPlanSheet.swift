import SwiftUI

public struct WeeklyPlanSheet: View {
    @ObservedObject public var viewModel: WeeklyHorizonViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var strategicIntentNotes: String = ""
    @State private var timeRemainingString: String = ""
    @State private var timerActive: Bool = true

    public init(viewModel: WeeklyHorizonViewModel) {
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    switch viewModel.sheetState {
                    case .idle, .loadingSynthesis:
                        loadingSynthesisView

                    case .synthesisLoaded:
                        synthesisInputView

                    case .generatingProposal:
                        generatingProposalView

                    case .proposalReady:
                        if let proposal = viewModel.activePlanProposal {
                            proposalReviewView(proposal: proposal)
                        } else {
                            synthesisInputView
                        }

                    case .applying(let proposalId):
                        applyingView(proposalId: proposalId, isDiscard: false)

                    case .aborting(let proposalId):
                        applyingView(proposalId: proposalId, isDiscard: true)

                    case .executedPresentation:
                        executedSuccessView

                    case .partiallyExecutedPresentation:
                        partiallyExecutedView

                    case .proposalFailedPresentation(let reason, let ops):
                        failedPresentationView(reason: reason, operations: ops)

                    case .abortedPresentation:
                        abortedView

                    case .expiredPresentation:
                        expiredView

                    case .serverExecutionInProgress(let proposalId):
                        serverExecutionInProgressView(proposalId: proposalId)

                    case .contractViolationPresentation(let violation):
                        contractViolationView(violation: violation)

                    case .mutationOutcomeUnknown(let proposalId):
                        mutationOutcomeUnknownView(proposalId: proposalId)

                    case .failedPresentation(let message):
                        generalFailureView(message: message)
                    }
                }
                .padding()
            }
            .navigationTitle("Weekly Plan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Close") {
                        dismiss()
                    }
                }
            }
            .task {
                if viewModel.horizon == nil {
                    await viewModel.loadWeeklyHorizon()
                }
            }
        }
    }

    // MARK: - State Subviews

    private var loadingSynthesisView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text("Loading week capacity...")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private var synthesisInputView: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Plan Your Week")
                .font(.title2)
                .fontWeight(.bold)

            Text("Enter your strategic intent or priority focus areas for the upcoming 7-day cycle. The AI coordinator will allocate open tasks and milestones within free focus windows.")
                .font(.subheadline)
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 6) {
                Text("Strategic Intent Notes")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)

                TextEditor(text: $strategicIntentNotes)
                    .frame(height: 120)
                    .padding(8)
                    .background(Color(.secondarySystemBackground))
                    .cornerRadius(8)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color(.separator), lineWidth: 1)
                    )
            }

            if let horizon = viewModel.horizon {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Horizon Summary")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)

                    HStack {
                        Text("Free Focus: \(horizon.totalFreeFocusMinutes / 60)h \(horizon.totalFreeFocusMinutes % 60)m")
                        Spacer()
                        Text("Overcommitted: \(horizon.overcommittedDaysCount) days")
                    }
                    .font(.caption)
                    .padding(10)
                    .background(Color(.tertiarySystemFill))
                    .cornerRadius(8)
                }
            }

            Button {
                Task {
                    await viewModel.createWeeklyPlanProposal(
                        strategicIntentNotes: strategicIntentNotes.isEmpty ? nil : strategicIntentNotes
                    )
                }
            } label: {
                HStack {
                    Image(systemName: "sparkles")
                    Text("Generate Plan Proposal")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(Color.blue)
                .foregroundColor(.white)
                .cornerRadius(12)
            }
            .padding(.top, 8)
        }
    }

    private var generatingProposalView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text("Synthesizing multi-day plan proposal...")
                .font(.headline)
            Text("Allocating task due dates and scheduling milestone reminders.")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private func proposalReviewView(proposal: WeeklyPlanProposalPayload) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            // Header & TTL Banner
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("Proposal Ready")
                        .font(.title3)
                        .fontWeight(.bold)
                    Spacer()
                    ttlCountdownBanner(expiresAt: proposal.expiresAt)
                }
                Text("Proposal ID: \(proposal.proposalId)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            Divider()

            // Operations List
            Text("Planned Operations (\(proposal.operations.count))")
                .font(.headline)

            ForEach(proposal.operations) { op in
                operationRow(op: op)
            }

            // Permissions Needed
            if !proposal.requiredPermissions.isEmpty {
                HStack(spacing: 6) {
                    Image(systemName: "lock.shield")
                        .foregroundColor(.orange)
                    Text("Permissions: \(proposal.requiredPermissions.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .padding(8)
                .background(Color.orange.opacity(0.1))
                .cornerRadius(6)
            }

            Divider()

            // Governed Actions (Confirm / Discard)
            HStack(spacing: 12) {
                Button(role: .destructive) {
                    viewModel.applyActiveWeeklyPlan(approved: false)
                } label: {
                    Text("Discard")
                        .fontWeight(.semibold)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.red.opacity(0.12))
                        .foregroundColor(.red)
                        .cornerRadius(12)
                }

                Button {
                    viewModel.applyActiveWeeklyPlan(approved: true)
                } label: {
                    Text("Confirm Plan")
                        .fontWeight(.semibold)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.blue)
                        .foregroundColor(.white)
                        .cornerRadius(12)
                }
            }
        }
    }

    private func ttlCountdownBanner(expiresAt: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: "timer")
                .font(.caption)
            Text(ttlFormatted(expiresAt: expiresAt))
                .font(.caption)
                .fontWeight(.bold)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color(.tertiarySystemFill))
        .cornerRadius(6)
    }

    private func ttlFormatted(expiresAt: String) -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var expDate = iso.date(from: expiresAt)
        if expDate == nil {
            iso.formatOptions = [.withInternetDateTime]
            expDate = iso.date(from: expiresAt)
        }

        guard let target = expDate else { return "TTL 30m" }
        let diff = Int(target.timeIntervalSinceNow)
        if diff <= 0 {
            return "Expired"
        }
        let m = diff / 60
        let s = diff % 60
        return String(format: "%02d:%02d", m, s)
    }

    private func operationRow(op: WeeklyOperationPayload) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(opTitle(op.operationType))
                    .font(.subheadline)
                    .fontWeight(.semibold)
                Spacer()
                Text(op.status.rawValue)
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.blue.opacity(0.12))
                    .foregroundColor(.blue)
                    .cornerRadius(4)
            }
            if let targetId = op.targetEntityId {
                Text("Target ID: \(targetId)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(10)
        .background(Color(.secondarySystemBackground))
        .cornerRadius(8)
    }

    private func opTitle(_ type: WeeklyOperationType) -> String {
        switch type {
        case .allocateTaskDueDate: return "Allocate Task Due Date"
        case .createMilestoneReminder: return "Create Milestone Reminder"
        case .createStrategicMemory: return "Create Strategic Memory"
        case .completeHonoredCommitment: return "Complete Honored Commitment"
        }
    }

    private func applyingView(proposalId: String, isDiscard: Bool) -> some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text(isDiscard ? "Discarding proposal on server..." : "Applying weekly plan on server...")
                .font(.headline)
            Text("Proposal ID: \(proposalId)")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private var executedSuccessView: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(.green)
                .font(.system(size: 48))
            Text("Weekly Plan Applied")
                .font(.title2)
                .fontWeight(.bold)
            Text("All multi-day allocations and reminders have been executed.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            Button("Done") {
                dismiss()
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private var partiallyExecutedView: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.orange)
                .font(.system(size: 48))
            Text("Partially Executed")
                .font(.title2)
                .fontWeight(.bold)
            Text("Some operations succeeded, but one or more operations encountered issues.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            Button("Acknowledge") {
                dismiss()
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private func failedPresentationView(reason: String?, operations: [WeeklyOperationPayload]) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "xmark.circle.fill")
                .foregroundColor(.red)
                .font(.system(size: 48))
            Text("Plan Execution Failed")
                .font(.title2)
                .fontWeight(.bold)
            if let r = reason {
                Text(r)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }
            Button("Close") {
                dismiss()
            }
            .buttonStyle(.bordered)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private var abortedView: some View {
        VStack(spacing: 16) {
            Image(systemName: "slash.circle")
                .foregroundColor(.secondary)
                .font(.system(size: 48))
            Text("Proposal Discarded")
                .font(.title2)
                .fontWeight(.bold)
            Text("The proposal was aborted on the server.")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Button("Dismiss") {
                dismiss()
            }
            .buttonStyle(.bordered)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private var expiredView: some View {
        VStack(spacing: 16) {
            Image(systemName: "clock.badge.xmark")
                .foregroundColor(.orange)
                .font(.system(size: 48))
            Text("Proposal Expired")
                .font(.title2)
                .fontWeight(.bold)
            Text("The 30-minute server reservation for this proposal has expired.")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Button("Start Fresh") {
                viewModel.startFresh()
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private func serverExecutionInProgressView(proposalId: String) -> some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text("Server Execution In Progress")
                .font(.title3)
                .fontWeight(.bold)
            Text("The server is still processing this proposal. Tap Check Status to retrieve the latest state.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)

            HStack(spacing: 12) {
                Button("Check Status") {
                    viewModel.resolveUnknownOutcome(proposalId: proposalId)
                }
                .buttonStyle(.borderedProminent)

                Button("Start Fresh") {
                    viewModel.startFresh()
                }
                .buttonStyle(.bordered)
            }
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private func contractViolationView(violation: WeeklyHorizonContractViolation) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "shield.slash.fill")
                .foregroundColor(.red)
                .font(.system(size: 48))
            Text("Integrity Violation")
                .font(.title2)
                .fontWeight(.bold)
            Text(violation.errorDescription ?? "Proposal integrity mismatch detected.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)

            Button("Start Fresh") {
                viewModel.startFresh()
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private func mutationOutcomeUnknownView(proposalId: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "wifi.exclamationmark")
                .foregroundColor(.orange)
                .font(.system(size: 48))
            Text("Outcome Unknown")
                .font(.title2)
                .fontWeight(.bold)
            Text("Network connection was interrupted while applying the proposal. The proposal state on the server is unknown.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)

            Text("Proposal ID: \(proposalId)")
                .font(.caption2)
                .foregroundColor(.secondary)

            VStack(spacing: 10) {
                Button {
                    viewModel.resolveUnknownOutcome(proposalId: proposalId)
                } label: {
                    HStack {
                        Image(systemName: "arrow.clockwise")
                        Text("Check Status")
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.blue)
                    .foregroundColor(.white)
                    .cornerRadius(10)
                }

                Button(role: .destructive) {
                    viewModel.abortUnknownProposal(proposalId: proposalId)
                } label: {
                    HStack {
                        Image(systemName: "xmark.octagon")
                        Text("Abort / Discard")
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.red.opacity(0.12))
                    .foregroundColor(.red)
                    .cornerRadius(10)
                }

                Button {
                    viewModel.startFresh()
                } label: {
                    Text("Start Fresh")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
                .padding(.top, 4)
            }
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }

    private func generalFailureView(message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.red)
                .font(.system(size: 48))
            Text("Request Failed")
                .font(.title2)
                .fontWeight(.bold)
            Text(message)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)

            if let recoveryContext = viewModel.retainedRecoveryContext {
                VStack(spacing: 10) {
                    Button("Check Status") {
                        viewModel.resolveUnknownOutcome(proposalId: recoveryContext.proposalId)
                    }
                    .buttonStyle(.borderedProminent)

                    Button("Start Fresh") {
                        viewModel.startFresh()
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.top, 8)
            } else {
                Button("Start Fresh") {
                    viewModel.startFresh()
                }
                .buttonStyle(.bordered)
                .padding(.top, 8)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 250)
    }
}

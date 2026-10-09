//
//  ScheduleRecoveryProposalSheet.swift
//  ProjectAurora
//
//  Created for Phase 6K / Phase 6I / Phase 6P: Schedule Recovery Proposal Presentation & Execution.
//

import SwiftUI

public struct ScheduleRecoveryProposalSheet: View {
    @Environment(\.dismiss) private var dismiss
    public let proposal: ScheduleRecoveryProposal
    @ObservedObject public var viewModel: ScheduleRecoveryViewModel

    public init(proposal: ScheduleRecoveryProposal, viewModel: ScheduleRecoveryViewModel? = nil) {
        self.proposal = proposal
        self.viewModel = viewModel ?? ScheduleRecoveryViewModel()
    }

    public init(viewModel: ScheduleRecoveryViewModel, proposal: ScheduleRecoveryProposal) {
        self.proposal = proposal
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            ZStack {
                Color(.systemGroupedBackground).ignoresSafeArea()

                switch viewModel.sheetState {
                case .idle:
                    proposalContentView
                case .readyToApply:
                    proposalContentView
                case .applying:
                    loadingStateView(title: "Applying Recovery Plan", subtitle: "Dispatching mutations to server...")
                case .cancelling:
                    loadingStateView(title: "Cancelling Proposal", subtitle: "Informing server coordinator...")
                case .recovering:
                    loadingStateView(title: "Verifying Server Status", subtitle: "Reconciling authoritative state...")
                case .lockConflict(let message):
                    lockConflictView(message: message)
                case .mutationOutcomeUnknown(let proposalId, let reason):
                    mutationOutcomeUnknownView(proposalId: proposalId, reason: reason)
                case .resolved(let resolvedState):
                    resolvedStateView(resolvedState: resolvedState)
                case .errorPresentation(let message):
                    errorStateView(message: message)
                }
            }
            .navigationTitle("Recovery Proposal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Dismiss") {
                        dismiss()
                    }
                    .disabled(viewModel.isLoading)
                }
            }
            .onAppear {
                viewModel.setProposal(proposal)
            }
        }
    }

    // MARK: - Proposal Content

    private var proposalContentView: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Header Card
                headerCard

                // Proposed Operations List
                operationsSection

                // Preconditions Summary
                preconditionsCard

                // Error message if any
                if let err = viewModel.errorMessage {
                    Text(err)
                        .font(.caption)
                        .foregroundColor(.red)
                        .padding(.horizontal)
                }

                // Action Buttons
                actionButtons
            }
            .padding(16)
        }
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .foregroundColor(.accentColor)
                Text("Schedule Recovery Plan")
                    .font(.headline)
                Spacer()
                Text(proposal.status.rawValue)
                    .font(.caption2.bold())
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.blue.opacity(0.15))
                    .foregroundColor(.blue)
                    .cornerRadius(4)
            }

            Text("Review proposed changes before applying. All operations require explicit confirmation.")
                .font(.subheadline)
                .foregroundColor(.secondary)

            Divider()

            HStack {
                Label("\(proposal.operations.count) Operations", systemImage: "list.bullet")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Label("Expires \(formatDate(proposal.expiresAt))", systemImage: "clock")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(16)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }

    // MARK: - Operations Section

    private var operationsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("PROPOSED RESCHEDULING")
                .font(.caption.bold())
                .foregroundColor(.secondary)
                .padding(.horizontal, 4)

            ForEach(Array(proposal.operations.enumerated()), id: \.element.operationId) { index, op in
                operationRow(operation: op, step: index + 1)
            }
        }
    }

    private func operationRow(operation: ScheduleRecoveryOperation, step: Int) -> some View {
        HStack(spacing: 12) {
            Circle()
                .fill(Color.accentColor.opacity(0.15))
                .frame(width: 32, height: 32)
                .overlay(
                    Text("\(step)")
                        .font(.caption.bold())
                        .foregroundColor(.accentColor)
                )

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(operation.operationType == .rescheduleTask ? "Task" : "Reminder")
                        .font(.caption2.bold())
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(operation.operationType == .rescheduleTask ? Color.blue.opacity(0.1) : Color.orange.opacity(0.1))
                        .foregroundColor(operation.operationType == .rescheduleTask ? .blue : .orange)
                        .cornerRadius(4)

                    Text(operation.targetEntityId)
                        .font(.caption.monospaced())
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }

                HStack {
                    Image(systemName: "arrow.right.circle")
                        .font(.caption)
                        .foregroundColor(.accentColor)
                    Text("New Time: \(formatDate(operation.newScheduledTime))")
                        .font(.subheadline)
                        .fontWeight(.medium)
                }
            }
            Spacer()
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(10)
    }

    // MARK: - Preconditions Card

    private var preconditionsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("PRECONDITIONS VERIFIED")
                .font(.caption.bold())
                .foregroundColor(.secondary)

            HStack {
                Image(systemName: "checkmark.shield.fill")
                    .foregroundColor(.green)
                Text("State Hash Verified")
                    .font(.caption)
                Spacer()
                Text(String(proposal.preconditionsHash.prefix(12)) + "...")
                    .font(.caption2.monospaced())
                    .foregroundColor(.secondary)
            }

            HStack {
                Image(systemName: "clock.badge.checkmark")
                    .foregroundColor(.green)
                Text("Target Window")
                    .font(.caption)
                Spacer()
                Text("\(formatDate(proposal.calendarPrecondition.windowStart)) – \(formatDate(proposal.calendarPrecondition.windowEnd))")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(12)
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }

    // MARK: - Action Buttons

    private var actionButtons: some View {
        VStack(spacing: 10) {
            Button {
                viewModel.applyProposal(proposalId: proposal.proposalId, confirmed: true)
            } label: {
                HStack {
                    Image(systemName: "checkmark.circle.fill")
                    Text("Confirm & Apply Recovery")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(12)
            }
            .disabled(viewModel.isLoading)

            Button {
                viewModel.cancelProposal(proposalId: proposal.proposalId)
            } label: {
                HStack {
                    Text("Cancel Proposal")
                        .foregroundColor(.red)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
            }
            .disabled(viewModel.isLoading)
        }
        .padding(.top, 8)
    }

    // MARK: - Loading View

    private func loadingStateView(title: String, subtitle: String) -> some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text(title)
                .font(.headline)
            Text(subtitle)
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .padding(24)
    }

    // MARK: - Lock Conflict View

    private func lockConflictView(message: String) -> some View {
        VStack(spacing: 20) {
            Image(systemName: "lock.trianglebadge.exclamationmark")
                .font(.system(size: 64))
                .foregroundColor(.orange)

            Text("Execution In Progress")
                .font(.title3.bold())

            Text(message)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button {
                viewModel.applyProposal(proposalId: proposal.proposalId, confirmed: true)
            } label: {
                Text("Try Again")
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(Color.accentColor)
                    .foregroundColor(.white)
                    .cornerRadius(10)
            }
            .padding(.horizontal, 32)

            Button("Dismiss") {
                dismiss()
            }
            .foregroundColor(.secondary)
        }
        .padding(24)
    }

    // MARK: - Mutation Outcome Unknown View

    private func mutationOutcomeUnknownView(proposalId: String, reason: String) -> some View {
        VStack(spacing: 20) {
            Image(systemName: "questionmark.diamond.fill")
                .font(.system(size: 64))
                .foregroundColor(.orange)

            Text("Server Status Unknown")
                .font(.title3.bold())

            Text("The server outcome is uncertain: \(reason)\n\nTo prevent conflicting mutations, re-applying is disabled until status is verified.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button {
                viewModel.recoverProposal(proposalId: proposalId)
            } label: {
                HStack {
                    Image(systemName: "arrow.clockwise")
                    Text("Verify Server Status")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(.horizontal, 32)

            Button("Dismiss") {
                dismiss()
            }
            .foregroundColor(.secondary)
        }
        .padding(24)
    }

    // MARK: - Resolved State View

    @ViewBuilder
    private func resolvedStateView(resolvedState: ScheduleRecoveryResolvedState) -> some View {
        switch resolvedState {
        case .readyToApply:
            proposalContentView

        case .draft:
            VStack(spacing: 16) {
                Image(systemName: "doc.text")
                    .font(.system(size: 48))
                    .foregroundColor(.secondary)
                Text("Draft Proposal")
                    .font(.headline)
                Button("Done") { dismiss() }
            }

        case .applied(let result):
            VStack(spacing: 20) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.green)

                Text("Recovery Applied Successfully")
                    .font(.title3.bold())

                Text("\(result.completedOperations.count) operations applied.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)

                if let note = result.diagnostics.presentationNotes.first {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(note.title)
                            .font(.caption.bold())
                        Text(note.details)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(12)
                    .background(Color(.secondarySystemGroupedBackground))
                    .cornerRadius(8)
                }

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)

        case .appliedWithoutExecutionResult(let prop):
            VStack(spacing: 20) {
                Image(systemName: "checkmark.seal.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.green)

                Text("Recovery Applied")
                    .font(.title3.bold())

                Text("Execution details unavailable from server store.")
                    .font(.caption.bold())
                    .foregroundColor(.secondary)

                Text("The recovery proposal was successfully applied on the server. All requested task and reminder reschedules are complete.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Proposal ID: \(prop.proposalId)")
                        .font(.caption.monospaced())
                    Text("Operations: \(prop.operations.count)")
                        .font(.caption)
                }
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(8)

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)

        case .partiallyExecuted(let result, let detail):
            VStack(spacing: 20) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.orange)

                Text("Partially Executed")
                    .font(.title3.bold())

                let count = result?.completedOperations.count ?? detail?.completedOperations.count ?? 0
                let failedId = result?.failedOperationId ?? detail?.failedOperationId ?? "Unknown"

                Text("\(count) operations completed before operation \(failedId) failed.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)

        case .failed(let result, let detail):
            VStack(spacing: 20) {
                Image(systemName: "xmark.octagon.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.red)

                Text("Recovery Execution Failed")
                    .font(.title3.bold())

                let reason = result?.failureReason ?? detail?.message ?? "Operation failed"
                Text(reason)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)

        case .preconditionFailed(let reason):
            VStack(spacing: 20) {
                Image(systemName: "shield.slash.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.red)

                Text("Precondition Check Failed")
                    .font(.title3.bold())

                Text(reason)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)

        case .abortedWithoutExecutionResult(let prop):
            VStack(spacing: 20) {
                Image(systemName: "exclamationmark.octagon.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.red)

                Text("Recovery Aborted")
                    .font(.title3.bold())

                Text("Execution details unavailable from server store.")
                    .font(.caption.bold())
                    .foregroundColor(.secondary)

                if let reason = prop.failureReason {
                    Text("Reason: \(reason)")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)

        case .terminalWithResultFetchError(let prop, let error):
            VStack(spacing: 20) {
                Image(systemName: "exclamationmark.icloud.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.orange)

                Text("Terminal: \(prop.status.rawValue)")
                    .font(.title3.bold())

                Text("Proposal completed on server, but fetching execution details failed: \(error)")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button {
                    viewModel.retryFetchExecutionDetails(proposalId: prop.proposalId)
                } label: {
                    HStack {
                        Image(systemName: "arrow.clockwise")
                        Text("Retry Fetching Details")
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(Color.accentColor)
                    .foregroundColor(.white)
                    .cornerRadius(10)
                }
                .padding(.horizontal, 32)

                Button("Close") {
                    dismiss()
                }
                .foregroundColor(.secondary)
            }
            .padding(24)

        case .cancelled:
            VStack(spacing: 20) {
                Image(systemName: "slash.circle")
                    .font(.system(size: 64))
                    .foregroundColor(.secondary)

                Text("Proposal Cancelled")
                    .font(.title3.bold())

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)

        case .expired:
            VStack(spacing: 20) {
                Image(systemName: "clock.badge.xmark")
                    .font(.system(size: 64))
                    .foregroundColor(.secondary)

                Text("Proposal Expired")
                    .font(.title3.bold())

                Text("This proposal has exceeded its time-to-live.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)

                Button("Done") {
                    dismiss()
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 32)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(24)
        }
    }

    private func errorStateView(message: String) -> some View {
        VStack(spacing: 20) {
            Image(systemName: "exclamationmark.circle.fill")
                .font(.system(size: 64))
                .foregroundColor(.red)

            Text("Error")
                .font(.title3.bold())

            Text(message)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button("Done") {
                dismiss()
            }
        }
        .padding(24)
    }

    private func formatDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "MMM d, HH:mm"
        return f.string(from: date)
    }
}

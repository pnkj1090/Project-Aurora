import SwiftUI

public struct EventDebriefSheet: View {
    @ObservedObject public var viewModel: EventBriefingViewModel
    public let eventId: String
    public let eventTitle: String

    @Environment(\.dismiss) private var dismiss

    // Local draft states before submitting proposal
    @State private var rawNotes: String = ""
    @State private var taskTitle: String = ""
    @State private var reminderTitle: String = ""
    @State private var isShowingAddTask: Bool = false
    @State private var isShowingAddReminder: Bool = false
    @State private var taskCandidates: [DebriefTaskCandidate] = []
    @State private var reminderCandidates: [DebriefReminderCandidate] = []

    public init(viewModel: EventBriefingViewModel, eventId: String, eventTitle: String) {
        self.viewModel = viewModel
        self.eventId = eventId
        self.eventTitle = eventTitle
    }

    public var body: some View {
        NavigationStack {
            Group {
                switch viewModel.debriefState {
                case .idle, .draftingNotes:
                    draftingView

                case .proposing:
                    VStack(spacing: 16) {
                        ProgressView()
                            .scaleEffect(1.2)
                        Text("Synthesizing Debrief Proposal...")
                            .font(.headline)
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                case .proposalReview(let proposal):
                    proposalReviewView(proposal: proposal)

                case .applying:
                    VStack(spacing: 16) {
                        ProgressView()
                            .scaleEffect(1.2)
                        Text("Executing Governed Debrief Operations...")
                            .font(.headline)
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                case .terminalCompleted(let proposal):
                    terminalResultView(
                        title: "Debrief Completed",
                        subtitle: "All operations executed and committed under governance.",
                        icon: "checkmark.circle.fill",
                        iconColor: .green,
                        proposal: proposal
                    )

                case .terminalPartial(let proposal):
                    terminalResultView(
                        title: "Partially Executed",
                        subtitle: "Some operations committed, but execution encountered a failure.",
                        icon: "exclamationmark.triangle.fill",
                        iconColor: .orange,
                        proposal: proposal
                    )

                case .terminalFailed(let proposal):
                    terminalResultView(
                        title: "Debrief Execution Failed",
                        subtitle: proposal.failureReason?.rawValue ?? "Operations were rolled back / zero mutations committed.",
                        icon: "xmark.octagon.fill",
                        iconColor: .red,
                        proposal: proposal
                    )

                case .terminalAborted(let proposal):
                    terminalResultView(
                        title: "Debrief Aborted",
                        subtitle: "The proposal was explicitly rejected or aborted.",
                        icon: "hand.raised.fill",
                        iconColor: .secondary,
                        proposal: proposal
                    )

                case .terminalExpired(let proposal):
                    terminalResultView(
                        title: "Proposal Expired",
                        subtitle: "The proposal exceeded its authoritative TTL.",
                        icon: "clock.badge.xmark.fill",
                        iconColor: .orange,
                        proposal: proposal
                    )

                case .mutationOutcomeUnknown(let proposalId, let reason):
                    recoveryView(proposalId: proposalId, reason: reason)

                case .genericError(let message):
                    VStack(spacing: 16) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.system(size: 44))
                            .foregroundColor(.red)
                        Text("Debrief Error")
                            .font(.headline)
                        Text(message)
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)

                        Button("Dismiss") {
                            viewModel.startFresh()
                        }
                        .buttonStyle(.borderedProminent)
                        .padding(.top, 8)
                    }
                    .padding()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .navigationTitle("Event Debrief")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") {
                        dismiss()
                    }
                }
            }
        }
    }

    // MARK: - Step 1: Drafting Notes & Candidates

    private var draftingView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Event Header
                VStack(alignment: .leading, spacing: 4) {
                    Text(eventTitle)
                        .font(.title3.bold())
                    Text("Capture key takeaways, action items, and follow-ups.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(12)

                // Meeting Notes Editor
                VStack(alignment: .leading, spacing: 8) {
                    Label("Meeting Notes", systemImage: "note.text")
                        .font(.footnote.bold())
                        .foregroundColor(.secondary)
                        .textCase(.uppercase)

                    TextEditor(text: $rawNotes)
                        .frame(minHeight: 120)
                        .padding(8)
                        .background(Color(.secondarySystemGroupedBackground))
                        .cornerRadius(8)
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(Color(.systemGray4), lineWidth: 1)
                        )
                }

                // Action Item Candidates
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Label("Action Items (\(taskCandidates.count))", systemImage: "checkmark.circle")
                            .font(.footnote.bold())
                            .foregroundColor(.indigo)
                            .textCase(.uppercase)
                        Spacer()
                        Button {
                            isShowingAddTask.toggle()
                        } label: {
                            Image(systemName: isShowingAddTask ? "chevron.up" : "plus.circle.fill")
                                .font(.title3)
                                .foregroundColor(.indigo)
                        }
                    }

                    if isShowingAddTask {
                        HStack {
                            TextField("Action item title...", text: $taskTitle)
                                .textFieldStyle(.roundedBorder)
                            Button("Add") {
                                guard !taskTitle.trimmingCharacters(in: .whitespaces).isEmpty else { return }
                                taskCandidates.append(DebriefTaskCandidate(exactTitle: taskTitle.trimmingCharacters(in: .whitespaces)))
                                taskTitle = ""
                                isShowingAddTask = false
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    }

                    ForEach(Array(taskCandidates.enumerated()), id: \.offset) { idx, cand in
                        HStack {
                            Text(cand.exactTitle)
                                .font(.subheadline)
                            Spacer()
                            Button {
                                taskCandidates.remove(at: idx)
                            } label: {
                                Image(systemName: "trash")
                                    .foregroundColor(.red)
                                    .font(.caption)
                            }
                        }
                        .padding(8)
                        .background(Color(.secondarySystemGroupedBackground))
                        .cornerRadius(8)
                    }
                }

                // Follow-up Reminder Candidates
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Label("Follow-up Reminders (\(reminderCandidates.count))", systemImage: "bell.badge")
                            .font(.footnote.bold())
                            .foregroundColor(.orange)
                            .textCase(.uppercase)
                        Spacer()
                        Button {
                            isShowingAddReminder.toggle()
                        } label: {
                            Image(systemName: isShowingAddReminder ? "chevron.up" : "plus.circle.fill")
                                .font(.title3)
                                .foregroundColor(.orange)
                        }
                    }

                    if isShowingAddReminder {
                        HStack {
                            TextField("Reminder title...", text: $reminderTitle)
                                .textFieldStyle(.roundedBorder)
                            Button("Add") {
                                guard !reminderTitle.trimmingCharacters(in: .whitespaces).isEmpty else { return }
                                let schedStr = ISO8601DateFormatter().string(from: Date().addingTimeInterval(86400))
                                reminderCandidates.append(DebriefReminderCandidate(exactTitle: reminderTitle.trimmingCharacters(in: .whitespaces), exactScheduledAt: schedStr))
                                reminderTitle = ""
                                isShowingAddReminder = false
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    }

                    ForEach(Array(reminderCandidates.enumerated()), id: \.offset) { idx, cand in
                        HStack {
                            Text(cand.exactTitle)
                                .font(.subheadline)
                            Spacer()
                            Button {
                                reminderCandidates.remove(at: idx)
                            } label: {
                                Image(systemName: "trash")
                                    .foregroundColor(.red)
                                    .font(.caption)
                            }
                        }
                        .padding(8)
                        .background(Color(.secondarySystemGroupedBackground))
                        .cornerRadius(8)
                    }
                }

                // Submit Proposal Button
                Button {
                    Task {
                        let payload = ProposeDebriefRequestPayload(
                            rawNotes: rawNotes,
                            taskCandidates: taskCandidates.isEmpty ? nil : taskCandidates,
                            reminderCandidates: reminderCandidates.isEmpty ? nil : reminderCandidates,
                            memoryCandidates: nil
                        )
                        await viewModel.proposeDebrief(eventId: eventId, payload: payload)
                    }
                } label: {
                    HStack {
                        Spacer()
                        Label("Propose Debrief Operations", systemImage: "wand.and.stars")
                            .fontWeight(.semibold)
                        Spacer()
                    }
                    .padding()
                }
                .buttonStyle(.borderedProminent)
                .disabled(rawNotes.trimmingCharacters(in: .whitespaces).isEmpty && taskCandidates.isEmpty && reminderCandidates.isEmpty)
            }
            .padding()
        }
    }

    // MARK: - Step 2: Exact Server Proposal Review

    private func proposalReviewView(proposal: DebriefProposal) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // Proposal Header
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Authoritative Server Proposal")
                            .font(.headline)
                        Spacer()
                        Text(proposal.status.rawValue.uppercased())
                            .font(.caption2.bold())
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color.blue.opacity(0.15))
                            .foregroundColor(.blue)
                            .cornerRadius(4)
                    }

                    Text("ID: \(proposal.proposalId)")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    Text("Expires: \(proposal.expiresAt)")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                .padding()
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(12)

                // Ordered Operations
                Text("Proposed Operations (\(proposal.orderedOperations.count))")
                    .font(.footnote.bold())
                    .foregroundColor(.secondary)
                    .textCase(.uppercase)

                ForEach(proposal.orderedOperations) { op in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            switch op.operationType {
                            case .createTask:
                                Label("Create Task", systemImage: "checkmark.circle.fill")
                                    .font(.subheadline.bold())
                                    .foregroundColor(.indigo)
                            case .createReminder:
                                Label("Create Reminder", systemImage: "bell.fill")
                                    .font(.subheadline.bold())
                                    .foregroundColor(.orange)
                            case .createMemory:
                                Label("Store Memory", systemImage: "brain.head.profile")
                                    .font(.subheadline.bold())
                                    .foregroundColor(.teal)
                            }
                            Spacer()
                            Text(op.status.rawValue.capitalized)
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }

                        Divider()

                        switch op.proposedPayload {
                        case .createTask(let p):
                            Text("Title: \(p.exactTitle)")
                                .font(.body)
                            if let due = p.exactDueAt {
                                Text("Due: \(due)")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        case .createReminder(let p):
                            Text("Title: \(p.exactTitle)")
                                .font(.body)
                            Text("Alert: \(p.exactScheduledAt)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        case .createMemory(let p):
                            Text("Takeaway: \(p.exactContent)")
                                .font(.body)
                            Text("Type: \(p.exactMemoryType)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding()
                    .background(Color(.secondarySystemGroupedBackground))
                    .cornerRadius(8)
                }

                // Explicit Confirmation Buttons
                HStack(spacing: 12) {
                    Button(role: .destructive) {
                        viewModel.applyProposal(proposalId: proposal.proposalId, approved: false)
                    } label: {
                        HStack {
                            Spacer()
                            Label("Reject & Abort", systemImage: "xmark")
                            Spacer()
                        }
                        .padding()
                    }
                    .buttonStyle(.bordered)

                    Button {
                        viewModel.applyProposal(proposalId: proposal.proposalId, approved: true)
                    } label: {
                        HStack {
                            Spacer()
                            Label("Approve & Execute", systemImage: "checkmark")
                            Spacer()
                        }
                        .padding()
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding(.top, 8)
            }
            .padding()
        }
    }

    // MARK: - Step 3: Terminal Results Presentation

    private func terminalResultView(
        title: String,
        subtitle: String,
        icon: String,
        iconColor: Color,
        proposal: DebriefProposal
    ) -> some View {
        ScrollView {
            VStack(spacing: 20) {
                Image(systemName: icon)
                    .font(.system(size: 52))
                    .foregroundColor(iconColor)

                Text(title)
                    .font(.title2.bold())

                Text(subtitle)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                VStack(alignment: .leading, spacing: 8) {
                    ForEach(proposal.orderedOperations) { op in
                        HStack {
                            switch op.status {
                            case .completed:
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundColor(.green)
                            case .failed:
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundColor(.red)
                            case .skipped:
                                Image(systemName: "arrowshape.turn.up.right.fill")
                                    .foregroundColor(.secondary)
                            case .pending:
                                Image(systemName: "clock")
                                    .foregroundColor(.orange)
                            }

                            Text(op.operationType.rawValue)
                                .font(.subheadline.bold())

                            Spacer()

                            if let eid = op.createdEntityId {
                                Text("ID: \(eid)")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .padding(8)
                        .background(Color(.secondarySystemGroupedBackground))
                        .cornerRadius(8)
                    }
                }
                .padding()

                Button("Done") {
                    viewModel.startFresh()
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .padding(.top, 8)
            }
            .padding()
        }
    }

    // MARK: - Step 4: Unknown Transport Uncertainty Recovery

    private func recoveryView(proposalId: String, reason: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 50))
                .foregroundColor(.orange)

            Text("Mutation Outcome Unknown")
                .font(.title3.bold())

            Text(reason)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button {
                viewModel.resolveUnknownOutcome(proposalId: proposalId)
            } label: {
                Label("Resolve Unknown Outcome", systemImage: "arrow.clockwise")
                    .fontWeight(.medium)
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

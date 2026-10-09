import SwiftUI

// MARK: - Proactive View

/// User interface for viewing and managing proactive suggestions / proposals.
///
/// PHASE 5H INVARIANTS:
/// - Represents proposals as "Proactive Suggestions", NOT delivered notifications.
/// - Zero notification scheduling (no UNUserNotificationCenter, no APNs/FCM).
/// - Non-destructive dismissal.
struct ProactiveView: View {
    @State private var candidates: [ProactiveCandidateItem] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var noticeMessage: String?
    @State private var pendingConfirmation: ProactivePendingConfirmationOut?
    @State private var pendingConversationId: String?
    @State private var pendingExecutionId: String?
    @State private var activeNavigationTarget: NavigationTarget?
    @ObservedObject private var taskViewModel: TaskViewModel
    @ObservedObject private var commitmentViewModel: CommitmentViewModel

    private let apiClient: AuroraAPIClient

    @MainActor
    init(
        apiClient: AuroraAPIClient = AuroraAPIClient(),
        taskViewModel: TaskViewModel? = nil,
        commitmentViewModel: CommitmentViewModel? = nil
    ) {
        self.apiClient = apiClient
        self.taskViewModel = taskViewModel ?? TaskViewModel()
        self.commitmentViewModel = commitmentViewModel ?? CommitmentViewModel()
    }

    var body: some View {
        Group {
            if isLoading && candidates.isEmpty {
                ProgressView("Loading suggestions...")
            } else if let error = errorMessage, candidates.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.largeTitle)
                        .foregroundColor(.orange)
                    Text(error)
                        .multilineTextAlignment(.center)
                        .font(.body)
                    Button("Retry") {
                        Task { await loadCandidates() }
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding()
            } else if candidates.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 48))
                        .foregroundColor(.secondary)
                    Text("No Proactive Suggestions")
                        .font(.headline)
                    Text("Pull to refresh to evaluate Tasks, Commitments, Calendar, and Reminders on-demand.")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                }
                .padding()
            } else {
                List {
                    if let notice = noticeMessage {
                        Section {
                            HStack {
                                Image(systemName: "info.circle")
                                    .foregroundColor(.blue)
                                Text(notice)
                                    .font(.subheadline)
                            }
                        }
                    }

                    ForEach(candidates) { candidate in
                        candidateRow(candidate)
                    }
                }
                .refreshable {
                    await refreshAndEvaluate()
                }
            }
        }
        .navigationTitle("Proactive Suggestions")
        .task {
            await loadCandidates()
        }
        .alert("Confirmation Required", isPresented: Binding(
            get: { pendingConfirmation != nil },
            set: { if !$0 { pendingConfirmation = nil } }
        )) {
            Button("Cancel", role: .cancel) {
                if let conf = pendingConfirmation, let convId = pendingConversationId, let execId = pendingExecutionId {
                    Task { await resolveConfirmation(conversationId: convId, executionId: execId, confirmationId: conf.confirmationId, approved: false) }
                }
                pendingConfirmation = nil
                pendingConversationId = nil
                pendingExecutionId = nil
            }
            Button("Confirm", role: .destructive) {
                if let conf = pendingConfirmation, let convId = pendingConversationId, let execId = pendingExecutionId {
                    Task { await resolveConfirmation(conversationId: convId, executionId: execId, confirmationId: conf.confirmationId, approved: true) }
                }
                pendingConfirmation = nil
                pendingConversationId = nil
                pendingExecutionId = nil
            }
        } message: {
            if let conf = pendingConfirmation {
                Text(conf.description)
            }
        }
        .sheet(item: Binding(
            get: { activeNavigationTarget.map { TargetWrapper(target: $0) } },
            set: { activeNavigationTarget = $0?.target }
        )) { wrapper in
            navigationDestinationView(wrapper.target)
        }
    }

    // MARK: - Row View

    private func candidateRow(_ candidate: ProactiveCandidateItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(candidate.trigger.displayName, systemImage: candidate.trigger.systemIcon)
                    .font(.caption)
                    .foregroundColor(.secondary)

                Spacer()

                priorityBadge(candidate.priority)
            }

            Text(candidate.title)
                .font(.headline)

            Text(candidate.message)
                .font(.subheadline)
                .foregroundColor(.secondary)

            HStack {
                Text("Action: \(candidate.action.displayName)")
                    .font(.caption2)
                    .foregroundColor(.secondary)

                Spacer()

                Button {
                    Task { await acceptCandidate(candidate.id) }
                } label: {
                    Text("Accept")
                        .font(.caption.bold())
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)

                Button(role: .destructive) {
                    Task { await dismissCandidate(candidate.id) }
                } label: {
                    Text("Dismiss")
                        .font(.caption.bold())
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .padding(.vertical, 4)
    }

    // MARK: - Priority Badge

    private func priorityBadge(_ priority: ProactivePriority) -> some View {
        Text(priority.displayName)
            .font(.caption2.bold())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(badgeColor(priority).opacity(0.15))
            .foregroundColor(badgeColor(priority))
            .clipShape(Capsule())
    }

    private func badgeColor(_ priority: ProactivePriority) -> Color {
        switch priority {
        case .high: return .red
        case .medium: return .orange
        case .low: return .blue
        }
    }

    // MARK: - Navigation Sheet View

    @ViewBuilder
    private func navigationDestinationView(_ target: NavigationTarget) -> some View {
        NavigationView {
            VStack(spacing: 16) {
                Image(systemName: targetIcon(target.type))
                    .font(.system(size: 48))
                    .foregroundColor(.accentColor)
                Text(targetTitle(target.type))
                    .font(.title2.bold())
                Text("Identifier: \(target.id)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text("Navigated to destination entity.")
                    .font(.body)
                    .foregroundColor(.secondary)
            }
            .padding()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        activeNavigationTarget = nil
                    }
                }
            }
        }
    }

    private func targetIcon(_ type: NavigationTargetType) -> String {
        switch type {
        case .task: return "checklist"
        case .commitment: return "hand.raised"
        case .calendarEvent: return "calendar"
        case .reminder: return "bell"
        }
    }

    private func targetTitle(_ type: NavigationTargetType) -> String {
        switch type {
        case .task: return "Task Details"
        case .commitment: return "Commitment Details"
        case .calendarEvent: return "Calendar Event"
        case .reminder: return "Reminder Details"
        }
    }

    // MARK: - Actions

    private func loadCandidates() async {
        isLoading = true
        errorMessage = nil
        do {
            candidates = try await apiClient.fetchProactiveCandidates(limit: 50, offset: 0, status: "eligible")
        } catch {
            errorMessage = "Failed to load proactive suggestions: \(error.localizedDescription)"
        }
        isLoading = false
    }

    private func refreshAndEvaluate() async {
        do {
            _ = try await apiClient.evaluateProposals()
            await loadCandidates()
        } catch {
            errorMessage = "Failed to evaluate proactive proposals: \(error.localizedDescription)"
        }
    }

    private func acceptCandidate(_ id: String) async {
        do {
            let res = try await apiClient.acceptProactiveCandidate(id: id)
            if res.confirmationRequired,
               let pending = res.pendingConfirmation,
               let conversationId = res.conversationId,
               let executionId = res.executionId,
               !executionId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                pendingConfirmation = pending
                pendingConversationId = conversationId
                pendingExecutionId = executionId
            } else if res.confirmationRequired {
                errorMessage = "The server did not return complete confirmation details. Refresh suggestions and try again."
            } else if let nav = res.navigationTarget {
                activeNavigationTarget = nav
                candidates.removeAll { $0.id == id }
            } else if res.status == "executed" {
                noticeMessage = res.message ?? "Action executed successfully."
                candidates.removeAll { $0.id == id }
            } else if res.status == "failed" {
                errorMessage = res.errorMessage ?? "Action execution failed."
            }
        } catch {
            errorMessage = "Error accepting candidate: \(error.localizedDescription)"
        }
    }

    private func resolveConfirmation(conversationId: String, executionId: String, confirmationId: String, approved: Bool) async {
        do {
            let response = try await apiClient.confirmAction(
                conversationId: conversationId,
                confirmationId: confirmationId,
                executionId: executionId,
                approved: approved
            )
            if approved {
                for action in response.clientActions ?? [] {
                    taskViewModel.applyAssistantAction(action)
                    commitmentViewModel.applyAssistantAction(action)
                }
            }
            await loadCandidates()
        } catch {
            errorMessage = "Confirmation resolution failed: \(error.localizedDescription)"
        }
    }

    private func dismissCandidate(_ id: String) async {
        candidates.removeAll { $0.id == id }
        do {
            _ = try await apiClient.dismissProactiveCandidate(id: id)
        } catch {
            await loadCandidates()
        }
    }
}

// Helper wrapper for Identifiable sheet binding
private struct TargetWrapper: Identifiable {
    let target: NavigationTarget
    var id: String { "\(target.type.rawValue)_\(target.id)" }
}

import SwiftUI

public struct EventBriefingSheet: View {
    @ObservedObject public var viewModel: EventBriefingViewModel
    public let eventId: String
    public let eventTitle: String

    @Environment(\.dismiss) private var dismiss

    public init(viewModel: EventBriefingViewModel, eventId: String, eventTitle: String) {
        self.viewModel = viewModel
        self.eventId = eventId
        self.eventTitle = eventTitle
    }

    public var body: some View {
        NavigationStack {
            Group {
                switch viewModel.briefingState {
                case .idle, .loadingBriefing:
                    VStack(spacing: 16) {
                        ProgressView()
                            .scaleEffect(1.2)
                        Text("Synthesizing Event Briefing...")
                            .font(.headline)
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                case .briefingFailed(let message):
                    VStack(spacing: 16) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.system(size: 44))
                            .foregroundColor(.orange)
                        Text("Failed to Load Briefing")
                            .font(.headline)
                        Text(message)
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                        Button {
                            Task {
                                await viewModel.loadBriefing(eventId: eventId)
                            }
                        } label: {
                            Label("Retry", systemImage: "arrow.clockwise")
                                .fontWeight(.medium)
                        }
                        .buttonStyle(.borderedProminent)
                        .padding(.top, 8)
                    }
                    .padding()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                case .briefingLoaded(let briefing):
                    ScrollView {
                        VStack(alignment: .leading, spacing: 20) {
                            // Header Card
                            headerSection(briefing: briefing)

                            // Executive Summary Card
                            summarySection(summary: briefing.summary)

                            // Attendees
                            if !briefing.attendees.isEmpty {
                                attendeesSection(attendees: briefing.attendees)
                            }

                            // Related Tasks
                            if !briefing.relatedTasks.isEmpty {
                                tasksSection(tasks: briefing.relatedTasks)
                            }

                            // Active Commitments
                            if !briefing.activeCommitments.isEmpty {
                                commitmentsSection(commitments: briefing.activeCommitments)
                            }

                            // Relevant Contextual Memories
                            if !briefing.relevantMemories.isEmpty {
                                memoriesSection(memories: briefing.relevantMemories)
                            }
                        }
                        .padding()
                    }
                }
            }
            .navigationTitle("Event Briefing")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") {
                        dismiss()
                    }
                }
            }
            .task {
                if case .idle = viewModel.briefingState {
                    await viewModel.loadBriefing(eventId: eventId)
                }
            }
        }
    }

    // MARK: - Sections

    private func headerSection(briefing: EventBriefing) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(briefing.title)
                .font(.title2)
                .fontWeight(.bold)

            HStack(spacing: 12) {
                Label(briefing.startTime, systemImage: "clock")
                    .font(.caption)
                    .foregroundColor(.secondary)

                if let loc = briefing.location, !loc.isEmpty {
                    Label(loc, systemImage: "mappin.and.ellipse")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }

    private func summarySection(summary: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Executive Briefing", systemImage: "sparkles")
                .font(.footnote)
                .fontWeight(.bold)
                .foregroundColor(.accentColor)
                .textCase(.uppercase)

            Text(summary)
                .font(.body)
                .lineSpacing(4)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.08))
        .cornerRadius(12)
    }

    private func attendeesSection(attendees: [BriefingAttendee]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Attendees (\(attendees.count))", systemImage: "person.2.fill")
                .font(.footnote)
                .fontWeight(.bold)
                .foregroundColor(.secondary)
                .textCase(.uppercase)

            ForEach(attendees) { att in
                HStack {
                    Circle()
                        .fill(Color.blue.opacity(0.2))
                        .frame(width: 32, height: 32)
                        .overlay(
                            Text(String((att.name ?? att.email ?? "?").prefix(1)).uppercased())
                                .font(.caption.bold())
                                .foregroundColor(.blue)
                        )

                    VStack(alignment: .leading, spacing: 2) {
                        Text(att.name ?? att.email ?? "Unknown")
                            .font(.subheadline)
                            .fontWeight(.medium)
                        if let email = att.email, att.name != nil {
                            Text(email)
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }

                    Spacer()

                    if let status = att.status {
                        Text(status.capitalized)
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color(.systemGray5))
                            .cornerRadius(4)
                    }
                }
                .padding(8)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(8)
            }
        }
    }

    private func tasksSection(tasks: [TaskItemSummary]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Correlated Tasks (\(tasks.count))", systemImage: "checkmark.circle.fill")
                .font(.footnote)
                .fontWeight(.bold)
                .foregroundColor(.indigo)
                .textCase(.uppercase)

            ForEach(tasks) { task in
                HStack {
                    Image(systemName: "circle")
                        .foregroundColor(.indigo)
                        .font(.caption)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.title)
                            .font(.subheadline)
                            .fontWeight(.medium)
                        if let due = task.dueAt {
                            Text("Due: \(due)")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }

                    Spacer()

                    if let prio = task.priority {
                        Text(prio.uppercased())
                            .font(.caption2)
                            .foregroundColor(.indigo)
                    }
                }
                .padding(8)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(8)
            }
        }
    }

    private func commitmentsSection(commitments: [CommitmentItemSummary]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Active Commitments (\(commitments.count))", systemImage: "hand.raised.fill")
                .font(.footnote)
                .fontWeight(.bold)
                .foregroundColor(.purple)
                .textCase(.uppercase)

            ForEach(commitments) { cmt in
                HStack {
                    Image(systemName: "flag.fill")
                        .foregroundColor(.purple)
                        .font(.caption)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(cmt.title)
                            .font(.subheadline)
                            .fontWeight(.medium)
                        if let target = cmt.targetDate {
                            Text("Target: \(target)")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }

                    Spacer()

                    Text(cmt.status.capitalized)
                        .font(.caption2)
                        .foregroundColor(.purple)
                }
                .padding(8)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(8)
            }
        }
    }

    private func memoriesSection(memories: [MemoryItemSummary]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Contextual Memories (\(memories.count))", systemImage: "brain.head.profile")
                .font(.footnote)
                .fontWeight(.bold)
                .foregroundColor(.teal)
                .textCase(.uppercase)

            ForEach(memories) { mem in
                VStack(alignment: .leading, spacing: 4) {
                    Text(mem.content)
                        .font(.subheadline)
                    Text(mem.type.capitalized)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(8)
            }
        }
    }
}

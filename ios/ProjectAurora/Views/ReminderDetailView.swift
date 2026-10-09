import SwiftUI

public struct ReminderDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var viewModel: ReminderViewModel
    let reminderId: String

    @State private var reminder: ReminderItem?
    @State private var isEditing: Bool = false
    @State private var editTitle: String = ""
    @State private var editBody: String = ""
    @State private var editScheduledAt: Date = Date()
    @State private var isPerformingAction: Bool = false
    @State private var showDeleteConfirmation: Bool = false

    public init(viewModel: ReminderViewModel, reminderId: String) {
        self.viewModel = viewModel
        self.reminderId = reminderId
    }

    private var currentReminder: ReminderItem? {
        reminder ?? viewModel.reminders.first(where: { $0.id == reminderId })
    }

    public var body: some View {
        Group {
            if let item = currentReminder {
                content(for: item)
            } else {
                ProgressView("Loading reminder...")
            }
        }
        .navigationTitle("Reminder")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                if let item = currentReminder, item.status == .scheduled {
                    Button("Edit") {
                        editTitle = item.title
                        editBody = item.body
                        editScheduledAt = item.scheduledAt
                        isEditing = true
                    }
                }
            }
        }
        .sheet(isPresented: $isEditing) {
            editSheet
        }
        .confirmationDialog(
            "Delete Reminder?",
            isPresented: $showDeleteConfirmation,
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                Task {
                    isPerformingAction = true
                    let success = await viewModel.deleteReminder(id: reminderId)
                    isPerformingAction = false
                    if success {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Are you sure you want to delete this reminder? This action removes it from your active list.")
        }
    }

    private func content(for item: ReminderItem) -> some View {
        List {
            // MARK: - Title & Body
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text(item.title)
                        .font(.title2)
                        .fontWeight(.bold)

                    if !item.body.isEmpty {
                        Text(item.body)
                            .font(.body)
                            .foregroundColor(.secondary)
                    }
                }
                .padding(.vertical, 4)
            }

            // MARK: - Schedule & Status
            Section("Status & Schedule") {
                HStack {
                    Label("Status", systemImage: item.status.systemIcon)
                    Spacer()
                    statusBadge(item.status)
                }

                HStack {
                    Label("Scheduled For", systemImage: "clock")
                    Spacer()
                    Text(item.scheduledAt.formatted(date: .abbreviated, time: .shortened))
                        .foregroundColor(.primary)
                }
            }

            // MARK: - Linked Item
            if item.taskId != nil || item.commitmentId != nil {
                Section("Linked Context") {
                    if let taskId = item.taskId {
                        HStack {
                            Label("Linked Task", systemImage: "checklist")
                            Spacer()
                            Text(taskId)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    if let cmtId = item.commitmentId {
                        HStack {
                            Label("Linked Commitment", systemImage: "hourglass")
                            Spacer()
                            Text(cmtId)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }

            // MARK: - Metadata
            Section("Information") {
                HStack {
                    Text("Created")
                    Spacer()
                    Text(item.createdAt.formatted(date: .abbreviated, time: .shortened))
                        .foregroundColor(.secondary)
                }
                HStack {
                    Text("Updated")
                    Spacer()
                    Text(item.updatedAt.formatted(date: .abbreviated, time: .shortened))
                        .foregroundColor(.secondary)
                }
            }

            // MARK: - Lifecycle Actions
            Section {
                if item.status == .scheduled {
                    Button {
                        Task {
                            isPerformingAction = true
                            _ = await viewModel.cancelReminder(id: item.id)
                            isPerformingAction = false
                        }
                    } label: {
                        HStack {
                            Image(systemName: "bell.slash")
                            Text("Cancel Reminder")
                        }
                        .foregroundColor(.orange)
                    }
                    .disabled(isPerformingAction)
                }

                Button(role: .destructive) {
                    showDeleteConfirmation = true
                } label: {
                    HStack {
                        Image(systemName: "trash")
                        Text("Delete Reminder")
                    }
                }
                .disabled(isPerformingAction)
            }
        }
    }

    private func statusBadge(_ status: ReminderStatus) -> some View {
        let (color, text) = switch status {
        case .scheduled:
            (Color.blue, "Scheduled")
        case .delivered:
            (Color.green, "Delivered")
        case .cancelled:
            (Color.orange, "Cancelled")
        case .failed:
            (Color.red, "Failed")
        }

        return Text(text)
            .font(.caption)
            .fontWeight(.semibold)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(0.15))
            .foregroundColor(color)
            .clipShape(Capsule())
    }

    private var editSheet: some View {
        NavigationStack {
            Form {
                Section("Title") {
                    TextField("Title", text: $editTitle)
                }
                Section("Notes") {
                    TextField("Notes or message", text: $editBody, axis: .vertical)
                        .lineLimit(2...4)
                }
                Section("Scheduled Time") {
                    DatePicker(
                        "Scheduled Time",
                        selection: $editScheduledAt,
                        displayedComponents: [.date, .hourAndMinute]
                    )
                }
            }
            .navigationTitle("Edit Reminder")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { isEditing = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            _ = await viewModel.updateReminder(
                                id: reminderId,
                                title: editTitle.trimmingCharacters(in: .whitespaces),
                                body: editBody.isEmpty ? nil : editBody,
                                scheduledAt: editScheduledAt
                            )
                            isEditing = false
                        }
                    }
                    .disabled(editTitle.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}

import SwiftUI

public struct CreateReminderSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var viewModel: ReminderViewModel

    @State private var title: String = ""
    @State private var bodyText: String = ""
    @State private var scheduledAt: Date = Date().addingTimeInterval(3600)
    @State private var taskId: String = ""
    @State private var commitmentId: String = ""
    @State private var hasLinkedItem: Bool = false
    @State private var linkType: String = "task"
    @State private var isSubmitting: Bool = false

    public init(viewModel: ReminderViewModel) {
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            Form {
                Section("Reminder Details") {
                    TextField("Title", text: $title)
                    TextField("Notes or message (optional)", text: $bodyText, axis: .vertical)
                        .lineLimit(2...4)
                }

                Section("Trigger Time") {
                    DatePicker(
                        "Scheduled Time",
                        selection: $scheduledAt,
                        in: Date()...,
                        displayedComponents: [.date, .hourAndMinute]
                    )
                }

                Section("Link Task or Commitment (Optional)") {
                    Toggle("Link to Existing Item", isOn: $hasLinkedItem)
                    if hasLinkedItem {
                        Picker("Item Type", selection: $linkType) {
                            Text("Task").tag("task")
                            Text("Commitment").tag("commitment")
                        }
                        .pickerStyle(.segmented)

                        if linkType == "task" {
                            TextField("Task ID (e.g. tsk_12345)", text: $taskId)
                        } else {
                            TextField("Commitment ID (e.g. cmt_12345)", text: $commitmentId)
                        }
                    }
                }

                if viewModel.notificationPermissionStatus != .authorized {
                    Section {
                        HStack(spacing: 12) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundColor(.orange)
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Notifications Not Enabled")
                                    .font(.subheadline)
                                    .fontWeight(.medium)
                                Text("The reminder will be saved on the backend, but device delivery requires notification authorization.")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            .navigationTitle("New Reminder")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }

                ToolbarItem(placement: .confirmationAction) {
                    Button("Schedule") {
                        Task {
                            isSubmitting = true
                            let cleanTaskId = hasLinkedItem && linkType == "task" && !taskId.trimmingCharacters(in: .whitespaces).isEmpty
                                ? taskId.trimmingCharacters(in: .whitespaces)
                                : nil
                            let cleanCmtId = hasLinkedItem && linkType == "commitment" && !commitmentId.trimmingCharacters(in: .whitespaces).isEmpty
                                ? commitmentId.trimmingCharacters(in: .whitespaces)
                                : nil

                            let success = await viewModel.createReminder(
                                title: title.trimmingCharacters(in: .whitespaces),
                                scheduledAt: scheduledAt,
                                body: bodyText.isEmpty ? nil : bodyText,
                                taskId: cleanTaskId,
                                commitmentId: cleanCmtId
                            )
                            isSubmitting = false
                            if success {
                                dismiss()
                            }
                        }
                    }
                    .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty || isSubmitting)
                }
            }
        }
    }
}

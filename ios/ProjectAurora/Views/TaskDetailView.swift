import SwiftUI

public struct TaskDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var viewModel: TaskViewModel

    @State private var task: TaskItem
    @State private var isEditing: Bool = false
    @State private var editTitle: String
    @State private var editDescription: String
    @State private var editPriority: TaskPriority
    @State private var hasDueDate: Bool
    @State private var editDueDate: Date
    @State private var editTagsString: String
    @State private var isSaving: Bool = false
    @State private var showDeleteConfirm: Bool = false

    public init(task: TaskItem, viewModel: TaskViewModel) {
        self._task = State(initialValue: task)
        self.viewModel = viewModel
        self._editTitle = State(initialValue: task.title)
        self._editDescription = State(initialValue: task.description)
        self._editPriority = State(initialValue: task.priority)
        self._hasDueDate = State(initialValue: task.dueAt != nil)
        self._editDueDate = State(initialValue: task.dueAt ?? Date())
        self._editTagsString = State(initialValue: task.tags.joined(separator: ", "))
    }

    public var body: some View {
        Form {
            if isEditing {
                editSection
            } else {
                readOnlySection
            }

            if !isEditing {
                lifecycleActionsSection
                metadataSection
            }
        }
        .navigationTitle(isEditing ? "Edit Task" : "Task Details")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if isEditing {
                    Button("Done") {
                        saveChanges()
                    }
                    .disabled(editTitle.trimmingCharacters(in: .whitespaces).isEmpty || isSaving)
                } else {
                    Button("Edit") {
                        isEditing = true
                    }
                }
            }
        }
        .confirmationDialog("Delete Task?", isPresented: $showDeleteConfirm, titleVisibility: .visible) {
            Button("Delete Task", role: .destructive) {
                Task {
                    let success = await viewModel.deleteTask(id: task.id)
                    if success {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Are you sure you want to delete this task? This will soft-delete the item.")
        }
    }

    private var readOnlySection: some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    statusPill(task.status)
                    Spacer()
                    priorityPill(task.priority)
                }

                Text(task.title)
                    .font(.title3.bold())

                if !task.description.isEmpty {
                    Text(task.description)
                        .font(.body)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.vertical, 4)

            if let due = task.dueAt {
                LabeledContent("Due Date") {
                    Text(due, style: .date)
                }
            }

            if let comp = task.completedAt {
                LabeledContent("Completed") {
                    Text(comp, style: .date)
                }
            }

            if !task.tags.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Tags")
                        .font(.caption)
                        .foregroundColor(.secondary)

                    FlowLayout(spacing: 6) {
                        ForEach(task.tags, id: \.self) { tag in
                            Text("#\(tag)")
                                .font(.caption)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Color(.secondarySystemFill))
                                .clipShape(Capsule())
                        }
                    }
                }
                .padding(.vertical, 4)
            }
        }
    }

    private var editSection: some View {
        Group {
            Section("Title & Notes") {
                TextField("Title", text: $editTitle)
                TextField("Description", text: $editDescription, axis: .vertical)
                    .lineLimit(3...6)
            }

            Section("Priority") {
                Picker("Priority", selection: $editPriority) {
                    ForEach(TaskPriority.allCases) { prio in
                        Text(prio.displayName).tag(prio)
                    }
                }
                .pickerStyle(.segmented)
            }

            Section("Due Date") {
                Toggle("Set Due Date", isOn: $hasDueDate)
                if hasDueDate {
                    DatePicker("Due Date", selection: $editDueDate, displayedComponents: [.date, .hourAndMinute])
                }
            }

            Section("Tags") {
                TextField("Tags (comma separated)", text: $editTagsString)
            }
        }
    }

    private var lifecycleActionsSection: some View {
        Section("Lifecycle Actions") {
            if task.status != .completed && task.status != .cancelled {
                Button {
                    Task {
                        let success = await viewModel.completeTask(id: task.id)
                        if success, let updated = viewModel.tasks.first(where: { $0.id == task.id }) {
                            self.task = updated
                        }
                    }
                } label: {
                    Label("Mark as Completed", systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                }

                Button {
                    Task {
                        let success = await viewModel.cancelTask(id: task.id)
                        if success, let updated = viewModel.tasks.first(where: { $0.id == task.id }) {
                            self.task = updated
                        }
                    }
                } label: {
                    Label("Cancel Task", systemImage: "xmark.circle")
                        .foregroundColor(.orange)
                }
            }

            Button(role: .destructive) {
                showDeleteConfirm = true
            } label: {
                Label("Delete Task", systemImage: "trash")
            }
        }
    }

    private var metadataSection: some View {
        Section("Metadata") {
            LabeledContent("Created") {
                Text(task.createdAt, style: .date)
            }
            LabeledContent("Last Updated") {
                Text(task.updatedAt, style: .date)
            }
            LabeledContent("Active Flag") {
                Text(task.isActive ? "Yes" : "No")
            }
        }
    }

    private func statusPill(_ status: TaskStatus) -> some View {
        HStack(spacing: 4) {
            Image(systemName: status.systemIcon)
            Text(status.displayName)
        }
        .font(.caption.bold())
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.blue.opacity(0.15))
        .foregroundColor(.blue)
        .clipShape(Capsule())
    }

    private func priorityPill(_ priority: TaskPriority) -> some View {
        HStack(spacing: 2) {
            Image(systemName: priority.systemIcon)
            Text(priority.displayName)
        }
        .font(.caption.bold())
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.orange.opacity(0.15))
        .foregroundColor(.orange)
        .clipShape(Capsule())
    }

    private func saveChanges() {
        Task {
            isSaving = true
            let tags = editTagsString
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }

            let success = await viewModel.updateTask(
                id: task.id,
                title: editTitle,
                description: editDescription.isEmpty ? nil : editDescription,
                priority: editPriority,
                dueAt: hasDueDate ? editDueDate : nil,
                tags: tags
            )
            isSaving = false
            if success {
                isEditing = false
                if let updated = viewModel.tasks.first(where: { $0.id == task.id }) {
                    self.task = updated
                }
            }
        }
    }
}

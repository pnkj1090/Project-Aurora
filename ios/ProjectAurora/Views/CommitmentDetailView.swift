import SwiftUI

public struct CommitmentDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var viewModel: CommitmentViewModel

    @State private var commitment: CommitmentItem
    @State private var isEditing: Bool = false
    @State private var editTitle: String
    @State private var editDescription: String
    @State private var editPriority: CommitmentPriority
    @State private var hasTargetDate: Bool
    @State private var editTargetDate: Date
    @State private var editTagsString: String
    @State private var isSaving: Bool = false
    @State private var showDeleteConfirm: Bool = false

    public init(commitment: CommitmentItem, viewModel: CommitmentViewModel) {
        self._commitment = State(initialValue: commitment)
        self.viewModel = viewModel
        self._editTitle = State(initialValue: commitment.title)
        self._editDescription = State(initialValue: commitment.description)
        self._editPriority = State(initialValue: commitment.priority)
        self._hasTargetDate = State(initialValue: commitment.targetDate != nil)
        self._editTargetDate = State(initialValue: commitment.targetDate ?? Date())
        self._editTagsString = State(initialValue: commitment.tags.joined(separator: ", "))
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
        .navigationTitle(isEditing ? "Edit Commitment" : "Commitment")
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
        .confirmationDialog("Delete Commitment?", isPresented: $showDeleteConfirm, titleVisibility: .visible) {
            Button("Delete Commitment", role: .destructive) {
                Task {
                    let success = await viewModel.deleteCommitment(id: commitment.id)
                    if success {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Are you sure you want to delete this commitment? This will soft-delete the item.")
        }
    }

    private var readOnlySection: some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    statusPill(commitment.status)
                    Spacer()
                    priorityPill(commitment.priority)
                }

                Text(commitment.title)
                    .font(.title3.bold())

                if !commitment.description.isEmpty {
                    Text(commitment.description)
                        .font(.body)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.vertical, 4)

            if let target = commitment.targetDate {
                LabeledContent("Target Date") {
                    Text(target, style: .date)
                }
            }

            if let comp = commitment.completedAt {
                LabeledContent("Completed") {
                    Text(comp, style: .date)
                }
            }

            if !commitment.tags.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Tags")
                        .font(.caption)
                        .foregroundColor(.secondary)

                    FlowLayout(spacing: 6) {
                        ForEach(commitment.tags, id: \.self) { tag in
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
            Section("Title & Details") {
                TextField("Title", text: $editTitle)
                TextField("Description", text: $editDescription, axis: .vertical)
                    .lineLimit(3...6)
            }

            Section("Priority") {
                Picker("Priority", selection: $editPriority) {
                    ForEach(CommitmentPriority.allCases) { prio in
                        Text(prio.displayName).tag(prio)
                    }
                }
                .pickerStyle(.segmented)
            }

            Section("Target Date") {
                Toggle("Set Target Date", isOn: $hasTargetDate)
                if hasTargetDate {
                    DatePicker("Target Date", selection: $editTargetDate, displayedComponents: [.date, .hourAndMinute])
                }
            }

            Section("Tags") {
                TextField("Tags (comma separated)", text: $editTagsString)
            }
        }
    }

    private var lifecycleActionsSection: some View {
        Section("Lifecycle Actions") {
            if commitment.status != .completed && commitment.status != .cancelled {
                Button {
                    Task {
                        let success = await viewModel.completeCommitment(id: commitment.id)
                        if success, let updated = viewModel.commitments.first(where: { $0.id == commitment.id }) {
                            self.commitment = updated
                        }
                    }
                } label: {
                    Label("Mark as Completed", systemImage: "checkmark.seal.fill")
                        .foregroundColor(.green)
                }

                Button {
                    Task {
                        let success = await viewModel.cancelCommitment(id: commitment.id)
                        if success, let updated = viewModel.commitments.first(where: { $0.id == commitment.id }) {
                            self.commitment = updated
                        }
                    }
                } label: {
                    Label("Cancel Commitment", systemImage: "xmark.seal")
                        .foregroundColor(.orange)
                }
            }

            Button(role: .destructive) {
                showDeleteConfirm = true
            } label: {
                Label("Delete Commitment", systemImage: "trash")
            }
        }
    }

    private var metadataSection: some View {
        Section("Metadata") {
            LabeledContent("Created") {
                Text(commitment.createdAt, style: .date)
            }
            LabeledContent("Last Updated") {
                Text(commitment.updatedAt, style: .date)
            }
            LabeledContent("Active Flag") {
                Text(commitment.isActive ? "Yes" : "No")
            }
        }
    }

    private func statusPill(_ status: CommitmentStatus) -> some View {
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

    private func priorityPill(_ priority: CommitmentPriority) -> some View {
        HStack(spacing: 2) {
            Image(systemName: priority.systemIcon)
            Text(priority.displayName)
        }
        .font(.caption.bold())
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.purple.opacity(0.15))
        .foregroundColor(.purple)
        .clipShape(Capsule())
    }

    private func saveChanges() {
        Task {
            isSaving = true
            let tags = editTagsString
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }

            let success = await viewModel.updateCommitment(
                id: commitment.id,
                title: editTitle,
                description: editDescription.isEmpty ? nil : editDescription,
                priority: editPriority,
                targetDate: hasTargetDate ? editTargetDate : nil,
                tags: tags
            )
            isSaving = false
            if success {
                isEditing = false
                if let updated = viewModel.commitments.first(where: { $0.id == commitment.id }) {
                    self.commitment = updated
                }
            }
        }
    }
}

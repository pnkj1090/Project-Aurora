import SwiftUI

public struct MemoryDetailView: View {
    @ObservedObject var viewModel: MemoryViewModel
    public let memory: MemoryItem
    @Environment(\.dismiss) private var dismiss

    @State private var isEditing: Bool = false
    @State private var editedContent: String = ""
    @State private var editedImportance: Int = 3
    @State private var editedTagsText: String = ""
    @State private var showingDeleteConfirmation: Bool = false
    @State private var isSaving: Bool = false

    public init(viewModel: MemoryViewModel, memory: MemoryItem) {
        self.viewModel = viewModel
        self.memory = memory
        _editedContent = State(initialValue: memory.content)
        _editedImportance = State(initialValue: memory.importance)
        _editedTagsText = State(initialValue: memory.tags.joined(separator: ", "))
    }

    public var body: some View {
        List {
            // MARK: - Content Section
            Section("Memory Text") {
                if isEditing {
                    TextEditor(text: $editedContent)
                        .frame(minHeight: 100)
                } else {
                    Text(memory.content)
                        .font(.body)
                        .textSelection(.enabled)
                }
            }

            // MARK: - Metadata Section
            Section("Classification & Metadata") {
                HStack {
                    Text("Type")
                    Spacer()
                    Label(memory.type.displayName, systemImage: memory.type.systemIcon)
                        .font(.subheadline.bold())
                        .foregroundColor(.accentColor)
                }

                if isEditing {
                    Stepper(value: $editedImportance, in: 1...5) {
                        HStack {
                            Text("Importance")
                            Spacer()
                            importanceStars(level: editedImportance)
                        }
                    }
                } else {
                    HStack {
                        Text("Importance")
                        Spacer()
                        importanceStars(level: memory.importance)
                    }
                }

                HStack {
                    Text("Source")
                    Spacer()
                    Text(memory.source.displayName)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }

                HStack {
                    Text("Status")
                    Spacer()
                    Text(memory.status.displayName)
                        .font(.subheadline)
                        .foregroundColor(memory.status == .active ? .green : .secondary)
                }
            }

            // MARK: - Tags Section
            Section("Tags") {
                if isEditing {
                    TextField("Comma-separated tags", text: $editedTagsText)
                } else if !memory.tags.isEmpty {
                    FlowLayout(spacing: 6) {
                        ForEach(memory.tags, id: \.self) { tag in
                            Text("#\(tag)")
                                .font(.caption.bold())
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Color(.secondarySystemBackground))
                                .cornerRadius(8)
                        }
                    }
                } else {
                    Text("No tags")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
            }

            // MARK: - Dates Section
            Section("Timestamps") {
                HStack {
                    Text("Created")
                    Spacer()
                    Text(memory.formattedCreatedDate)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                if let exp = memory.expiresAt {
                    HStack {
                        Text("Expires")
                        Spacer()
                        Text(formatDate(exp))
                            .font(.caption)
                            .foregroundColor(.orange)
                    }
                }
            }

            // MARK: - Danger Zone
            Section {
                Button(role: .destructive) {
                    showingDeleteConfirmation = true
                } label: {
                    HStack {
                        Spacer()
                        Label("Forget this Memory", systemImage: "trash")
                            .foregroundColor(.red)
                        Spacer()
                    }
                }
            }
        }
        .navigationTitle(isEditing ? "Edit Memory" : "Memory Details")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if isEditing {
                    Button("Done") {
                        Task {
                            await saveEdits()
                        }
                    }
                    .disabled(isSaving || editedContent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } else {
                    Button("Edit") {
                        isEditing = true
                    }
                }
            }
        }
        .confirmationDialog(
            "Forget Memory?",
            isPresented: $showingDeleteConfirmation,
            titleVisibility: .visible
        ) {
            Button("Forget Memory", role: .destructive) {
                Task {
                    let success = await viewModel.deleteMemory(id: memory.id)
                    if success {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This will safely archive the memory. Aurora will no longer use it in conversation context.")
        }
    }

    private func importanceStars(level: Int) -> some View {
        HStack(spacing: 2) {
            ForEach(1...5, id: \.self) { i in
                Image(systemName: i <= level ? "star.fill" : "star")
                    .foregroundColor(i <= level ? .yellow : .secondary)
                    .font(.caption)
            }
        }
    }

    private func formatDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    private func saveEdits() async {
        isSaving = true
        let tags = editedTagsText
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        let success = await viewModel.updateMemory(
            id: memory.id,
            content: editedContent,
            importance: editedImportance,
            tags: tags
        )

        isSaving = false
        if success {
            isEditing = false
        }
    }
}

// MARK: - FlowLayout Helper for Tag Chips

public struct FlowLayout: Layout {
    public var spacing: CGFloat = 6

    public init(spacing: CGFloat = 6) {
        self.spacing = spacing
    }

    public func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? 300
        var height: CGFloat = 0
        var currentX: CGFloat = 0
        var currentY: CGFloat = 0
        var lineHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if currentX + size.width > maxWidth && currentX > 0 {
                currentX = 0
                currentY += lineHeight + spacing
                lineHeight = 0
            }
            currentX += size.width + spacing
            lineHeight = max(lineHeight, size.height)
            height = currentY + lineHeight
        }

        return CGSize(width: maxWidth, height: height)
    }

    public func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var currentX = bounds.minX
        var currentY = bounds.minY
        var lineHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if currentX + size.width > bounds.maxX && currentX > bounds.minX {
                currentX = bounds.minX
                currentY += lineHeight + spacing
                lineHeight = 0
            }
            subview.place(at: CGPoint(x: currentX, y: currentY), proposal: ProposedViewSize(size))
            currentX += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }
}

import SwiftUI

public struct MemoryCandidateReviewSheet: View {
    @ObservedObject var viewModel: MemoryViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var editingCandidateId: String? = nil
    @State private var editedContent: String = ""
    @State private var editedTagsText: String = ""
    @State private var isProcessingId: String? = nil

    public init(viewModel: MemoryViewModel) {
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            Group {
                if viewModel.pendingCandidates.isEmpty {
                    emptySuggestionsView
                } else {
                    candidateListView
                }
            }
            .navigationTitle("Memory Suggestions")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }

    // MARK: - Subviews

    private var emptySuggestionsView: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 56))
                .foregroundColor(.green)
            Text("All Caught Up")
                .font(.title2.bold())
            Text("There are no new memory suggestions pending your review.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Spacer()
        }
    }

    private var candidateListView: some View {
        List {
            Section {
                Text("Aurora identified potential memories from recent conversations. Review and approve which items to retain.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            ForEach(viewModel.pendingCandidates) { candidate in
                candidateCard(candidate)
                    .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
                    .listRowSeparator(.hidden)
            }
        }
        .listStyle(.plain)
    }

    private func candidateCard(_ candidate: CandidateReviewItem) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            // Header Row: Type, Confidence, Sensitivity
            HStack(spacing: 6) {
                Text(candidate.memoryType.capitalized)
                    .font(.caption.bold())
                    .foregroundColor(.accentColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.accentColor.opacity(0.12))
                    .cornerRadius(6)

                if candidate.confidence > 0.0 {
                    Text("\(Int(candidate.confidence * 100))% confidence")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }

                Spacer()

                if candidate.sensitivity.lowercased() == "sensitive" {
                    HStack(spacing: 3) {
                        Image(systemName: "exclamationmark.shield.fill")
                        Text("Sensitive")
                    }
                    .font(.caption2.bold())
                    .foregroundColor(.orange)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.orange.opacity(0.15))
                    .cornerRadius(4)
                }
            }

            // Main Content Box or Edit Mode
            if editingCandidateId == candidate.id {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Edit Memory Content")
                        .font(.caption.bold())
                        .foregroundColor(.secondary)

                    TextField("Memory content", text: $editedContent, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(3...6)

                    Text("Tags (comma separated)")
                        .font(.caption.bold())
                        .foregroundColor(.secondary)

                    TextField("e.g. food, dietary, favorite", text: $editedTagsText)
                        .textFieldStyle(.roundedBorder)

                    HStack(spacing: 12) {
                        Button("Cancel") {
                            cancelEditing()
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)

                        Spacer()

                        Button("Save & Approve") {
                            saveAndApprove(candidate)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                        .disabled(editedContent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isProcessingId != nil)
                    }
                    .padding(.top, 4)
                }
                .padding(10)
                .background(Color(.secondarySystemBackground))
                .cornerRadius(8)
            } else {
                Text(candidate.content)
                    .font(.body)
                    .foregroundColor(.primary)

                // Tags
                if !candidate.tags.isEmpty {
                    HStack(spacing: 4) {
                        ForEach(candidate.tags, id: \.self) { tag in
                            Text("#\(tag)")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 2)
                                .background(Color(.tertiarySystemFill))
                                .cornerRadius(4)
                        }
                    }
                }

                // Temporal Indication
                if let expiresAt = candidate.expiresAt {
                    HStack(spacing: 4) {
                        Image(systemName: "clock")
                            .font(.caption2)
                        Text("Expires: \(expiresAt.prefix(10))")
                            .font(.caption2)
                    }
                    .foregroundColor(.secondary)
                }
            }

            // Conflict Warning and Dual Resolution
            if candidate.hasConflict, let conflict = candidate.conflictTarget {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 4) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundColor(.orange)
                        Text("Conflicts with existing memory:")
                            .font(.caption.bold())
                            .foregroundColor(.orange)
                    }

                    Text(conflict.existingContent)
                        .font(.caption)
                        .foregroundColor(.primary)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(.tertiarySystemBackground))
                        .cornerRadius(6)

                    HStack(spacing: 8) {
                        Button {
                            resolveConflict(candidate: candidate, action: "replace")
                        } label: {
                            Text("Replace Existing")
                                .font(.caption.bold())
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                        .disabled(isProcessingId != nil)

                        Button {
                            resolveConflict(candidate: candidate, action: "keep_both")
                        } label: {
                            Text("Keep Both")
                                .font(.caption.bold())
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .disabled(isProcessingId != nil)

                        Spacer()

                        Button(role: .destructive) {
                            rejectCandidate(candidate)
                        } label: {
                            Text("Dismiss")
                                .font(.caption)
                        }
                        .buttonStyle(.borderless)
                        .disabled(isProcessingId != nil)
                    }
                    .padding(.top, 4)
                }
                .padding(10)
                .background(Color.orange.opacity(0.08))
                .cornerRadius(8)
            } else if editingCandidateId != candidate.id {
                // Normal Action Buttons (Non-conflict, Not editing)
                HStack(spacing: 12) {
                    Button {
                        approveCandidate(candidate)
                    } label: {
                        Label("Approve", systemImage: "checkmark")
                            .font(.caption.bold())
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(isProcessingId != nil)

                    Button {
                        startEditing(candidate)
                    } label: {
                        Label("Edit & Approve", systemImage: "pencil")
                            .font(.caption)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(isProcessingId != nil)

                    Spacer()

                    Button(role: .destructive) {
                        rejectCandidate(candidate)
                    } label: {
                        Text("Dismiss")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .disabled(isProcessingId != nil)
                }
                .padding(.top, 4)
            }
        }
        .padding(14)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.04), radius: 2, x: 0, y: 1)
    }

    // MARK: - Actions

    private func startEditing(_ candidate: CandidateReviewItem) {
        editingCandidateId = candidate.id
        editedContent = candidate.content
        editedTagsText = candidate.tags.joined(separator: ", ")
    }

    private func cancelEditing() {
        editingCandidateId = nil
        editedContent = ""
        editedTagsText = ""
    }

    private func saveAndApprove(_ candidate: CandidateReviewItem) {
        let trimmed = editedContent.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let tags = editedTagsText
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        isProcessingId = candidate.id
        Task {
            _ = await viewModel.approveCandidate(
                id: candidate.id,
                editedContent: trimmed != candidate.content ? trimmed : nil,
                overrideTags: !tags.isEmpty ? tags : nil,
                conflictAction: candidate.hasConflict ? "replace" : nil
            )
            isProcessingId = nil
            cancelEditing()
        }
    }

    private func approveCandidate(_ candidate: CandidateReviewItem) {
        isProcessingId = candidate.id
        Task {
            _ = await viewModel.approveCandidate(id: candidate.id)
            isProcessingId = nil
        }
    }

    private func resolveConflict(candidate: CandidateReviewItem, action: String) {
        isProcessingId = candidate.id
        Task {
            _ = await viewModel.approveCandidate(id: candidate.id, conflictAction: action)
            isProcessingId = nil
        }
    }

    private func rejectCandidate(_ candidate: CandidateReviewItem) {
        isProcessingId = candidate.id
        Task {
            _ = await viewModel.rejectCandidate(id: candidate.id)
            isProcessingId = nil
        }
    }
}

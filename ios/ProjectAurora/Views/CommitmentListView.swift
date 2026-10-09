import SwiftUI

public struct CommitmentListView: View {
    @ObservedObject private var viewModel: CommitmentViewModel
    @State private var showingCreateSheet: Bool = false
    @State private var commitmentPendingDeletion: CommitmentItem? = nil

    @MainActor
    public init(viewModel: CommitmentViewModel? = nil) {
        self.viewModel = viewModel ?? CommitmentViewModel()
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // MARK: - Status Filter Chips
                statusFilterBar
                    .padding(.vertical, 8)
                    .background(Color(.systemGroupedBackground))

                Divider()

                // MARK: - Content
                if viewModel.isLoading && viewModel.commitments.isEmpty {
                    loadingView
                } else if viewModel.commitments.isEmpty {
                    emptyStateView
                } else {
                    commitmentList
                }
            }
            .navigationTitle("Commitments")
            .searchable(
                text: $viewModel.searchQuery,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Search commitments or tags"
            )
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showingCreateSheet = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("Add Commitment")
                }
            }
            .sheet(isPresented: $showingCreateSheet) {
                CreateCommitmentSheet(viewModel: viewModel)
            }
            .confirmationDialog(
                "Delete this commitment?",
                isPresented: Binding(
                    get: { commitmentPendingDeletion != nil },
                    set: { if !$0 { commitmentPendingDeletion = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Delete Commitment", role: .destructive) {
                    if let cmt = commitmentPendingDeletion {
                        Task {
                            _ = await viewModel.deleteCommitment(id: cmt.id)
                            commitmentPendingDeletion = nil
                        }
                    }
                }
                Button("Cancel", role: .cancel) {
                    commitmentPendingDeletion = nil
                }
            } message: {
                if let cmt = commitmentPendingDeletion {
                    Text("Are you sure you want to delete \"\(cmt.title)\"? This will soft-delete the commitment.")
                }
            }
            .task {
                await viewModel.loadCommitments()
            }
            .refreshable {
                await viewModel.loadCommitments()
            }
        }
    }

    // MARK: - Subviews

    private var statusFilterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                filterChip(
                    title: "All",
                    icon: "tray.fill",
                    isSelected: viewModel.selectedStatus == nil
                ) {
                    viewModel.setStatusFilter(nil)
                }

                ForEach(CommitmentStatus.allCases) { status in
                    filterChip(
                        title: status.displayName,
                        icon: status.systemIcon,
                        isSelected: viewModel.selectedStatus == status
                    ) {
                        viewModel.setStatusFilter(status)
                    }
                }
            }
            .padding(.horizontal)
        }
    }

    private func filterChip(title: String, icon: String? = nil, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 5) {
                if let icon = icon {
                    Image(systemName: icon)
                        .font(.caption2)
                }
                Text(title)
                    .font(.subheadline.bold())
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(isSelected ? Color.accentColor : Color(.secondarySystemBackground))
            .foregroundColor(isSelected ? .white : .primary)
            .cornerRadius(16)
        }
        .buttonStyle(.plain)
    }

    private var commitmentList: some View {
        List {
            ForEach(viewModel.commitments) { cmt in
                NavigationLink(destination: CommitmentDetailView(commitment: cmt, viewModel: viewModel)) {
                    commitmentRow(for: cmt)
                }
                .swipeActions(edge: .leading) {
                    if cmt.status != .completed && cmt.status != .cancelled {
                        Button {
                            Task { _ = await viewModel.completeCommitment(id: cmt.id) }
                        } label: {
                            Label("Complete", systemImage: "checkmark.seal.fill")
                        }
                        .tint(.green)
                    }
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button(role: .destructive) {
                        commitmentPendingDeletion = cmt
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }

                    if cmt.status != .cancelled && cmt.status != .completed {
                        Button {
                            Task { _ = await viewModel.cancelCommitment(id: cmt.id) }
                        } label: {
                            Label("Cancel", systemImage: "xmark.seal")
                        }
                        .tint(.orange)
                    }
                }
            }

            if viewModel.commitments.count < viewModel.totalCount {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .task {
                        await viewModel.loadMore()
                    }
            }
        }
        .listStyle(.insetGrouped)
    }

    private func commitmentRow(for cmt: CommitmentItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: cmt.status.systemIcon)
                    .foregroundColor(statusColor(for: cmt.status))

                Text(cmt.title)
                    .font(.body.weight(.medium))
                    .strikethrough(cmt.status == .completed || cmt.status == .cancelled, color: .secondary)

                Spacer()

                priorityBadge(cmt.priority)
            }

            if !cmt.description.isEmpty {
                Text(cmt.description)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 8) {
                if let target = cmt.targetDate {
                    HStack(spacing: 4) {
                        Image(systemName: "clock")
                        Text(target, style: .date)
                    }
                    .font(.caption2)
                    .foregroundColor(.secondary)
                }

                ForEach(cmt.tags.prefix(3), id: \.self) { tag in
                    Text("#\(tag)")
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color(.tertiarySystemFill))
                        .clipShape(Capsule())
                }
            }
        }
        .padding(.vertical, 4)
    }

    private func priorityBadge(_ priority: CommitmentPriority) -> some View {
        HStack(spacing: 2) {
            Image(systemName: priority.systemIcon)
            Text(priority.displayName)
        }
        .font(.caption2.bold())
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(priorityColor(priority).opacity(0.15))
        .foregroundColor(priorityColor(priority))
        .clipShape(Capsule())
    }

    private func statusColor(for status: CommitmentStatus) -> Color {
        switch status {
        case .active: return .blue
        case .completed: return .green
        case .cancelled: return .secondary
        }
    }

    private func priorityColor(_ priority: CommitmentPriority) -> Color {
        switch priority {
        case .low: return .gray
        case .medium: return .blue
        case .high: return .purple
        }
    }

    private var emptyStateView: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "hourglass")
                .font(.system(size: 48))
                .foregroundColor(.secondary)

            Text("No commitments found")
                .font(.headline)

            Text("Track promises, agreements, and long-term intentions.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button {
                showingCreateSheet = true
            } label: {
                Label("Create Commitment", systemImage: "plus")
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)

            Spacer()
        }
    }

    private var loadingView: some View {
        VStack(spacing: 12) {
            Spacer()
            ProgressView()
            Text("Loading commitments...")
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
        }
    }
}

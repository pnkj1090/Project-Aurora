import SwiftUI

public struct MemoryListView: View {
    @StateObject private var viewModel = MemoryViewModel()
    @State private var showingCreateSheet: Bool = false
    @State private var showingReviewSheet: Bool = false
    @State private var memoryPendingDeletion: MemoryItem? = nil

    public init() {}

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // MARK: - Memory Type Filter Chips
                typeFilterBar
                    .padding(.vertical, 8)
                    .background(Color(.systemGroupedBackground))

                Divider()

                // MARK: - Pending Review Banner
                if viewModel.pendingCandidatesCount > 0 {
                    pendingReviewBanner
                }

                // MARK: - Memory List Content
                if viewModel.isLoading && viewModel.memories.isEmpty {
                    loadingView
                } else if viewModel.memories.isEmpty {
                    emptyStateView
                } else {
                    memoryList
                }
            }
            .navigationTitle("Memories")
            .searchable(
                text: $viewModel.searchQuery,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Search memories or tags"
            )
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    HStack(spacing: 12) {
                        if viewModel.pendingCandidatesCount > 0 {
                            Button {
                                showingReviewSheet = true
                            } label: {
                                ZStack(alignment: .topTrailing) {
                                    Image(systemName: "sparkles")
                                    Text("\(viewModel.pendingCandidatesCount)")
                                        .font(.system(size: 9, weight: .bold))
                                        .foregroundColor(.white)
                                        .padding(3)
                                        .background(Color.accentColor)
                                        .clipShape(Circle())
                                        .offset(x: 8, y: -8)
                                }
                            }
                            .accessibilityLabel("Pending Memory Suggestions: \(viewModel.pendingCandidatesCount)")
                        }

                        Button {
                            showingCreateSheet = true
                        } label: {
                            Image(systemName: "plus")
                        }
                        .accessibilityLabel("Add Memory")
                    }
                }
            }
            .sheet(isPresented: $showingCreateSheet) {
                CreateMemorySheet(viewModel: viewModel)
            }
            .sheet(isPresented: $showingReviewSheet) {
                MemoryCandidateReviewSheet(viewModel: viewModel)
            }
            .confirmationDialog(
                "Forget this memory?",
                isPresented: Binding(
                    get: { memoryPendingDeletion != nil },
                    set: { if !$0 { memoryPendingDeletion = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Forget Memory", role: .destructive) {
                    if let mem = memoryPendingDeletion {
                        Task {
                            await viewModel.deleteMemory(id: mem.id)
                            memoryPendingDeletion = nil
                        }
                    }
                }
                Button("Cancel", role: .cancel) {
                    memoryPendingDeletion = nil
                }
            } message: {
                Text("This memory will be archived and removed from active context.")
            }
            .task {
                await viewModel.loadMemories()
            }
            .refreshable {
                await viewModel.loadMemories()
            }
            .alert("Error", isPresented: Binding(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.dismissError() } }
            )) {
                Button("OK") { viewModel.dismissError() }
            } message: {
                if let err = viewModel.errorMessage {
                    Text(err)
                }
            }
        }
    }

    // MARK: - Subviews

    private var typeFilterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                filterChip(title: "All", isSelected: viewModel.selectedType == nil) {
                    viewModel.selectType(nil)
                }

                ForEach(MemoryType.allCases) { type in
                    filterChip(
                        title: type.displayName,
                        icon: type.systemIcon,
                        isSelected: viewModel.selectedType == type
                    ) {
                        viewModel.selectType(type)
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

    private var memoryList: some View {
        List {
            ForEach(viewModel.memories) { memory in
                NavigationLink(destination: MemoryDetailView(viewModel: viewModel, memory: memory)) {
                    memoryRow(memory: memory)
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button(role: .destructive) {
                        memoryPendingDeletion = memory
                    } label: {
                        Label("Forget", systemImage: "trash")
                    }
                }
                .onAppear {
                    // Trigger pagination when nearing end of current batch
                    if memory.id == viewModel.memories.last?.id {
                        Task {
                            await viewModel.loadMore()
                        }
                    }
                }
            }

            if viewModel.isLoadingMore {
                HStack {
                    Spacer()
                    ProgressView("Loading more...")
                        .controlSize(.small)
                    Spacer()
                }
                .listRowSeparator(.hidden)
            }
        }
        .listStyle(.insetGrouped)
    }

    private func memoryRow(memory: MemoryItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label(memory.type.displayName, systemImage: memory.type.systemIcon)
                    .font(.caption.bold())
                    .foregroundColor(.accentColor)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.accentColor.opacity(0.12))
                    .cornerRadius(6)

                Spacer()

                HStack(spacing: 2) {
                    ForEach(1...5, id: \.self) { i in
                        Image(systemName: i <= memory.importance ? "star.fill" : "star")
                            .font(.system(size: 9))
                            .foregroundColor(i <= memory.importance ? .yellow : .secondary)
                    }
                }

                Text(memory.relativeCreatedDate)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            Text(memory.content)
                .font(.body)
                .foregroundColor(.primary)
                .lineLimit(2)

            if !memory.tags.isEmpty {
                HStack(spacing: 4) {
                    ForEach(memory.tags.prefix(3), id: \.self) { tag in
                        Text("#\(tag)")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                    if memory.tags.count > 3 {
                        Text("+\(memory.tags.count - 3)")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var emptyStateView: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "brain.head.profile")
                .font(.system(size: 54))
                .foregroundColor(.secondary)
            Text(viewModel.searchQuery.isEmpty ? "No memories saved yet" : "No memories match '\(viewModel.searchQuery)'")
                .font(.headline)
                .foregroundColor(.primary)
            Text(viewModel.searchQuery.isEmpty ? "Aurora automatically retains preferences and facts you ask it to remember, or you can add them manually." : "Try clearing your search query or selecting a different type.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            if viewModel.searchQuery.isEmpty {
                Button {
                    showingCreateSheet = true
                } label: {
                    Label("Add First Memory", systemImage: "plus")
                        .font(.headline)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 10)
                        .background(Color.accentColor)
                        .foregroundColor(.white)
                        .cornerRadius(12)
                }
                .padding(.top, 8)
            }
            Spacer()
        }
    }

    private var loadingView: some View {
        VStack(spacing: 12) {
            Spacer()
            ProgressView()
                .controlSize(.large)
            Text("Loading memories...")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Spacer()
        }
    }

    private var pendingReviewBanner: some View {
        Button {
            showingReviewSheet = true
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "sparkles")
                    .foregroundColor(.accentColor)
                    .font(.headline)

                VStack(alignment: .leading, spacing: 2) {
                    Text("\(viewModel.pendingCandidatesCount) Memory Suggestion\(viewModel.pendingCandidatesCount > 1 ? "s" : "")")
                        .font(.subheadline.bold())
                        .foregroundColor(.primary)
                    Text("Tap to review memories identified from conversations")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Spacer()

                Image(systemName: "chevron.right")
                    .font(.caption.bold())
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color.accentColor.opacity(0.1))
            .cornerRadius(10)
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
        .buttonStyle(.plain)
    }
}


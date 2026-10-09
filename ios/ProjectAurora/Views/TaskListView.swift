import SwiftUI

public struct TaskListView: View {
    @ObservedObject private var viewModel: TaskViewModel
    @State private var showingCreateSheet: Bool = false
    @State private var taskPendingDeletion: TaskItem? = nil

    @MainActor
    public init(viewModel: TaskViewModel? = nil) {
        self.viewModel = viewModel ?? TaskViewModel()
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
                if viewModel.isLoading && viewModel.tasks.isEmpty {
                    loadingView
                } else if viewModel.tasks.isEmpty {
                    emptyStateView
                } else {
                    taskList
                }
            }
            .navigationTitle("Tasks")
            .searchable(
                text: $viewModel.searchQuery,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Search tasks or tags"
            )
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showingCreateSheet = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("Add Task")
                }
            }
            .sheet(isPresented: $showingCreateSheet) {
                CreateTaskSheet(viewModel: viewModel)
            }
            .confirmationDialog(
                "Delete this task?",
                isPresented: Binding(
                    get: { taskPendingDeletion != nil },
                    set: { if !$0 { taskPendingDeletion = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Delete Task", role: .destructive) {
                    if let task = taskPendingDeletion {
                        Task {
                            _ = await viewModel.deleteTask(id: task.id)
                            taskPendingDeletion = nil
                        }
                    }
                }
                Button("Cancel", role: .cancel) {
                    taskPendingDeletion = nil
                }
            } message: {
                if let task = taskPendingDeletion {
                    Text("Are you sure you want to delete \"\(task.title)\"? This will soft-delete the task.")
                }
            }
            .task {
                await viewModel.loadTasks()
            }
            .refreshable {
                await viewModel.loadTasks()
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

                ForEach(TaskStatus.allCases) { status in
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

    private var taskList: some View {
        List {
            ForEach(viewModel.tasks) { task in
                NavigationLink(destination: TaskDetailView(task: task, viewModel: viewModel)) {
                    taskRow(for: task)
                }
                .swipeActions(edge: .leading) {
                    if task.status == .pending {
                        Button {
                            Task { _ = await viewModel.startTask(id: task.id) }
                        } label: {
                            Label("Start", systemImage: "play.fill")
                        }
                        .tint(.orange)
                    }
                    if task.status != .completed && task.status != .cancelled {
                        Button {
                            Task { _ = await viewModel.completeTask(id: task.id) }
                        } label: {
                            Label("Complete", systemImage: "checkmark.circle.fill")
                        }
                        .tint(.green)
                    }
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button(role: .destructive) {
                        taskPendingDeletion = task
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }

                    if task.status != .cancelled && task.status != .completed {
                        Button {
                            Task { _ = await viewModel.cancelTask(id: task.id) }
                        } label: {
                            Label("Cancel", systemImage: "xmark.circle")
                        }
                        .tint(.orange)
                    }
                }
            }

            if viewModel.tasks.count < viewModel.totalCount {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .task {
                        await viewModel.loadMore()
                    }
            }
        }
        .listStyle(.insetGrouped)
    }

    private func taskRow(for task: TaskItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: task.status.systemIcon)
                    .foregroundColor(statusColor(for: task.status))

                Text(task.title)
                    .font(.body.weight(.medium))
                    .strikethrough(task.status == .completed || task.status == .cancelled, color: .secondary)

                Spacer()

                priorityBadge(task.priority)
            }

            if !task.description.isEmpty {
                Text(task.description)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 8) {
                if let due = task.dueAt {
                    HStack(spacing: 4) {
                        Image(systemName: "calendar")
                        Text(due, style: .date)
                    }
                    .font(.caption2)
                    .foregroundColor(.secondary)
                }

                ForEach(task.tags.prefix(3), id: \.self) { tag in
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

    private func priorityBadge(_ priority: TaskPriority) -> some View {
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

    private func statusColor(for status: TaskStatus) -> Color {
        switch status {
        case .pending: return .blue
        case .inProgress: return .orange
        case .completed: return .green
        case .cancelled: return .secondary
        }
    }

    private func priorityColor(_ priority: TaskPriority) -> Color {
        switch priority {
        case .low: return .gray
        case .medium: return .blue
        case .high: return .red
        }
    }

    private var emptyStateView: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "checklist")
                .font(.system(size: 48))
                .foregroundColor(.secondary)

            Text("No tasks found")
                .font(.headline)

            Text("Add tasks to track your actionable work units.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button {
                showingCreateSheet = true
            } label: {
                Label("Create Task", systemImage: "plus")
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
            Text("Loading tasks...")
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
        }
    }
}

import Foundation
import Combine

@MainActor
public final class TaskViewModel: ObservableObject {
    @Published public var tasks: [TaskItem] = []
    @Published public var totalCount: Int = 0
    @Published public var selectedStatus: TaskStatus? = nil
    @Published public var selectedPriority: TaskPriority? = nil
    @Published public var searchQuery: String = "" {
        didSet {
            scheduleDebouncedSearch()
        }
    }
    @Published public var isLoading: Bool = false
    @Published public var isLoadingMore: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiClient: AuroraAPIClient
    private var searchTask: Task<Void, Never>? = nil
    private let pageSize: Int = 30
    private var currentOffset: Int = 0

    public init(apiClient: AuroraAPIClient = AuroraAPIClient()) {
        self.apiClient = apiClient
    }

    /// Load the initial page of tasks matching current filters and query
    public func loadTasks() async {
        isLoading = true
        errorMessage = nil
        currentOffset = 0

        do {
            let response = try await apiClient.fetchTasks(
                status: selectedStatus,
                priority: selectedPriority,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: 0,
                includeInactive: false
            )
            self.tasks = response.items.map { incoming in
                guard let existing = self.tasks.first(where: { $0.id == incoming.id }),
                      existing.updatedAt >= incoming.updatedAt else { return incoming }
                return existing
            }
            self.totalCount = response.total
            self.currentOffset = response.items.count
        } catch {
            self.errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    /// Load subsequent page of tasks (pagination)
    public func loadMore() async {
        guard !isLoading, !isLoadingMore, tasks.count < totalCount else { return }

        isLoadingMore = true
        do {
            let response = try await apiClient.fetchTasks(
                status: selectedStatus,
                priority: selectedPriority,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: currentOffset,
                includeInactive: false
            )
            for item in response.items { applyServerTask(item) }
            self.totalCount = response.total
            self.currentOffset += response.items.count
        } catch {
            self.errorMessage = error.localizedDescription
        }
        isLoadingMore = false
    }

    public func setStatusFilter(_ status: TaskStatus?) {
        guard selectedStatus != status else { return }
        selectedStatus = status
        Task { await loadTasks() }
    }

    public func setPriorityFilter(_ priority: TaskPriority?) {
        guard selectedPriority != priority else { return }
        selectedPriority = priority
        Task { await loadTasks() }
    }

    private func scheduleDebouncedSearch() {
        searchTask?.cancel()
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            await loadTasks()
        }
    }

    /// Create an actionable task
    public func createTask(
        title: String,
        description: String? = nil,
        priority: TaskPriority = .medium,
        dueAt: Date? = nil,
        tags: [String] = []
    ) async -> Bool {
        errorMessage = nil
        let payload = TaskCreateRequest(
            title: title,
            description: description,
            priority: priority,
            dueAt: dueAt,
            tags: tags
        )
        do {
            let created = try await apiClient.createTask(payload: payload)
            self.tasks.insert(created, at: 0)
            self.totalCount += 1
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Update user-editable fields on an existing task
    public func updateTask(
        id: String,
        title: String? = nil,
        description: String? = nil,
        priority: TaskPriority? = nil,
        dueAt: Date? = nil,
        tags: [String]? = nil,
        sourceCommitmentId: String? = nil,
        clearCommitment: Bool = false
    ) async -> Bool {
        errorMessage = nil
        let payload = TaskUpdateRequest(
            title: title,
            description: description,
            priority: priority,
            dueAt: dueAt,
            tags: tags,
            sourceCommitmentId: sourceCommitmentId,
            clearCommitment: clearCommitment
        )
        do {
            let updated = try await apiClient.updateTask(id: id, payload: payload)
            applyServerTask(updated)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    public func startTask(id: String) async -> Bool {
        errorMessage = nil
        do {
            applyServerTask(try await apiClient.startTask(id: id))
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    public func linkTask(id: String, toCommitment commitmentId: String) async -> Bool {
        errorMessage = nil
        do {
            applyServerTask(try await apiClient.linkTask(id: id, toCommitment: commitmentId))
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    public func unlinkTask(id: String) async -> Bool {
        errorMessage = nil
        do {
            applyServerTask(try await apiClient.unlinkTask(id: id))
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    public func applyAssistantAction(_ action: ClientActionPayload) {
        guard let id = action.taskId,
              let updatedAt = Self.parseActionDate(action.updatedAt) else { return }
        if action.action == "delete_task" || action.isActive == false {
            guard let index = tasks.firstIndex(where: { $0.id == id }), updatedAt > tasks[index].updatedAt else { return }
            tasks.remove(at: index)
            totalCount = max(0, totalCount - 1)
            return
        }
        guard let title = action.title,
              let statusRaw = action.status, let status = TaskStatus(rawValue: statusRaw),
              let priorityRaw = action.priority, let priority = TaskPriority(rawValue: priorityRaw) else { return }
        if let index = tasks.firstIndex(where: { $0.id == id }) {
            guard updatedAt > tasks[index].updatedAt else { return }
            var item = tasks[index]
            item.title = title
            item.description = action.description ?? item.description
            item.status = status
            item.priority = priority
            item.updatedAt = updatedAt
            item.dueAt = action.dueAt.flatMap(Self.parseActionDate)
            item.completedAt = action.completedAt.flatMap(Self.parseActionDate)
            item.tags = action.tags ?? item.tags
            item.isActive = action.isActive ?? item.isActive
            item.sourceCommitmentId = action.sourceCommitmentId
            tasks[index] = item
        } else if let userId = action.userId,
                  let createdAt = action.createdAt.flatMap(Self.parseActionDate) {
            tasks.insert(TaskItem(
                id: id, userId: userId, title: title, description: action.description ?? "",
                status: status, priority: priority, createdAt: createdAt, updatedAt: updatedAt,
                dueAt: action.dueAt.flatMap(Self.parseActionDate),
                completedAt: action.completedAt.flatMap(Self.parseActionDate),
                tags: action.tags ?? [], isActive: action.isActive ?? true,
                sourceCommitmentId: action.sourceCommitmentId
            ), at: 0)
            totalCount += 1
        }
    }

    private func applyServerTask(_ incoming: TaskItem) {
        if let index = tasks.firstIndex(where: { $0.id == incoming.id }) {
            guard incoming.updatedAt > tasks[index].updatedAt else { return }
            tasks[index] = incoming
        } else if incoming.isActive {
            tasks.insert(incoming, at: 0)
            totalCount += 1
        }
    }

    private static func parseActionDate(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let standard = ISO8601DateFormatter()
        standard.formatOptions = [.withInternetDateTime]
        return fractional.date(from: value) ?? standard.date(from: value)
    }

    /// Dedicated lifecycle action: Mark task completed
    public func completeTask(id: String) async -> Bool {
        errorMessage = nil
        do {
            let completed = try await apiClient.completeTask(id: id)
            applyServerTask(completed)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Dedicated lifecycle action: Mark task cancelled
    public func cancelTask(id: String) async -> Bool {
        errorMessage = nil
        do {
            let cancelled = try await apiClient.cancelTask(id: id)
            applyServerTask(cancelled)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Dedicated deletion action: Soft delete task
    public func deleteTask(id: String) async -> Bool {
        errorMessage = nil
        do {
            let response = try await apiClient.deleteTask(id: id)
            if response.deleted {
                self.tasks.removeAll { $0.id == id }
                self.totalCount = max(0, self.totalCount - 1)
                return true
            }
            return false
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }
}

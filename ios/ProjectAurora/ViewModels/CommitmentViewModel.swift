import Foundation
import Combine

@MainActor
public final class CommitmentViewModel: ObservableObject {
    @Published public var commitments: [CommitmentItem] = []
    @Published public var totalCount: Int = 0
    @Published public var selectedStatus: CommitmentStatus? = nil
    @Published public var selectedPriority: CommitmentPriority? = nil
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

    /// Load the initial page of commitments matching current filters and query
    public func loadCommitments() async {
        isLoading = true
        errorMessage = nil
        currentOffset = 0

        do {
            let response = try await apiClient.fetchCommitments(
                status: selectedStatus,
                priority: selectedPriority,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: 0,
                includeInactive: false
            )
            self.commitments = response.items.map { incoming in
                guard let existing = self.commitments.first(where: { $0.id == incoming.id }),
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

    /// Load subsequent page of commitments (pagination)
    public func loadMore() async {
        guard !isLoading, !isLoadingMore, commitments.count < totalCount else { return }

        isLoadingMore = true
        do {
            let response = try await apiClient.fetchCommitments(
                status: selectedStatus,
                priority: selectedPriority,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: currentOffset,
                includeInactive: false
            )
            for item in response.items { applyServerCommitment(item) }
            self.totalCount = response.total
            self.currentOffset += response.items.count
        } catch {
            self.errorMessage = error.localizedDescription
        }
        isLoadingMore = false
    }

    public func setStatusFilter(_ status: CommitmentStatus?) {
        guard selectedStatus != status else { return }
        selectedStatus = status
        Task { await loadCommitments() }
    }

    public func setPriorityFilter(_ priority: CommitmentPriority?) {
        guard selectedPriority != priority else { return }
        selectedPriority = priority
        Task { await loadCommitments() }
    }

    private func scheduleDebouncedSearch() {
        searchTask?.cancel()
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            await loadCommitments()
        }
    }

    /// Create a tracked commitment
    public func createCommitment(
        title: String,
        description: String? = nil,
        priority: CommitmentPriority = .medium,
        targetDate: Date? = nil,
        tags: [String] = []
    ) async -> Bool {
        errorMessage = nil
        let payload = CommitmentCreateRequest(
            title: title,
            description: description,
            priority: priority,
            targetDate: targetDate,
            tags: tags
        )
        do {
            let created = try await apiClient.createCommitment(payload: payload)
            self.commitments.insert(created, at: 0)
            self.totalCount += 1
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Update user-editable fields on an existing commitment
    public func updateCommitment(
        id: String,
        title: String? = nil,
        description: String? = nil,
        priority: CommitmentPriority? = nil,
        targetDate: Date? = nil,
        tags: [String]? = nil
    ) async -> Bool {
        errorMessage = nil
        let payload = CommitmentUpdateRequest(
            title: title,
            description: description,
            priority: priority,
            targetDate: targetDate,
            tags: tags
        )
        do {
            let updated = try await apiClient.updateCommitment(id: id, payload: payload)
            applyServerCommitment(updated)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    public func applyAssistantAction(_ action: ClientActionPayload) {
        guard let id = action.commitmentId,
              let updatedAt = Self.parseActionDate(action.updatedAt) else { return }
        if action.action == "delete_commitment" || action.isActive == false {
            guard let index = commitments.firstIndex(where: { $0.id == id }), updatedAt > commitments[index].updatedAt else { return }
            commitments.remove(at: index)
            totalCount = max(0, totalCount - 1)
            return
        }
        guard let title = action.title,
              let statusRaw = action.status, let status = CommitmentStatus(rawValue: statusRaw),
              let priorityRaw = action.priority, let priority = CommitmentPriority(rawValue: priorityRaw) else { return }
        if let index = commitments.firstIndex(where: { $0.id == id }) {
            guard updatedAt > commitments[index].updatedAt else { return }
            var item = commitments[index]
            item.title = title
            item.description = action.description ?? item.description
            item.status = status
            item.priority = priority
            item.updatedAt = updatedAt
            item.targetDate = action.targetDate.flatMap(Self.parseActionDate)
            item.completedAt = action.completedAt.flatMap(Self.parseActionDate)
            item.tags = action.tags ?? item.tags
            item.isActive = action.isActive ?? item.isActive
            commitments[index] = item
        } else if let userId = action.userId,
                  let createdAt = action.createdAt.flatMap(Self.parseActionDate) {
            commitments.insert(CommitmentItem(
                id: id, userId: userId, title: title, description: action.description ?? "",
                status: status, priority: priority, createdAt: createdAt, updatedAt: updatedAt,
                targetDate: action.targetDate.flatMap(Self.parseActionDate),
                completedAt: action.completedAt.flatMap(Self.parseActionDate),
                tags: action.tags ?? [], isActive: action.isActive ?? true
            ), at: 0)
            totalCount += 1
        }
    }

    private func applyServerCommitment(_ incoming: CommitmentItem) {
        if let index = commitments.firstIndex(where: { $0.id == incoming.id }) {
            guard incoming.updatedAt > commitments[index].updatedAt else { return }
            commitments[index] = incoming
        } else if incoming.isActive {
            commitments.insert(incoming, at: 0)
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

    /// Dedicated lifecycle action: Mark commitment completed
    public func completeCommitment(id: String) async -> Bool {
        errorMessage = nil
        do {
            let completed = try await apiClient.completeCommitment(id: id)
            applyServerCommitment(completed)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Dedicated lifecycle action: Mark commitment cancelled
    public func cancelCommitment(id: String) async -> Bool {
        errorMessage = nil
        do {
            let cancelled = try await apiClient.cancelCommitment(id: id)
            applyServerCommitment(cancelled)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Dedicated deletion action: Soft delete commitment
    public func deleteCommitment(id: String) async -> Bool {
        errorMessage = nil
        do {
            let response = try await apiClient.deleteCommitment(id: id)
            if response.deleted {
                self.commitments.removeAll { $0.id == id }
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

import Foundation
import Combine

@MainActor
public final class MemoryViewModel: ObservableObject {
    @Published public var memories: [MemoryItem] = []
    @Published public var totalCount: Int = 0
    @Published public var selectedType: MemoryType? = nil
    @Published public var searchQuery: String = "" {
        didSet {
            scheduleDebouncedSearch()
        }
    }
    @Published public var isLoading: Bool = false
    @Published public var isLoadingMore: Bool = false
    @Published public var errorMessage: String? = nil
    @Published public var pendingCandidates: [CandidateReviewItem] = []
    @Published public var pendingCandidatesCount: Int = 0

    private let apiClient: AuroraAPIClient
    private var searchTask: Task<Void, Never>? = nil
    private let pageSize: Int = 30
    private var currentOffset: Int = 0

    public init(apiClient: AuroraAPIClient = AuroraAPIClient()) {
        self.apiClient = apiClient
    }

    /// Load the initial page of memories based on current filters and search query
    public func loadMemories() async {
        isLoading = true
        errorMessage = nil
        currentOffset = 0

        // Explicitly load pending candidates concurrently on refresh
        async let candidateTask: Void = loadPendingCandidates()

        do {
            let response = try await apiClient.fetchMemories(
                type: selectedType,
                status: .active,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: 0,
                includeArchived: false
            )
            self.memories = response.items
            self.totalCount = response.total
            self.currentOffset = response.items.count
        } catch {
            self.errorMessage = error.localizedDescription
        }

        _ = await candidateTask
        isLoading = false
    }

    /// Load subsequent page of memories (pagination)
    public func loadMore() async {
        guard !isLoading, !isLoadingMore, memories.count < totalCount else { return }

        isLoadingMore = true
        do {
            let response = try await apiClient.fetchMemories(
                type: selectedType,
                status: .active,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: currentOffset,
                includeArchived: false
            )
            self.memories.append(contentsOf: response.items)
            self.totalCount = response.total
            self.currentOffset += response.items.count
        } catch {
            self.errorMessage = error.localizedDescription
        }
        isLoadingMore = false
    }

    /// Change selected MemoryType filter and reload
    public func selectType(_ type: MemoryType?) {
        guard selectedType != type else { return }
        selectedType = type
        Task {
            await loadMemories()
        }
    }

    /// Debounced search trigger (350ms)
    private func scheduleDebouncedSearch() {
        searchTask?.cancel()
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            await loadMemories()
        }
    }

    /// Create a new explicit memory
    public func createMemory(
        content: String,
        type: MemoryType = .explicit,
        importance: Int = 3,
        tags: [String] = [],
        expiresAt: Date? = nil
    ) async -> Bool {
        let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            self.errorMessage = "Memory content cannot be empty."
            return false
        }

        errorMessage = nil
        do {
            let payload = MemoryCreatePayload(
                content: trimmed,
                type: type,
                importance: importance,
                tags: tags,
                expiresAt: expiresAt
            )
            let created = try await apiClient.createMemory(payload: payload)

            // Insert optimistically at the top if matches current filter
            if selectedType == nil || selectedType == created.type {
                memories.insert(created, at: 0)
                totalCount += 1
            }
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Update allowed fields on an existing memory
    public func updateMemory(
        id: String,
        content: String? = nil,
        importance: Int? = nil,
        tags: [String]? = nil,
        expiresAt: Date? = nil
    ) async -> Bool {
        errorMessage = nil
        do {
            let payload = MemoryUpdatePayload(
                content: content,
                importance: importance,
                tags: tags,
                expiresAt: expiresAt
            )
            let updated = try await apiClient.updateMemory(id: id, payload: payload)

            if let idx = memories.firstIndex(where: { $0.id == id }) {
                memories[idx] = updated
            }
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Soft delete (archive) a memory
    public func deleteMemory(id: String) async -> Bool {
        errorMessage = nil
        do {
            _ = try await apiClient.deleteMemory(id: id)
            // Remove from active list
            if let idx = memories.firstIndex(where: { $0.id == id }) {
                memories.remove(at: idx)
                totalCount = max(0, totalCount - 1)
            }
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    // MARK: - Phase 5L.2 Candidate Review Actions
    
    /// Fetch pending memory candidates awaiting user review
    public func loadPendingCandidates() async {
        do {
            let res = try await apiClient.fetchMemoryCandidates(limit: 50, offset: 0)
            self.pendingCandidates = res.items
            self.pendingCandidatesCount = res.total
        } catch {
            // Non-fatal, do not clobber memory list error
        }
    }

    /// Approve a pending candidate, applying optional edits, tag overrides, or conflict action
    public func approveCandidate(
        id: String,
        editedContent: String? = nil,
        overrideTags: [String]? = nil,
        conflictAction: String? = nil
    ) async -> Bool {
        errorMessage = nil
        do {
            let payload = CandidateApprovePayload(
                editedContent: editedContent,
                overrideTags: overrideTags,
                conflictAction: conflictAction
            )
            let res = try await apiClient.approveMemoryCandidate(id: id, payload: payload)

            // Remove from pending candidates list
            pendingCandidates.removeAll { $0.id == id }
            pendingCandidatesCount = max(0, pendingCandidatesCount - 1)

            // Insert new or updated memory optimistically if matching active filter
            if selectedType == nil || selectedType == res.memory.type {
                memories.insert(res.memory, at: 0)
                totalCount += 1
            }
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Reject and discard a pending candidate
    public func rejectCandidate(id: String) async -> Bool {
        errorMessage = nil
        do {
            _ = try await apiClient.rejectMemoryCandidate(id: id)
            pendingCandidates.removeAll { $0.id == id }
            pendingCandidatesCount = max(0, pendingCandidatesCount - 1)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    public func dismissError() {
        self.errorMessage = nil
    }
}

import Foundation
import Combine

@MainActor
public final class ReminderViewModel: ObservableObject {
    @Published public var reminders: [ReminderItem] = []
    @Published public var totalCount: Int = 0
    @Published public var selectedStatus: ReminderStatus? = nil
    @Published public var searchQuery: String = "" {
        didSet {
            scheduleDebouncedSearch()
        }
    }
    @Published public var isLoading: Bool = false
    @Published public var isLoadingMore: Bool = false
    @Published public var errorMessage: String? = nil

    // Explicit Notification Permission state
    @Published public var notificationPermissionStatus: PermissionStatus = .notDetermined
    @Published public var isRequestingPermission: Bool = false

    private let apiClient: AuroraAPIClient
    private let notificationProvider: NotificationProviderProtocol
    private var searchTask: Task<Void, Never>? = nil
    private let pageSize: Int = 30
    private var currentOffset: Int = 0

    public init(
        apiClient: AuroraAPIClient = AuroraAPIClient(),
        notificationProvider: NotificationProviderProtocol = UNNotificationProvider()
    ) {
        self.apiClient = apiClient
        self.notificationProvider = notificationProvider
    }

    // MARK: - Notification Permission Handling

    public func checkNotificationPermission() async {
        let status = await notificationProvider.checkAuthorizationStatus()
        self.notificationPermissionStatus = status
    }

    public func requestNotificationPermission() async {
        isRequestingPermission = true
        defer { isRequestingPermission = false }

        do {
            let status = try await notificationProvider.requestAuthorization()
            self.notificationPermissionStatus = status
        } catch {
            self.errorMessage = "Failed to request notification permission: \(error.localizedDescription)"
        }
    }

    // MARK: - Reminders Querying & Pagination

    public func loadReminders() async {
        isLoading = true
        errorMessage = nil
        currentOffset = 0

        // Also refresh permission state
        await checkNotificationPermission()

        do {
            let response = try await apiClient.fetchReminders(
                status: selectedStatus,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: 0
            )
            self.reminders = response.items
            self.totalCount = response.total
            self.currentOffset = response.items.count
        } catch {
            self.errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    public func loadMore() async {
        guard !isLoading, !isLoadingMore, reminders.count < totalCount else { return }

        isLoadingMore = true
        do {
            let response = try await apiClient.fetchReminders(
                status: selectedStatus,
                query: searchQuery.isEmpty ? nil : searchQuery,
                limit: pageSize,
                offset: currentOffset
            )
            self.reminders.append(contentsOf: response.items)
            self.totalCount = response.total
            self.currentOffset += response.items.count
        } catch {
            self.errorMessage = error.localizedDescription
        }
        isLoadingMore = false
    }

    public func setStatusFilter(_ status: ReminderStatus?) {
        guard selectedStatus != status else { return }
        selectedStatus = status
        Task { await loadReminders() }
    }

    private func scheduleDebouncedSearch() {
        searchTask?.cancel()
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            await loadReminders()
        }
    }

    // MARK: - Reminder Operations

    public func createReminder(
        title: String,
        scheduledAt: Date,
        body: String? = nil,
        taskId: String? = nil,
        commitmentId: String? = nil
    ) async -> Bool {
        let payload = ReminderCreateRequest(
            title: title,
            scheduledAt: scheduledAt,
            body: body,
            taskId: taskId,
            commitmentId: commitmentId
        )

        do {
            let created = try await apiClient.createReminder(payload: payload)

            // Direct REST response drives local notification reconciliation
            await reconcileNotification(for: created)

            self.reminders.insert(created, at: 0)
            self.totalCount += 1
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    public func updateReminder(
        id: String,
        title: String? = nil,
        body: String? = nil,
        scheduledAt: Date? = nil
    ) async -> Bool {
        let payload = ReminderUpdateRequest(
            title: title,
            body: body,
            scheduledAt: scheduledAt
        )

        do {
            let updated = try await apiClient.updateReminder(id: id, payload: payload)

            // Stale check using updatedAt
            if let idx = reminders.firstIndex(where: { $0.id == id }) {
                if reminders[idx].updatedAt <= updated.updatedAt {
                    reminders[idx] = updated
                }
            }

            // Direct REST response drives notification reconciliation
            await reconcileNotification(for: updated)

            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    public func snoozeReminder(id: String, durationMinutes: Int = 15) async -> Bool {
        do {
            let updated = try await apiClient.snoozeReminder(id: id, durationMinutes: durationMinutes)

            // Stale check using updatedAt
            if let idx = reminders.firstIndex(where: { $0.id == id }) {
                if reminders[idx].updatedAt <= updated.updatedAt {
                    reminders[idx] = updated
                }
            }

            // Direct REST response drives notification reconciliation
            await reconcileNotification(for: updated)

            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    public func cancelReminder(id: String) async -> Bool {
        do {
            let cancelled = try await apiClient.cancelReminder(id: id)

            if let idx = reminders.firstIndex(where: { $0.id == id }) {
                if reminders[idx].updatedAt <= cancelled.updatedAt {
                    reminders[idx] = cancelled
                }
            }

            // Direct REST response drives notification reconciliation
            await reconcileNotification(for: cancelled)
            return true
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    public func deleteReminder(id: String) async -> Bool {
        do {
            let resp = try await apiClient.deleteReminder(id: id)
            if resp.deleted {
                reminders.removeAll { $0.id == id }
                totalCount = max(0, totalCount - 1)
                // Direct REST cancellation of active notification
                await notificationProvider.cancel(ids: [id])
                return true
            }
            return false
        } catch {
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    // MARK: - Phase 6B Notification Reconciliation

    public func reconcileNotification(for reminder: ReminderItem) async {
        guard let signal = reminder.notificationSync else { return }
        await handleSyncSignal(signal, for: reminder)
    }

    public func handleSyncSignal(_ signal: NotificationSyncSignal, for reminder: ReminderItem) async {
        let notifId = reminder.notificationId ?? reminder.id
        switch signal {
        case .SCHEDULE:
            do {
                try await notificationProvider.schedule(
                    id: notifId,
                    title: reminder.title,
                    body: reminder.body,
                    scheduledAt: reminder.scheduledAt
                )
            } catch {
                print("[NotificationSync] Failed to schedule local notification: \(error)")
            }
        case .RESCHEDULE:
            await notificationProvider.cancel(ids: [notifId])
            do {
                try await notificationProvider.schedule(
                    id: notifId,
                    title: reminder.title,
                    body: reminder.body,
                    scheduledAt: reminder.scheduledAt
                )
            } catch {
                print("[NotificationSync] Failed to reschedule local notification: \(error)")
            }
        case .CANCEL:
            await notificationProvider.cancel(ids: [notifId])
        case .NONE:
            break
        }
    }
}

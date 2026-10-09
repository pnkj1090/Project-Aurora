import Foundation
import Combine

@MainActor
public final class CalendarViewModel: ObservableObject {
    @Published public var calendarPermissionStatus: PermissionStatus = .notDetermined
    @Published public var events: [CalendarEventItem] = []
    @Published public var isLoading: Bool = false
    @Published public var isRequestingPermission: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiClient: AuroraAPIClient
    private let calendarProvider: CalendarProviderProtocol

    public init(
        apiClient: AuroraAPIClient = AuroraAPIClient(),
        calendarProvider: CalendarProviderProtocol = EKCalendarProvider()
    ) {
        self.apiClient = apiClient
        self.calendarProvider = calendarProvider
    }

    // MARK: - Permission Handling

    public func checkCalendarPermission() async {
        let status = await calendarProvider.checkAuthorizationStatus()
        self.calendarPermissionStatus = status
    }

    public func requestCalendarPermission() async {
        guard calendarPermissionStatus != .denied && calendarPermissionStatus != .restricted else {
            self.errorMessage = "Calendar access was previously denied or restricted. Please enable it in iOS Settings."
            return
        }

        isRequestingPermission = true
        errorMessage = nil
        defer { isRequestingPermission = false }

        do {
            let status = try await calendarProvider.requestAuthorization()
            self.calendarPermissionStatus = status
            if status == .authorized {
                await loadUpcomingEvents()
            }
        } catch {
            self.errorMessage = "Failed to request calendar authorization: \(error.localizedDescription)"
        }
    }

    // MARK: - Upcoming Event Retrieval

    public func loadUpcomingEvents(windowHours: Int = 48) async {
        guard calendarPermissionStatus == .authorized else {
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        let now = Date()
        let endAt = Calendar.current.date(byAdding: .hour, value: windowHours, to: now) ?? now.addingTimeInterval(TimeInterval(windowHours * 3600))

        do {
            let fetched = try await calendarProvider.fetchEvents(startAt: now, endAt: endAt, maxResults: 20)
            self.events = fetched

            // Ephemerally report the fresh schedule snapshot to the backend context layer
            _ = try? await apiClient.reportCalendarEvents(events: fetched)
        } catch {
            self.errorMessage = error.localizedDescription
        }
    }
}

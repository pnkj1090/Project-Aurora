import Foundation
import Combine

@MainActor
public final class LocationViewModel: ObservableObject {
    @Published public var locationPermissionStatus: PermissionStatus = .notDetermined
    @Published public var currentLocation: LocationItem? = nil
    @Published public var isRequestingLocation: Bool = false
    @Published public var isRequestingPermission: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiClient: AuroraAPIClient
    private let locationProvider: LocationProviderProtocol

    public init(
        apiClient: AuroraAPIClient = AuroraAPIClient(),
        locationProvider: LocationProviderProtocol = CoreLocationProvider()
    ) {
        self.apiClient = apiClient
        self.locationProvider = locationProvider
    }

    // MARK: - Permission Handling

    public func checkLocationPermission() async {
        let status = await locationProvider.checkAuthorizationStatus()
        self.locationPermissionStatus = status
    }

    public func requestLocationPermission() async {
        guard locationPermissionStatus != .denied && locationPermissionStatus != .restricted else {
            self.errorMessage = "Location access was previously denied or restricted. Please enable it in iOS Settings."
            return
        }

        isRequestingPermission = true
        errorMessage = nil
        defer { isRequestingPermission = false }

        do {
            let status = try await locationProvider.requestAuthorization()
            self.locationPermissionStatus = status
        } catch {
            self.errorMessage = "Failed to request location authorization: \(error.localizedDescription)"
        }
    }

    // MARK: - Current Location Retrieval

    public func fetchCurrentLocation() async {
        guard locationPermissionStatus == .authorized else {
            self.errorMessage = "Location permission is not authorized."
            return
        }

        isRequestingLocation = true
        errorMessage = nil
        defer { isRequestingLocation = false }

        do {
            let location = try await locationProvider.getCurrentLocation()
            self.currentLocation = location

            // Ephemerally report the fresh snapshot to the backend context layer
            _ = try? await apiClient.reportCurrentLocation(item: location)
        } catch {
            self.errorMessage = error.localizedDescription
        }
    }

    public func clearCurrentLocation() {
        self.currentLocation = nil
    }
}

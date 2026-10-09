import Foundation
import CoreLocation

// MARK: - Location Provider Protocol

public protocol LocationProviderProtocol: Sendable {
    func checkAuthorizationStatus() async -> PermissionStatus
    func requestAuthorization() async throws -> PermissionStatus
    func getCurrentLocation() async throws -> LocationItem
}

// MARK: - Location Errors

public enum LocationProviderError: LocalizedError, Equatable {
    case unauthorized(PermissionStatus)
    case servicesUnavailable
    case locationUnavailable(String)
    case timeout

    public var errorDescription: String? {
        switch self {
        case .unauthorized(let status):
            return "Location access is not authorized (status: \(status.rawValue))."
        case .servicesUnavailable:
            return "Location services are unavailable on this device."
        case .locationUnavailable(let message):
            return "Unable to retrieve current location: \(message)"
        case .timeout:
            return "Location request timed out."
        }
    }
}

// MARK: - CoreLocation Implementation

public final class CoreLocationProvider: NSObject, LocationProviderProtocol, CLLocationManagerDelegate, @unchecked Sendable {
    private let locationManager: CLLocationManager
    private let lock = NSLock()
    private var locationContinuation: CheckedContinuation<LocationItem, Error>?
    private var authContinuation: CheckedContinuation<PermissionStatus, Error>?

    public init(locationManager: CLLocationManager = CLLocationManager()) {
        self.locationManager = locationManager
        super.init()
        self.locationManager.delegate = self
        self.locationManager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        guard CLLocationManager.locationServicesEnabled() else {
            return .unavailable
        }
        return mapStatus(locationManager.authorizationStatus)
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        guard CLLocationManager.locationServicesEnabled() else {
            return .unavailable
        }

        let current = mapStatus(locationManager.authorizationStatus)
        guard current == .notDetermined else {
            return current
        }

        return try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            self.authContinuation = continuation
            lock.unlock()

            locationManager.requestWhenInUseAuthorization()
        }
    }

    public func getCurrentLocation() async throws -> LocationItem {
        let auth = await checkAuthorizationStatus()
        guard auth == .authorized else {
            throw LocationProviderError.unauthorized(auth)
        }

        return try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            // Cancel any pending location continuation before starting a new one
            if let pending = self.locationContinuation {
                pending.resume(throwing: LocationProviderError.locationUnavailable("Superseded by new request"))
            }
            self.locationContinuation = continuation
            lock.unlock()

            // One-shot location request (does not start continuous tracking)
            locationManager.requestLocation()
        }
    }

    // MARK: - CLLocationManagerDelegate

    public func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let mapped = mapStatus(manager.authorizationStatus)
        lock.lock()
        if let cont = authContinuation {
            authContinuation = nil
            lock.unlock()
            cont.resume(returning: mapped)
        } else {
            lock.unlock()
        }
    }

    public func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        lock.lock()
        let cont = locationContinuation
        locationContinuation = nil
        lock.unlock()

        guard let lastLocation = locations.last else {
            cont?.resume(throwing: LocationProviderError.locationUnavailable("No coordinate returned"))
            return
        }

        let item = LocationItem(
            latitude: lastLocation.coordinate.latitude,
            longitude: lastLocation.coordinate.longitude,
            accuracyMeters: lastLocation.horizontalAccuracy,
            capturedAt: lastLocation.timestamp,
            source: "ios_core_location"
        )
        cont?.resume(returning: item)
    }

    public func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        lock.lock()
        let cont = locationContinuation
        locationContinuation = nil
        lock.unlock()

        cont?.resume(throwing: LocationProviderError.locationUnavailable(error.localizedDescription))
    }

    // MARK: - Helper

    private func mapStatus(_ status: CLAuthorizationStatus) -> PermissionStatus {
        switch status {
        case .notDetermined:
            return .notDetermined
        case .restricted:
            return .restricted
        case .denied:
            return .denied
        case .authorizedWhenInUse, .authorizedAlways:
            return .authorized
        @unknown default:
            return .unavailable
        }
    }
}

// MARK: - Mock Location Provider (for Previews & Offline Tests)

public final class MockLocationProvider: LocationProviderProtocol, @unchecked Sendable {
    public var currentStatus: PermissionStatus
    public var mockLocation: LocationItem?
    public var shouldThrowError: Bool = false

    public init(
        initialStatus: PermissionStatus = .notDetermined,
        mockLocation: LocationItem? = LocationItem(
            latitude: 37.7749,
            longitude: -122.4194,
            accuracyMeters: 10.0,
            capturedAt: Date(),
            source: "mock_provider"
        )
    ) {
        self.currentStatus = initialStatus
        self.mockLocation = mockLocation
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        return currentStatus
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        if currentStatus == .notDetermined {
            currentStatus = .authorized
        }
        return currentStatus
    }

    public func getCurrentLocation() async throws -> LocationItem {
        guard currentStatus == .authorized else {
            throw LocationProviderError.unauthorized(currentStatus)
        }

        if shouldThrowError {
            throw LocationProviderError.locationUnavailable("Simulated provider failure")
        }

        guard let location = mockLocation else {
            throw LocationProviderError.locationUnavailable("No mock location available")
        }

        return location
    }
}

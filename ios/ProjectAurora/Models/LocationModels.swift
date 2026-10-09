import Foundation

// MARK: - Location Snapshot Model

public struct LocationItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String {
        "\(latitude),\(longitude),\(capturedAt.timeIntervalSince1970)"
    }

    public let latitude: Double
    public let longitude: Double
    public let accuracyMeters: Double
    public let capturedAt: Date
    public let source: String
    public let userId: String?

    enum CodingKeys: String, CodingKey {
        case latitude
        case longitude
        case accuracyMeters = "accuracy_meters"
        case capturedAt = "captured_at"
        case source
        case userId = "user_id"
    }

    public init(
        latitude: Double,
        longitude: Double,
        accuracyMeters: Double,
        capturedAt: Date = Date(),
        source: String = "ios_core_location",
        userId: String? = nil
    ) {
        self.latitude = latitude
        self.longitude = longitude
        self.accuracyMeters = accuracyMeters
        self.capturedAt = capturedAt
        self.source = source
        self.userId = userId
    }
}

// MARK: - Location Context/API Response Model

public struct LocationResponse: Codable, Equatable, Sendable {
    public let status: String?
    public let location: LocationItem?
    public let message: String?

    enum CodingKeys: String, CodingKey {
        case status
        case location
        case message
    }

    public init(status: String? = nil, location: LocationItem? = nil, message: String? = nil) {
        self.status = status
        self.location = location
        self.message = message
    }
}

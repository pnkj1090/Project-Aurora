import Foundation

// MARK: - Contact Item Model

public struct ContactItem: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let displayName: String
    public let phoneNumbers: [String]
    public let emailAddresses: [String]
    public let organization: String?
    public let source: String
    public let userId: String?

    enum CodingKeys: String, CodingKey {
        case id = "contact_id"
        case displayName = "display_name"
        case phoneNumbers = "phone_numbers"
        case emailAddresses = "email_addresses"
        case organization
        case source
        case userId = "user_id"
    }

    public init(
        id: String,
        displayName: String,
        phoneNumbers: [String] = [],
        emailAddresses: [String] = [],
        organization: String? = nil,
        source: String = "ios_contacts",
        userId: String? = nil
    ) {
        self.id = id
        self.displayName = displayName
        self.phoneNumbers = phoneNumbers
        self.emailAddresses = emailAddresses
        self.organization = organization
        self.source = source
        self.userId = userId
    }
}

// MARK: - Contact Search API Response

public struct ContactSearchResponse: Codable, Sendable {
    public let status: String
    public let count: Int
    public let contacts: [ContactItem]
    public let query: String?

    public init(status: String = "success", count: Int, contacts: [ContactItem], query: String? = nil) {
        self.status = status
        self.count = count
        self.contacts = contacts
        self.query = query
    }
}

// MARK: - Contact Ephemeral Snapshot Ingest Request

public struct ContactSnapshotIngestRequest: Codable, Sendable {
    public let contacts: [ContactItem]
    public let userId: String?

    enum CodingKeys: String, CodingKey {
        case contacts
        case userId = "user_id"
    }

    public init(contacts: [ContactItem], userId: String? = nil) {
        self.contacts = contacts
        self.userId = userId
    }
}

import Foundation
import Contacts

// MARK: - Contacts Provider Protocol

public protocol ContactsProviderProtocol: Sendable {
    func checkAuthorizationStatus() async -> PermissionStatus
    func requestAuthorization() async throws -> PermissionStatus
    func fetchContacts(query: String?, maxResults: Int) async throws -> [ContactItem]
}

// MARK: - Contacts Provider Errors

public enum ContactsProviderError: LocalizedError, Equatable {
    case unauthorized(PermissionStatus)
    case fetchFailed(String)

    public var errorDescription: String? {
        switch self {
        case .unauthorized(let status):
            return "Contacts access is not authorized (status: \(status.rawValue))."
        case .fetchFailed(let message):
            return "Failed to fetch contacts: \(message)"
        }
    }
}

// MARK: - Native Contacts Framework Provider (Read-Only)

public final class CNContactsProvider: ContactsProviderProtocol, @unchecked Sendable {
    private let contactStore: CNContactStore

    public init(contactStore: CNContactStore = CNContactStore()) {
        self.contactStore = contactStore
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        let status = CNContactStore.authorizationStatus(for: .contacts)
        return mapStatus(status)
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        let current = await checkAuthorizationStatus()
        guard current == .notDetermined else {
            return current
        }

        return try await withCheckedThrowingContinuation { continuation in
            contactStore.requestAccess(for: .contacts) { granted, error in
                if let error = error {
                    continuation.resume(throwing: ContactsProviderError.fetchFailed(error.localizedDescription))
                } else {
                    continuation.resume(returning: granted ? .authorized : .denied)
                }
            }
        }
    }

    public func fetchContacts(query: String? = nil, maxResults: Int = 10) async throws -> [ContactItem] {
        let auth = await checkAuthorizationStatus()
        guard auth == .authorized else {
            throw ContactsProviderError.unauthorized(auth)
        }

        let keysToFetch: [CNKeyDescriptor] = [
            CNContactIdentifierKey as CNKeyDescriptor,
            CNContactGivenNameKey as CNKeyDescriptor,
            CNContactFamilyNameKey as CNKeyDescriptor,
            CNContactMiddleNameKey as CNKeyDescriptor,
            CNContactOrganizationNameKey as CNKeyDescriptor,
            CNContactPhoneNumbersKey as CNKeyDescriptor,
            CNContactEmailAddressesKey as CNKeyDescriptor
        ]

        let limit = max(1, min(maxResults, 50))
        var results: [ContactItem] = []

        let trimmedQuery = query?.trimmingCharacters(in: .whitespacesAndNewlines)

        if let q = trimmedQuery, !q.isEmpty {
            // Predicate search by name
            let predicate = CNContact.predicateForContacts(matchingName: q)
            let unified = try contactStore.unifiedContacts(matching: predicate, keysToFetch: keysToFetch)
            for contact in unified.prefix(limit) {
                results.append(mapContact(contact))
            }
        } else {
            // Enumerate contacts up to limit
            let request = CNContactFetchRequest(keysToFetch: keysToFetch)
            var count = 0
            try contactStore.enumerateContacts(with: request) { contact, stop in
                results.append(self.mapContact(contact))
                count += 1
                if count >= limit {
                    stop.pointee = true
                }
            }
        }

        return results
    }

    // MARK: - Private Helpers

    private func mapStatus(_ status: CNAuthorizationStatus) -> PermissionStatus {
        switch status {
        case .notDetermined:
            return .notDetermined
        case .restricted:
            return .restricted
        case .denied:
            return .denied
        case .authorized:
            return .authorized
        @unknown default:
            return .unavailable
        }
    }

    private func mapContact(_ contact: CNContact) -> ContactItem {
        let formatter = CNContactFormatter()
        let name = formatter.string(from: contact) ??
            "\(contact.givenName) \(contact.familyName)".trimmingCharacters(in: .whitespaces)

        let displayName = name.isEmpty ? "Unknown Contact" : name

        let phones = contact.phoneNumbers.map { $0.value.stringValue.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        let emails = contact.emailAddresses.map { ($0.value as String).trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        let org = contact.organizationName.trimmingCharacters(in: .whitespaces)

        return ContactItem(
            id: contact.identifier,
            displayName: displayName,
            phoneNumbers: phones,
            emailAddresses: emails,
            organization: org.isEmpty ? nil : org,
            source: "ios_contacts"
        )
    }
}

// MARK: - Mock Contacts Provider for Previews and Unit Testing

public final class MockContactsProvider: ContactsProviderProtocol, @unchecked Sendable {
    public var mockStatus: PermissionStatus
    public var mockContacts: [ContactItem]

    public init(
        status: PermissionStatus = .authorized,
        contacts: [ContactItem] = [
            ContactItem(
                id: "mock-1",
                displayName: "Gordon Freeman",
                phoneNumbers: ["+15554321"],
                emailAddresses: ["gordon@blackmesa.gov"],
                organization: "Black Mesa"
            ),
            ContactItem(
                id: "mock-2",
                displayName: "Alyx Vance",
                phoneNumbers: ["+15558765"],
                emailAddresses: ["alyx@resistance.org"],
                organization: "Resistance"
            ),
            ContactItem(
                id: "mock-3",
                displayName: "Sarah Connor",
                phoneNumbers: ["+15551234"],
                emailAddresses: ["sarah@resistance.org"],
                organization: "Resistance"
            )
        ]
    ) {
        self.mockStatus = status
        self.mockContacts = contacts
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        return mockStatus
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        if mockStatus == .notDetermined {
            mockStatus = .authorized
        }
        return mockStatus
    }

    public func fetchContacts(query: String? = nil, maxResults: Int = 10) async throws -> [ContactItem] {
        guard mockStatus == .authorized else {
            throw ContactsProviderError.unauthorized(mockStatus)
        }

        let limit = max(1, min(maxResults, 50))
        guard let q = query?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(), !q.isEmpty else {
            return Array(mockContacts.prefix(limit))
        }

        let filtered = mockContacts.filter { c in
            c.displayName.lowercased().contains(q) ||
            (c.organization?.lowercased().contains(q) ?? false) ||
            c.phoneNumbers.contains { $0.contains(q) } ||
            c.emailAddresses.contains { $0.lowercased().contains(q) }
        }

        return Array(filtered.prefix(limit))
    }
}

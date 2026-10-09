import Foundation
import Combine

@MainActor
public final class ContactViewModel: ObservableObject {
    @Published public var permissionStatus: PermissionStatus = .notDetermined
    @Published public var searchText: String = ""
    @Published public var contacts: [ContactItem] = []
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    private let contactsProvider: ContactsProviderProtocol
    private let apiClient: AuroraAPIClient

    public init(
        contactsProvider: ContactsProviderProtocol = CNContactsProvider(),
        apiClient: AuroraAPIClient = AuroraAPIClient()
    ) {
        self.contactsProvider = contactsProvider
        self.apiClient = apiClient
    }

    public func checkPermission() async {
        let status = await contactsProvider.checkAuthorizationStatus()
        self.permissionStatus = status
    }

    public func requestPermission() async {
        do {
            let status = try await contactsProvider.requestAuthorization()
            self.permissionStatus = status
            if status == .authorized {
                await searchContacts()
            }
        } catch {
            self.errorMessage = error.localizedDescription
        }
    }

    public func searchContacts() async {
        guard permissionStatus == .authorized else {
            return
        }

        isLoading = true
        errorMessage = nil

        do {
            let queryParam = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
            let effectiveQuery = queryParam.isEmpty ? nil : queryParam

            // 1. Perform bounded local query using device Contacts framework
            let localMatches = try await contactsProvider.fetchContacts(query: effectiveQuery, maxResults: 20)
            self.contacts = localMatches

            // 2. Report only the bounded matching subset to Aurora backend for ephemeral AI/tool context
            // CRITICAL AMENDMENT 1: Never upload the full address book!
            if !localMatches.isEmpty {
                _ = try? await apiClient.reportContactsSnapshot(contacts: localMatches)
            }
        } catch {
            self.errorMessage = error.localizedDescription
        }

        isLoading = false
    }
}

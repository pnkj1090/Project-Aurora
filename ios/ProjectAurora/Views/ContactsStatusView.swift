import SwiftUI

public struct ContactsStatusView: View {
    @StateObject private var viewModel: ContactViewModel

    public init(viewModel: ContactViewModel? = nil) {
        _viewModel = StateObject(wrappedValue: viewModel ?? ContactViewModel())
    }

    public var body: some View {
        List {
            // MARK: - Permission Status Section
            Section {
                permissionStatusCard
            } header: {
                Text("Permission Status")
            }

            // MARK: - Search Section
            if viewModel.permissionStatus == .authorized {
                Section {
                    HStack {
                        Image(systemName: "magnifyingglass")
                            .foregroundColor(.secondary)
                        TextField("Search contacts by name, company, phone, email", text: $viewModel.searchText)
                            .textFieldStyle(.plain)
                            .onSubmit {
                                Task { await viewModel.searchContacts() }
                            }

                        if !viewModel.searchText.isEmpty {
                            Button {
                                viewModel.searchText = ""
                                Task { await viewModel.searchContacts() }
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundColor(.secondary)
                            }
                        }
                    }

                    Button {
                        Task { await viewModel.searchContacts() }
                    } label: {
                        HStack {
                            Spacer()
                            Text("Search Contacts")
                                .fontWeight(.medium)
                            Spacer()
                        }
                    }
                } header: {
                    Text("Lookup")
                }

                // MARK: - Contact Results Section
                Section {
                    if viewModel.isLoading {
                        HStack {
                            Spacer()
                            ProgressView("Searching contacts...")
                            Spacer()
                        }
                        .padding(.vertical, 8)
                    } else if viewModel.contacts.isEmpty {
                        Text(viewModel.searchText.isEmpty ? "No contacts loaded. Enter a query or tap search." : "No contacts match '\(viewModel.searchText)'.")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                            .italic()
                    } else {
                        ForEach(viewModel.contacts) { contact in
                            contactRow(contact)
                        }
                    }
                } header: {
                    Text("Contacts (\(viewModel.contacts.count))")
                }
            }

            // MARK: - Privacy & Security Notice
            Section {
                privacyNoticeCard
            } header: {
                Text("Privacy & Security")
            }
        }
        .navigationTitle("Contacts")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.checkPermission()
            if viewModel.permissionStatus == .authorized {
                await viewModel.searchContacts()
            }
        }
    }

    // MARK: - Subviews

    private var permissionStatusCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: statusIcon)
                    .foregroundColor(statusColor)
                    .font(.title2)
                VStack(alignment: .leading) {
                    Text(statusTitle)
                        .font(.headline)
                    Text(statusDescription)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
            }

            if viewModel.permissionStatus != .authorized {
                Button {
                    Task { await viewModel.requestPermission() }
                } label: {
                    Text(viewModel.permissionStatus == .notDetermined ? "Enable Contacts" : "Open Settings")
                        .font(.subheadline.bold())
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.blue)
                .padding(.top, 4)
            }
        }
        .padding(.vertical, 4)
    }

    private func contactRow(_ contact: ContactItem) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(contact.displayName)
                .font(.body.weight(.medium))

            if let org = contact.organization {
                Text(org)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if !contact.phoneNumbers.isEmpty {
                HStack(spacing: 4) {
                    Image(systemName: "phone")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    Text(contact.phoneNumbers.joined(separator: ", "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            if !contact.emailAddresses.isEmpty {
                HStack(spacing: 4) {
                    Image(systemName: "envelope")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    Text(contact.emailAddresses.joined(separator: ", "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var privacyNoticeCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "lock.shield")
                    .foregroundColor(.green)
                Text("Zero Contact Persistence Guarantee")
                    .font(.footnote.bold())
            }

            Text("• Read-only: Aurora cannot create, edit, or delete contacts.\n• Data minimization: Only essential contact details are retrieved.\n• Ephemeral in-memory only: Contacts are never stored in a database.\n• Opt-in: Contact info is never automatically dumped into chat prompts.\n• Strictly no automatic task, reminder, or memory creation.")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 4)
    }

    // MARK: - Status Presentation Helpers

    private var statusIcon: String {
        switch viewModel.permissionStatus {
        case .authorized: return "checkmark.seal.fill"
        case .denied: return "xmark.seal.fill"
        case .restricted: return "exclamationmark.shield.fill"
        case .notDetermined: return "questionmark.circle.fill"
        case .unavailable: return "slash.circle.fill"
        }
    }

    private var statusColor: Color {
        switch viewModel.permissionStatus {
        case .authorized: return .green
        case .denied: return .red
        case .restricted: return .orange
        case .notDetermined: return .blue
        case .unavailable: return .gray
        }
    }

    private var statusTitle: String {
        switch viewModel.permissionStatus {
        case .authorized: return "Contacts Authorized"
        case .denied: return "Access Denied"
        case .restricted: return "Access Restricted"
        case .notDetermined: return "Access Not Determined"
        case .unavailable: return "Contacts Unavailable"
        }
    }

    private var statusDescription: String {
        switch viewModel.permissionStatus {
        case .authorized:
            return "Aurora can query contact details for explicit assistance."
        case .denied:
            return "Contacts permission was denied in system Settings."
        case .restricted:
            return "Contacts access is restricted by device parental or MDM controls."
        case .notDetermined:
            return "Grant permission to search contact details when needed."
        case .unavailable:
            return "Contacts framework is unavailable on this platform."
        }
    }
}

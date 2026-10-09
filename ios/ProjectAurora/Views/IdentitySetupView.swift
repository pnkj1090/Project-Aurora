import SwiftUI

public struct IdentitySetupView: View {
    @Environment(\.dismiss) private var dismiss
    let onProvisioned: () -> Void
    @State private var secret = ""
    @State private var errorMessage: String?
    @State private var isProvisioning = false
    private let apiClient = AuroraAPIClient()

    public init(onProvisioned: @escaping () -> Void) { self.onProvisioned = onProvisioned }

    public var body: some View {
        NavigationStack {
            Form {
                Section("Connect this installation") {
                    Text("Run the local provisioning command on your Aurora server, then enter its one-time secret. The secret expires after 10 minutes.")
                        .font(.subheadline)
                    SecureField("One-time provisioning secret", text: $secret)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button {
                        Task { await provision() }
                    } label: {
                        if isProvisioning { ProgressView() } else { Text("Connect") }
                    }
                    .disabled(secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isProvisioning)
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Aurora Setup")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
            }
        }
    }

    @MainActor
    private func provision() async {
        isProvisioning = true
        errorMessage = nil
        do {
            _ = try await apiClient.provisionInstallation(secret: secret.trimmingCharacters(in: .whitespacesAndNewlines))
            secret = ""
            onProvisioned()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
        isProvisioning = false
    }
}

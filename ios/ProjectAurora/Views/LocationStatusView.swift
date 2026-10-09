import SwiftUI

public struct LocationStatusView: View {
    @StateObject private var viewModel: LocationViewModel

    @MainActor
    public init(viewModel: LocationViewModel? = nil) {
        _viewModel = StateObject(wrappedValue: viewModel ?? LocationViewModel())
    }

    public var body: some View {
        Form {
            Section {
                statusHeader
            } header: {
                Text("Permission Status")
            } footer: {
                statusFooter
            }

            if viewModel.locationPermissionStatus == .authorized {
                Section("Current Snapshot") {
                    if let location = viewModel.currentLocation {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text("Captured:")
                                    .fontWeight(.medium)
                                Spacer()
                                Text(location.capturedAt, style: .time)
                                    .foregroundColor(.secondary)
                            }
                            HStack {
                                Text("Accuracy:")
                                    .fontWeight(.medium)
                                Spacer()
                                Text("±\(Int(location.accuracyMeters))m")
                                    .foregroundColor(.secondary)
                            }
                            HStack {
                                Text("Source:")
                                    .fontWeight(.medium)
                                Spacer()
                                Text(location.source)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                    } else {
                        Text("No snapshot captured yet.")
                            .foregroundColor(.secondary)
                    }

                    Button {
                        Task {
                            await viewModel.fetchCurrentLocation()
                        }
                    } label: {
                        HStack {
                            if viewModel.isRequestingLocation {
                                ProgressView()
                                    .controlSize(.small)
                                Text("Requesting...")
                            } else {
                                Image(systemName: "location.fill")
                                Text("Request Current Snapshot")
                            }
                        }
                    }
                    .disabled(viewModel.isRequestingLocation)
                }
            }

            Section {
                VStack(alignment: .leading, spacing: 6) {
                    Label("Privacy First", systemImage: "hand.raised.shield")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(.blue)

                    Text("Aurora never tracks movement, never persists location history, and only accesses your current location when explicitly authorized and requested.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .padding(.vertical, 2)
            }
        }
        .navigationTitle("Location Context")
        .task {
            await viewModel.checkLocationPermission()
        }
    }

    // MARK: - Subviews

    @ViewBuilder
    private var statusHeader: some View {
        switch viewModel.locationPermissionStatus {
        case .authorized:
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                    .font(.title2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Authorized")
                        .font(.headline)
                    Text("Aurora has permission to access your current location.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

        case .notDetermined:
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    Image(systemName: "questionmark.circle.fill")
                        .foregroundColor(.orange)
                        .font(.title2)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Not Requested")
                            .font(.headline)
                        Text("Location access has not been requested yet.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                Button {
                    Task {
                        await viewModel.requestLocationPermission()
                    }
                } label: {
                    HStack {
                        if viewModel.isRequestingPermission {
                            ProgressView()
                                .controlSize(.small)
                            Text("Requesting...")
                        } else {
                            Text("Enable Location Access")
                                .fontWeight(.semibold)
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }

        case .denied:
            HStack(spacing: 12) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.red)
                    .font(.title2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Access Denied")
                        .font(.headline)
                    Text("Location permission was denied. You can enable it in iOS Settings.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

        case .restricted:
            HStack(spacing: 12) {
                Image(systemName: "lock.circle.fill")
                    .foregroundColor(.secondary)
                    .font(.title2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Restricted")
                        .font(.headline)
                    Text("Location access is restricted by device parental controls or MDM.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

        case .unavailable:
            HStack(spacing: 12) {
                Image(systemName: "slash.circle.fill")
                    .foregroundColor(.secondary)
                    .font(.title2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Unavailable")
                        .font(.headline)
                    Text("Location services are disabled on this device.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private var statusFooter: some View {
        if let error = viewModel.errorMessage {
            Text(error)
                .foregroundColor(.red)
        }
    }
}

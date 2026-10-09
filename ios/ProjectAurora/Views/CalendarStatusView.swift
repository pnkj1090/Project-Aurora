import SwiftUI

public struct CalendarStatusView: View {
    @StateObject private var viewModel: CalendarViewModel

    @MainActor
    public init(viewModel: CalendarViewModel? = nil) {
        _viewModel = StateObject(wrappedValue: viewModel ?? CalendarViewModel())
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

            if viewModel.calendarPermissionStatus == .authorized {
                Section("Upcoming Events (Next 48 Hours)") {
                    if viewModel.isLoading && viewModel.events.isEmpty {
                        HStack {
                            Spacer()
                            ProgressView("Loading calendar...")
                            Spacer()
                        }
                        .padding()
                    } else if viewModel.events.isEmpty {
                        Text("No upcoming events found.")
                            .foregroundColor(.secondary)
                    } else {
                        ForEach(viewModel.events) { event in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(event.title)
                                    .font(.headline)

                                HStack {
                                    if event.allDay {
                                        Label("All Day", systemImage: "sun.max")
                                            .font(.caption)
                                            .foregroundColor(.orange)
                                    } else {
                                        Text(event.startAt, style: .time)
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                        Text("-")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                        Text(event.endAt, style: .time)
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }

                                    if let cal = event.calendarName {
                                        Spacer()
                                        Text(cal)
                                            .font(.caption2)
                                            .padding(.horizontal, 6)
                                            .padding(.vertical, 2)
                                            .background(Color.blue.opacity(0.1))
                                            .cornerRadius(4)
                                    }
                                }

                                if let loc = event.location, !loc.isEmpty {
                                    Label(loc, systemImage: "mappin.and.ellipse")
                                        .font(.caption2)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    }

                    Button {
                        Task {
                            await viewModel.loadUpcomingEvents()
                        }
                    } label: {
                        HStack {
                            if viewModel.isLoading {
                                ProgressView()
                                    .controlSize(.small)
                                Text("Refreshing...")
                            } else {
                                Image(systemName: "arrow.clockwise")
                                Text("Refresh Events")
                            }
                        }
                    }
                    .disabled(viewModel.isLoading)
                }
            }

            Section {
                VStack(alignment: .leading, spacing: 6) {
                    Label("Privacy & Read-Only Guarantee", systemImage: "lock.shield")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(.blue)

                    Text("Aurora has strictly read-only calendar access. It cannot create, modify, or delete events, cannot send invitations, and never persists calendar history to disk.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .padding(.vertical, 2)
            }
        }
        .navigationTitle("Calendar Context")
        .task {
            await viewModel.checkCalendarPermission()
            if viewModel.calendarPermissionStatus == .authorized {
                await viewModel.loadUpcomingEvents()
            }
        }
    }

    // MARK: - Subviews

    @ViewBuilder
    private var statusHeader: some View {
        switch viewModel.calendarPermissionStatus {
        case .authorized:
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                    .font(.title2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Authorized")
                        .font(.headline)
                    Text("Aurora has read-only access to your calendar.")
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
                        Text("Calendar access has not been requested yet.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                Button {
                    Task {
                        await viewModel.requestCalendarPermission()
                    }
                } label: {
                    HStack {
                        if viewModel.isRequestingPermission {
                            ProgressView()
                                .controlSize(.small)
                            Text("Requesting...")
                        } else {
                            Text("Enable Calendar Access")
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
                    Text("Calendar permission was denied. You can enable it in iOS Settings.")
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
                    Text("Calendar access is restricted by device parental controls or MDM.")
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
                    Text("Calendar services are unavailable on this device.")
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

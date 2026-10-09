import SwiftUI

public struct ReminderListView: View {
    @StateObject private var viewModel = ReminderViewModel()
    @State private var showingCreateSheet = false

    public init() {}

    public var body: some View {
        VStack(spacing: 0) {
            // MARK: - Notification Permission Banner
            permissionBanner

            // MARK: - Status Filter Picker
            filterPicker
                .padding(.horizontal)
                .padding(.vertical, 8)
                .background(Color(.systemGroupedBackground))

            Divider()

            // MARK: - Content
            if viewModel.isLoading && viewModel.reminders.isEmpty {
                loadingView
            } else if viewModel.reminders.isEmpty {
                emptyView
            } else {
                remindersList
            }
        }
        .navigationTitle("Reminders")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showingCreateSheet = true
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("Create reminder")
            }
        }
        .sheet(isPresented: $showingCreateSheet) {
            CreateReminderSheet(viewModel: viewModel)
        }
        .task {
            await viewModel.loadReminders()
        }
        .refreshable {
            await viewModel.loadReminders()
        }
    }

    // MARK: - Subviews

    @ViewBuilder
    private var permissionBanner: some View {
        switch viewModel.notificationPermissionStatus {
        case .authorized:
            EmptyView() // Don't clutter UI when already authorized

        case .notDetermined:
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "bell.badge")
                        .font(.title3)
                        .foregroundColor(.blue)

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Enable Notifications")
                            .font(.headline)
                        Text("Aurora needs notification permission to alert you when reminders trigger.")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }
                }

                Button {
                    Task {
                        await viewModel.requestNotificationPermission()
                    }
                } label: {
                    if viewModel.isRequestingPermission {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Text("Allow Notifications")
                            .fontWeight(.semibold)
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
            .padding()
            .background(Color.blue.opacity(0.1))
            .cornerRadius(10)
            .padding(.horizontal)
            .padding(.top, 8)

        case .denied:
            HStack(spacing: 10) {
                Image(systemName: "bell.slash.fill")
                    .foregroundColor(.orange)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Notifications Disabled")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                    Text("Notifications are disabled in Settings. Aurora will save reminders, but local alerts cannot be delivered.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            .padding(10)
            .background(Color.orange.opacity(0.12))
            .cornerRadius(8)
            .padding(.horizontal)
            .padding(.top, 8)

        case .restricted, .unavailable:
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.circle")
                    .foregroundColor(.secondary)

                Text("Notifications are restricted or unavailable on this device.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(10)
            .background(Color(.tertiarySystemGroupedBackground))
            .cornerRadius(8)
            .padding(.horizontal)
            .padding(.top, 8)
        }
    }

    private var filterPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                filterChip(title: "All", status: nil)
                ForEach(ReminderStatus.allCases) { status in
                    filterChip(title: status.displayName, status: status)
                }
            }
        }
    }

    private func filterChip(title: String, status: ReminderStatus?) -> some View {
        let isSelected = viewModel.selectedStatus == status
        return Button {
            viewModel.setStatusFilter(status)
        } label: {
            Text(title)
                .font(.subheadline)
                .fontWeight(isSelected ? .semibold : .regular)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(isSelected ? Color.blue : Color(.secondarySystemBackground))
                .foregroundColor(isSelected ? .white : .primary)
                .clipShape(Capsule())
        }
        .accessibilityLabel("Filter by \(title)")
    }

    private var remindersList: some View {
        List {
            ForEach(viewModel.reminders) { reminder in
                NavigationLink(destination: ReminderDetailView(viewModel: viewModel, reminderId: reminder.id)) {
                    reminderRow(reminder)
                }
                .swipeActions(edge: .leading) {
                    if reminder.status == .scheduled {
                        Button {
                            Task { await viewModel.cancelReminder(id: reminder.id) }
                        } label: {
                            Label("Cancel", systemImage: "bell.slash")
                        }
                        .tint(.orange)
                    }
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button(role: .destructive) {
                        Task { await viewModel.deleteReminder(id: reminder.id) }
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                }
            }

            if viewModel.reminders.count < viewModel.totalCount {
                HStack {
                    Spacer()
                    if viewModel.isLoadingMore {
                        ProgressView()
                    } else {
                        Button("Load More") {
                            Task { await viewModel.loadMore() }
                        }
                        .font(.footnote)
                    }
                    Spacer()
                }
                .listRowSeparator(.hidden)
            }
        }
        .listStyle(.insetGrouped)
    }

    private func reminderRow(_ item: ReminderItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.title)
                    .font(.body)
                    .fontWeight(.medium)
                Spacer()
                statusBadge(item.status)
            }

            if !item.body.isEmpty {
                Text(item.body)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 8) {
                Label(
                    item.scheduledAt.formatted(date: .abbreviated, time: .shortened),
                    systemImage: "clock"
                )
                .font(.caption)
                .foregroundColor(.secondary)

                if let taskId = item.taskId {
                    HStack(spacing: 2) {
                        Image(systemName: "checklist")
                        Text(taskId)
                    }
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color(.tertiarySystemGroupedBackground))
                    .cornerRadius(4)
                }

                if let cmtId = item.commitmentId {
                    HStack(spacing: 2) {
                        Image(systemName: "hourglass")
                        Text(cmtId)
                    }
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color(.tertiarySystemGroupedBackground))
                    .cornerRadius(4)
                }
            }
        }
        .padding(.vertical, 2)
    }

    private func statusBadge(_ status: ReminderStatus) -> some View {
        let (color, text) = switch status {
        case .scheduled:
            (Color.blue, "Scheduled")
        case .delivered:
            (Color.green, "Delivered")
        case .cancelled:
            (Color.orange, "Cancelled")
        case .failed:
            (Color.red, "Failed")
        }

        return Text(text)
            .font(.caption2)
            .fontWeight(.semibold)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundColor(color)
            .clipShape(Capsule())
    }

    private var loadingView: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("Loading reminders...")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyView: some View {
        VStack(spacing: 16) {
            Image(systemName: "bell.slash")
                .font(.system(size: 48))
                .foregroundColor(.secondary)

            Text("No Reminders Found")
                .font(.headline)

            Text("You haven't scheduled any reminders matching the selected filter.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            Button {
                showingCreateSheet = true
            } label: {
                Label("New Reminder", systemImage: "plus")
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

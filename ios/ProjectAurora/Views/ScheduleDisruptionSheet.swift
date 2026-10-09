//
//  ScheduleDisruptionSheet.swift
//  ProjectAurora
//
//  Created for Phase 6K: iOS Schedule Disruption Integration & Recovery Presentation.
//

import SwiftUI

public struct ScheduleDisruptionSheet: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: ScheduleDisruptionViewModel
    @State private var isShowingProposalSheet: Bool = false

    @MainActor
    public init(viewModel: ScheduleDisruptionViewModel? = nil) {
        _viewModel = StateObject(wrappedValue: viewModel ?? ScheduleDisruptionViewModel())
    }

    public init(diagnostic: ScheduleDisruptionDiagnostic) {
        _viewModel = StateObject(wrappedValue: ScheduleDisruptionViewModel(initialDiagnostic: diagnostic))
    }

    public var body: some View {
        NavigationStack {
            ZStack {
                Color(.systemGroupedBackground).ignoresSafeArea()

                switch viewModel.state {
                case .idle:
                    idleView
                case .loading:
                    loadingView
                case .loaded(let diagnostic):
                    loadedContentView(diagnostic: diagnostic)
                case .empty:
                    emptyGoodStandingView
                case .taskDomainUnavailable:
                    taskDomainUnavailableView
                case .calendarUnavailable:
                    calendarUnavailableView
                case .staleCalendar:
                    staleCalendarView
                case .universeSaturated:
                    universeSaturatedView
                case .failed(let message):
                    failedView(message: message)
                }
            }
            .navigationTitle("Schedule Disruption")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Close") {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task {
                            await viewModel.refresh()
                        }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("Refresh Diagnostic")
                }
            }
            .sheet(item: Binding<ScheduleRecoveryProposal?>(
                get: { viewModel.proposedProposal },
                set: { _ in viewModel.dismissProposal() }
            )) { proposal in
                ScheduleRecoveryProposalSheet(proposal: proposal)
            }
        }
    }

    // MARK: - Idle State (Explicit User Trigger)

    private var idleView: some View {
        VStack(spacing: 20) {
            Image(systemName: "gauge.with.needle")
                .font(.system(size: 56))
                .foregroundColor(.accentColor)

            Text("Schedule Diagnostic")
                .font(.title2.bold())

            Text("Analyze working hours capacity, detect calendar conflicts, and review tasks in jeopardy.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            Button {
                Task {
                    await viewModel.diagnose()
                }
            } label: {
                HStack {
                    Image(systemName: "waveform.path.badge.magnifyingglass")
                    Text("Check Schedule Disruption")
                }
                .fontWeight(.semibold)
                .padding(.horizontal, 24)
                .padding(.vertical, 12)
                .background(Color.accentColor)
                .foregroundColor(.white)
                .cornerRadius(10)
            }
            .padding(.top, 8)
        }
        .padding()
    }

    // MARK: - Loading State

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.3)
            Text("Evaluating Schedule Disruption...")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
    }

    // MARK: - Loaded Content View

    private func loadedContentView(diagnostic: ScheduleDisruptionDiagnostic) -> some View {
        ScrollView {
            VStack(spacing: 16) {
                // 1. Disruption Severity Banner
                severityBanner(diagnostic: diagnostic)

                // 2. Capacity Deficit Card
                capacityDeficitCard(diagnostic: diagnostic)

                // 3. Omitted Domains Notice (if any)
                if !diagnostic.omittedDomains.isEmpty {
                    omittedDomainsNotice(omitted: diagnostic.omittedDomains)
                }

                // 4. Tasks in Jeopardy Section
                jeopardyTasksSection(diagnostic: diagnostic)

                // 5. Candidate Reminders Section (if any)
                if !diagnostic.recommendedRecoveryReminderIds.isEmpty {
                    candidateRemindersSection(diagnostic: diagnostic)
                }

                // 6. Action Footer
                actionFooter(diagnostic: diagnostic)
            }
            .padding(16)
        }
    }

    // MARK: - 1. Severity Banner

    private func severityBanner(diagnostic: ScheduleDisruptionDiagnostic) -> some View {
        let (color, icon, title, description) = bannerAttributes(severity: diagnostic.severity, diagnostic: diagnostic)

        return HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.title2)
                .foregroundColor(color)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(title)
                        .font(.headline)
                        .foregroundColor(color)
                    Spacer()
                    Text(diagnostic.severity.rawValue.uppercased())
                        .font(.caption2.bold())
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(color.opacity(0.15))
                        .foregroundColor(color)
                        .cornerRadius(4)
                }

                Text(description)
                    .font(.subheadline)
                    .foregroundColor(.primary)

                if diagnostic.overdueTaskCount > 0 {
                    Text("\(diagnostic.overdueTaskCount) overdue task(s) requiring immediate attention.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding(14)
        .background(color.opacity(0.08))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(color.opacity(0.25), lineWidth: 1)
        )
        .cornerRadius(12)
    }

    private func bannerAttributes(
        severity: ScheduleDisruptionSeverity,
        diagnostic: ScheduleDisruptionDiagnostic
    ) -> (Color, String, String, String) {
        switch severity {
        case .critical:
            return (
                .red,
                "exclamationmark.octagon.fill",
                "Critical Schedule Disruption",
                "Focus capacity deficit of \(diagnostic.focusCapacityDeficitMinutes)m exceeds available time."
            )
        case .moderate:
            return (
                .orange,
                "exclamationmark.triangle.fill",
                "Moderate Disruption",
                "Schedule has \(diagnostic.focusCapacityDeficitMinutes)m deficit across remaining working hours."
            )
        case .minor:
            return (
                .yellow,
                "info.circle.fill",
                "Minor Schedule Drift",
                "Minor scheduling pressure detected with \(diagnostic.jeopardyTasks.count) task(s) in jeopardy."
            )
        case .none:
            return (
                .green,
                "checkmark.circle.fill",
                "Schedule Balanced",
                "Sufficient working hours available for all planned workload."
            )
        }
    }

    // MARK: - 2. Capacity Deficit Card

    private func capacityDeficitCard(diagnostic: ScheduleDisruptionDiagnostic) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Focus Capacity Analysis")
                    .font(.headline)
                Spacer()
                Text("Timezone: \(diagnostic.timezone)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Divider()

            HStack(spacing: 12) {
                metricColumn(title: "Working Hours", value: "\(diagnostic.totalWorkingMinutes)m", color: .secondary)
                metricColumn(title: "Available", value: "\(diagnostic.availableWorkingMinutes)m", color: .blue)
                metricColumn(title: "Required", value: "\(diagnostic.requiredTaskMinutes)m", color: .purple)
                metricColumn(
                    title: "Deficit",
                    value: "\(diagnostic.focusCapacityDeficitMinutes)m",
                    color: diagnostic.focusCapacityDeficitMinutes > 0 ? .red : .green
                )
            }

            // Visual Capacity vs Workload Bar
            VStack(alignment: .leading, spacing: 4) {
                GeometryReader { geo in
                    let total = max(1, diagnostic.totalWorkingMinutes)
                    let availRatio = min(1.0, max(0.0, Double(diagnostic.availableWorkingMinutes) / Double(total)))
                    let reqRatio = min(1.0, max(0.0, Double(diagnostic.requiredTaskMinutes) / Double(total)))

                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color(.systemGray5))
                            .frame(height: 10)

                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color.blue.opacity(0.7))
                            .frame(width: geo.size.width * availRatio, height: 10)

                        if diagnostic.requiredTaskMinutes > diagnostic.availableWorkingMinutes {
                            RoundedRectangle(cornerRadius: 4)
                                .stroke(Color.red, lineWidth: 2)
                                .frame(width: min(geo.size.width, geo.size.width * reqRatio), height: 10)
                        }
                    }
                }
                .frame(height: 10)

                HStack {
                    Text("Blue: Available Focus")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    Spacer()
                    if diagnostic.focusCapacityDeficitMinutes > 0 {
                        Text("Deficit: \(diagnostic.focusCapacityDeficitMinutes)m")
                            .font(.caption2.bold())
                            .foregroundColor(.red)
                    }
                }
            }
            .padding(.top, 4)
        }
        .padding(14)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }

    private func metricColumn(title: String, value: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundColor(.secondary)
            Text(value)
                .font(.subheadline.bold())
                .foregroundColor(color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - 3. Omitted Domains Notice

    private func omittedDomainsNotice(omitted: [String]) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.circle")
                .foregroundColor(.secondary)
            Text("Omitted domains: \(omitted.joined(separator: ", ")). Metrics computed without external calendar/commitments.")
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
        }
        .padding(10)
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }

    // MARK: - 4. Jeopardy Tasks Section

    private func jeopardyTasksSection(diagnostic: ScheduleDisruptionDiagnostic) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Tasks in Jeopardy (\(diagnostic.jeopardyTasks.count))")
                    .font(.headline)
                Spacer()
                Text("\(viewModel.selectedTaskIds.count) selected")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if diagnostic.jeopardyTasks.isEmpty {
                Text("No tasks currently at risk.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .padding(.vertical, 8)
            } else {
                ForEach(diagnostic.jeopardyTasks) { task in
                    jeopardyTaskRow(task: task, diagnostic: diagnostic)
                }
            }
        }
        .padding(14)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }

    private func jeopardyTaskRow(task: TaskJeopardyItem, diagnostic: ScheduleDisruptionDiagnostic) -> some View {
        let isSelected = viewModel.selectedTaskIds.contains(task.taskId)
        let isRecommended = diagnostic.recommendedRecoveryTaskIds.contains(task.taskId)

        return Button {
            viewModel.toggleTaskSelection(task.taskId)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundColor(isSelected ? .accentColor : .secondary)
                    .padding(.top, 2)

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(task.title)
                            .font(.subheadline.bold())
                            .foregroundColor(.primary)
                            .multilineTextAlignment(.leading)
                        Spacer()
                        Text("\(task.estimatedDurationMinutes)m")
                            .font(.caption2.bold())
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color(.systemGray5))
                            .cornerRadius(4)
                    }

                    // Reasons Chips
                    DisruptionFlowLayout(spacing: 4) {
                        ForEach(task.jeopardyReasons, id: \.rawValue) { reason in
                            reasonChip(reason: reason)
                        }
                        if isRecommended {
                            Text("Recommended")
                                .font(.caption2.bold())
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.accentColor.opacity(0.15))
                                .foregroundColor(.accentColor)
                                .cornerRadius(4)
                        }
                    }

                    // Due date & commitment target
                    HStack(spacing: 8) {
                        if let due = task.currentDueAt {
                            Text("Due: \(formatDate(due, timezone: diagnostic.timezone))")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                        if let target = task.commitmentTargetDate {
                            Text("Commitment Target: \(target)")
                                .font(.caption2)
                                .foregroundColor(.purple)
                        }
                    }
                }
            }
            .padding(10)
            .background(isSelected ? Color.accentColor.opacity(0.06) : Color(.systemBackground))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(isSelected ? Color.accentColor : Color.clear, lineWidth: 1)
            )
            .cornerRadius(8)
        }
        .buttonStyle(.plain)
    }

    private func reasonChip(reason: TaskJeopardyReason) -> some View {
        let (label, color): (String, Color) = {
            switch reason {
            case .overdue: return ("Overdue", .red)
            case .deadlineDuringEvent: return ("Event Clash", .orange)
            case .commitmentOverrunRisk: return ("Commitment Risk", .purple)
            case .workingHoursOverflow: return ("Hours Overflow", .blue)
            case .unscheduledWithTarget: return ("Unscheduled Target", .yellow)
            }
        }()

        return Text(label)
            .font(.caption2)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundColor(color)
            .cornerRadius(4)
    }

    // MARK: - 5. Candidate Reminders Section

    private func candidateRemindersSection(diagnostic: ScheduleDisruptionDiagnostic) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Candidate Reminders (\(diagnostic.recommendedRecoveryReminderIds.count))")
                    .font(.headline)
                Spacer()
                Text("\(viewModel.selectedReminderIds.count) selected")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            ForEach(diagnostic.recommendedRecoveryReminderIds, id: \.self) { reminderId in
                let isSelected = viewModel.selectedReminderIds.contains(reminderId)
                Button {
                    viewModel.toggleReminderSelection(reminderId)
                } label: {
                    HStack {
                        Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                            .foregroundColor(isSelected ? .accentColor : .secondary)
                        Text("Reminder: \(reminderId)")
                            .font(.subheadline)
                            .foregroundColor(.primary)
                        Spacer()
                    }
                    .padding(10)
                    .background(isSelected ? Color.accentColor.opacity(0.06) : Color(.systemBackground))
                    .cornerRadius(8)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(14)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }

    // MARK: - 6. Action Footer Bar

    private func actionFooter(diagnostic: ScheduleDisruptionDiagnostic) -> some View {
        VStack(spacing: 10) {
            HStack {
                Button("Select Recommended") {
                    viewModel.selectAllRecommended()
                }
                .font(.subheadline)

                Spacer()

                Button("Clear Selection") {
                    viewModel.clearSelection()
                }
                .font(.subheadline)
                .foregroundColor(.secondary)
            }
            .padding(.horizontal, 4)

            Button {
                Task {
                    _ = try? await viewModel.proposeRecovery()
                }
            } label: {
                HStack {
                    if viewModel.isProposingRecovery {
                        ProgressView()
                            .tint(.white)
                            .padding(.trailing, 4)
                    } else {
                        Image(systemName: "arrow.triangle.2.circlepath.circle.fill")
                    }
                    Text("Propose Schedule Recovery (\(viewModel.selectedTaskIds.count) selected)")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(viewModel.isRecoveryActionEnabled ? Color.accentColor : Color.gray.opacity(0.4))
                .foregroundColor(.white)
                .cornerRadius(12)
            }
            .disabled(!viewModel.isRecoveryActionEnabled || viewModel.isProposingRecovery)

            if !viewModel.isRecoveryActionEnabled {
                Text("Select at least 1 task to propose recovery.")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            if let err = viewModel.proposalErrorMessage {
                Text(err)
                    .font(.caption)
                    .foregroundColor(.red)
            }
        }
        .padding(.top, 8)
    }

    // MARK: - Error & Empty States

    private var emptyGoodStandingView: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 56))
                .foregroundColor(.green)
            Text("Schedule in Good Standing")
                .font(.title3.bold())
            Text("No schedule disruption detected. All tasks fit comfortably within available working hours.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .padding()
    }

    private var taskDomainUnavailableView: some View {
        errorGuidanceCard(
            icon: "lock.shield",
            title: "Task Access Required",
            description: "Aurora needs permission to inspect your tasks in order to diagnose schedule disruptions.",
            actionTitle: "Retry",
            onAction: { Task { await viewModel.retry() } }
        )
    }

    private var calendarUnavailableView: some View {
        errorGuidanceCard(
            icon: "calendar.badge.exclamationmark",
            title: "Calendar Snapshot Unavailable",
            description: "No current calendar snapshot is available to evaluate schedule conflicts.",
            actionTitle: "Retry",
            onAction: { Task { await viewModel.retry() } }
        )
    }

    private var staleCalendarView: some View {
        errorGuidanceCard(
            icon: "clock.badge.exclamationmark",
            title: "Calendar Snapshot Stale",
            description: "The calendar snapshot is older than 24 hours. Refresh your calendar to re-evaluate.",
            actionTitle: "Retry",
            onAction: { Task { await viewModel.retry() } }
        )
    }

    private var universeSaturatedView: some View {
        errorGuidanceCard(
            icon: "tray.full.fill",
            title: "Task Backlog Saturated",
            description: "Your active task list exceeds the 500-task limit. Please archive completed or stale items.",
            actionTitle: "Retry",
            onAction: { Task { await viewModel.retry() } }
        )
    }

    private func failedView(message: String) -> some View {
        errorGuidanceCard(
            icon: "exclamationmark.triangle",
            title: "Diagnosis Failed",
            description: message,
            actionTitle: "Retry",
            onAction: { Task { await viewModel.retry() } }
        )
    }

    private func errorGuidanceCard(
        icon: String,
        title: String,
        description: String,
        actionTitle: String,
        onAction: @escaping () -> Void
    ) -> some View {
        VStack(spacing: 16) {
            Image(systemName: icon)
                .font(.system(size: 48))
                .foregroundColor(.secondary)
            Text(title)
                .font(.headline)
            Text(description)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button(action: onAction) {
                Text(actionTitle)
                    .fontWeight(.semibold)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 10)
                    .background(Color.accentColor)
                    .foregroundColor(.white)
                    .cornerRadius(8)
            }
        }
        .padding()
    }

    // MARK: - Helpers

    private func formatDate(_ date: Date, timezone: String) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, HH:mm"
        if let tz = TimeZone(identifier: timezone) {
            formatter.timeZone = tz
        }
        return formatter.string(from: date)
    }
}

// Simple FlowLayout for reason chips
private struct DisruptionFlowLayout: Layout {
    var spacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        var height: CGFloat = 0
        var x: CGFloat = 0
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > width && x > 0 {
                x = 0
                height += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        height += rowHeight
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX && x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

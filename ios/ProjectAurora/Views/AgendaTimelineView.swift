import SwiftUI

public struct AgendaTimelineView: View {
    @StateObject private var viewModel: AgendaViewModel
    @StateObject private var weeklyViewModel: WeeklyHorizonViewModel
    @StateObject private var eventBriefingViewModel: EventBriefingViewModel
    @StateObject private var recoveryViewModel: ScheduleRecoveryViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var isShowingCoordinationSheet: Bool = false
    @State private var isShowingDisruptionSheet: Bool = false
    @State private var isShowingDayPlanSheet: Bool = false
    @State private var isShowingEveningReviewSheet: Bool = false
    @State private var isShowingWeeklyHorizonSheet: Bool = false
    @State private var selectedEventForBriefing: AgendaItemPayload?
    @State private var selectedEventForDebrief: AgendaItemPayload?

    @MainActor
    public init(
        viewModel: AgendaViewModel? = nil,
        weeklyViewModel: WeeklyHorizonViewModel? = nil,
        eventBriefingViewModel: EventBriefingViewModel? = nil,
        recoveryViewModel: ScheduleRecoveryViewModel? = nil
    ) {
        _viewModel = StateObject(wrappedValue: viewModel ?? AgendaViewModel())
        _weeklyViewModel = StateObject(wrappedValue: weeklyViewModel ?? WeeklyHorizonViewModel())
        _eventBriefingViewModel = StateObject(wrappedValue: eventBriefingViewModel ?? EventBriefingViewModel())
        _recoveryViewModel = StateObject(wrappedValue: recoveryViewModel ?? ScheduleRecoveryViewModel())
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // MARK: - Date Navigation Header
                dateNavigationHeader
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                    .background(Color(.secondarySystemBackground))

                Divider()

                // MARK: - Content Body
                if viewModel.isLoading && viewModel.agenda == nil {
                    loadingView
                } else if let err = viewModel.errorMessage, viewModel.agenda == nil {
                    errorView(message: err)
                } else if let agenda = viewModel.agenda {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 16) {
                            // Conflict Banners
                            if !agenda.conflicts.isEmpty {
                                conflictsSection(conflicts: agenda.conflicts)
                            }

                            // Diurnal Horizon Planning Quick Actions
                            diurnalHorizonActionsSection

                            // Section 1: All-Day & Target Horizons
                            let horizonItems = agenda.items.filter { $0.temporalKind == .dateTarget || $0.isAllDay }
                            if !horizonItems.isEmpty {
                                horizonsSection(items: horizonItems)
                            }

                            // Section 2: Chronological Timeline
                            let timelineItems = agenda.items.filter { $0.temporalKind != .dateTarget && !$0.isAllDay }
                            timelineSection(items: timelineItems, freeSlots: agenda.freeSlots)

                            // Omitted Sources Footer
                            if !agenda.omittedSources.isEmpty {
                                omittedSourcesFooter(sources: agenda.omittedSources)
                            }
                        }
                        .padding()
                    }
                    .refreshable {
                        await viewModel.loadDailyAgenda()
                    }
                } else {
                    emptyAgendaView
                }
            }
            .navigationTitle("Daily Agenda")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    HStack(spacing: 12) {
                        Button {
                            isShowingCoordinationSheet = true
                        } label: {
                            Label("Coordinate", systemImage: "arrow.triangle.swap")
                        }
                        Button {
                            isShowingDisruptionSheet = true
                        } label: {
                            Label("Disruption", systemImage: "gauge.with.needle")
                        }
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    HStack(spacing: 8) {
                        Menu {
                            Button {
                                isShowingWeeklyHorizonSheet = true
                            } label: {
                                Label("Weekly Horizon", systemImage: "calendar.badge.clock")
                            }
                            Divider()
                            Button {
                                isShowingDayPlanSheet = true
                            } label: {
                                Label("Plan Day", systemImage: "sun.max.fill")
                            }
                            Button {
                                isShowingEveningReviewSheet = true
                            } label: {
                                Label("Evening Review", systemImage: "moon.stars.fill")
                            }
                        } label: {
                            Image(systemName: "sparkles")
                        }
                        .accessibilityLabel("Planning Horizons")

                        Button("Done") {
                            dismiss()
                        }
                    }
                }
            }
            .sheet(isPresented: $isShowingCoordinationSheet) {
                ScheduleCoordinationSheet(targetDate: viewModel.selectedDate)
            }
            .sheet(isPresented: $isShowingDisruptionSheet) {
                ScheduleDisruptionSheet()
            }
            .sheet(isPresented: $isShowingDayPlanSheet) {
                DayPlanSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $isShowingEveningReviewSheet) {
                EveningReviewSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $isShowingWeeklyHorizonSheet) {
                WeeklyHorizonView(viewModel: weeklyViewModel)
            }
            .sheet(item: $selectedEventForBriefing) { event in
                EventBriefingSheet(
                    viewModel: eventBriefingViewModel,
                    eventId: event.sourceEntityId,
                    eventTitle: event.displayTitle
                )
            }
            .sheet(item: $selectedEventForDebrief) { event in
                EventDebriefSheet(
                    viewModel: eventBriefingViewModel,
                    eventId: event.sourceEntityId,
                    eventTitle: event.displayTitle
                )
            }
            .task {
                await viewModel.loadDailyAgenda()
            }
        }
    }

    // MARK: - Diurnal & Strategic Horizon Actions

    private var diurnalHorizonActionsSection: some View {
        VStack(spacing: 8) {
            Button {
                isShowingWeeklyHorizonSheet = true
            } label: {
                HStack {
                    Image(systemName: "calendar.badge.clock")
                        .foregroundColor(.blue)
                    Text("Weekly Strategic Horizon")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity)
                .padding(10)
                .background(Color.blue.opacity(0.08))
                .cornerRadius(8)
            }

            HStack(spacing: 12) {
                Button {
                    isShowingDayPlanSheet = true
                } label: {
                    HStack {
                        Image(systemName: "sun.max.fill")
                            .foregroundColor(.orange)
                        Text("Plan Day")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(10)
                    .background(Color.orange.opacity(0.12))
                    .cornerRadius(8)
                }

                Button {
                    isShowingEveningReviewSheet = true
                } label: {
                    HStack {
                        Image(systemName: "moon.stars.fill")
                            .foregroundColor(.purple)
                        Text("Evening Review")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(10)
                    .background(Color.purple.opacity(0.12))
                    .cornerRadius(8)
                }
            }
        }
    }

    // MARK: - Date Navigation Header

    private var dateNavigationHeader: some View {
        HStack {
            Button {
                viewModel.previousDay()
            } label: {
                Image(systemName: "chevron.left")
                    .padding(8)
            }
            .accessibilityLabel("Previous Day")

            Spacer()

            DatePicker(
                "",
                selection: $viewModel.selectedDate,
                displayedComponents: [.date]
            )
            .labelsHidden()

            Button("Today") {
                viewModel.goToToday()
            }
            .font(.caption)
            .buttonStyle(.bordered)

            Spacer()

            Button {
                viewModel.nextDay()
            } label: {
                Image(systemName: "chevron.right")
                    .padding(8)
            }
            .accessibilityLabel("Next Day")
        }
    }

    // MARK: - Conflict Banners

    private func conflictsSection(conflicts: [AgendaConflictPayload]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(conflicts) { conflict in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: conflict.severity == "warning" ? "exclamationmark.triangle.fill" : "info.circle.fill")
                        .foregroundColor(conflict.severity == "warning" ? .orange : .blue)
                        .padding(.top, 2)

                    VStack(alignment: .leading, spacing: 4) {
                        Text(conflict.description)
                            .font(.subheadline)
                            .fontWeight(.medium)

                        if let suggestion = conflict.suggestedAction {
                            Text(suggestion)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    Spacer()
                }
                .padding(10)
                .background(conflict.severity == "warning" ? Color.orange.opacity(0.12) : Color.blue.opacity(0.12))
                .cornerRadius(8)
            }

            Button {
                isShowingCoordinationSheet = true
            } label: {
                HStack {
                    Image(systemName: "wand.and.stars")
                    Text("Resolve Conflicts & Coordinate")
                    Spacer()
                    Image(systemName: "chevron.right")
                }
                .font(.subheadline.bold())
                .foregroundColor(.accentColor)
                .padding(10)
                .background(Color.accentColor.opacity(0.1))
                .cornerRadius(8)
            }

            Button {
                isShowingDisruptionSheet = true
            } label: {
                HStack {
                    Image(systemName: "gauge.with.needle")
                    Text("Check Schedule Disruption")
                    Spacer()
                    Image(systemName: "chevron.right")
                }
                .font(.subheadline.bold())
                .foregroundColor(.orange)
                .padding(10)
                .background(Color.orange.opacity(0.1))
                .cornerRadius(8)
            }
        }
    }

    // MARK: - Section 1: All-Day & Target Horizons

    private func horizonsSection(items: [AgendaItemPayload]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("All-Day & Target Horizons")
                .font(.footnote)
                .fontWeight(.bold)
                .foregroundColor(.secondary)
                .textCase(.uppercase)

            ForEach(items) { item in
                HStack(spacing: 12) {
                    if item.temporalKind == .dateTarget {
                        Image(systemName: "hourglass")
                            .foregroundColor(.purple)
                            .font(.title3)
                    } else {
                        Image(systemName: "sun.max.fill")
                            .foregroundColor(.orange)
                            .font(.title3)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.displayTitle)
                            .font(.subheadline)
                            .fontWeight(.medium)

                        HStack(spacing: 6) {
                            if item.temporalKind == .dateTarget {
                                Text("Goal Target")
                                    .font(.caption2)
                                    .foregroundColor(.purple)
                            } else {
                                Text("All Day Event")
                                    .font(.caption2)
                                    .foregroundColor(.orange)
                            }

                            if let prio = item.priority {
                                Text("• [\(prio.uppercased())]")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }

                    Spacer()

                    if item.sourceDomain == "calendar" {
                        Menu {
                            Button {
                                selectedEventForBriefing = item
                            } label: {
                                Label("View Briefing", systemImage: "sparkles")
                            }

                            Button {
                                selectedEventForDebrief = item
                            } label: {
                                Label("Debrief Event", systemImage: "square.and.pencil")
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                                .font(.title3)
                                .foregroundColor(.accentColor)
                        }
                    }
                }
                .padding(10)
                .background(Color(.secondarySystemGroupedBackground))
                .cornerRadius(8)
            }
        }
    }

    // MARK: - Section 2: Chronological Timeline

    private func timelineSection(items: [AgendaItemPayload], freeSlots: [FreeSlotPayload]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Chronological Timeline")
                .font(.footnote)
                .fontWeight(.bold)
                .foregroundColor(.secondary)
                .textCase(.uppercase)

            if items.isEmpty && freeSlots.isEmpty {
                Text("No scheduled items or events for this date.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .padding(.vertical, 8)
            } else {
                ForEach(items) { item in
                    timelineItemRow(item: item)
                }

                if !freeSlots.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Available Working Gaps (Proposal Only)")
                            .font(.caption)
                            .fontWeight(.semibold)
                            .foregroundColor(.secondary)

                        ForEach(freeSlots) { slot in
                            HStack {
                                Image(systemName: "clock.badge.checkmark")
                                    .foregroundColor(.green)

                                Text("\(formatTime(slot.startAt)) - \(formatTime(slot.endAt))")
                                    .font(.subheadline)
                                    .fontWeight(.medium)

                                Spacer()

                                Text("\(slot.durationMinutes) min free")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                            .padding(10)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .strokeBorder(Color.green.opacity(0.4), style: StrokeStyle(lineWidth: 1, dash: [4]))
                            )
                        }
                    }
                    .padding(.top, 8)
                }
            }
        }
    }

    private func timelineItemRow(item: AgendaItemPayload) -> some View {
        HStack(alignment: .center, spacing: 12) {
            // Icon according to source domain
            Group {
                switch item.sourceDomain {
                case "calendar":
                    Image(systemName: "calendar")
                        .foregroundColor(.blue)
                case "tasks":
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.indigo)
                case "reminders":
                    Image(systemName: "bell.fill")
                        .foregroundColor(.orange)
                default:
                    Image(systemName: "circle")
                        .foregroundColor(.gray)
                }
            }
            .font(.title3)
            .frame(width: 24)

            // Details
            VStack(alignment: .leading, spacing: 2) {
                Text(item.displayTitle)
                    .font(.subheadline)
                    .fontWeight(.medium)

                HStack(spacing: 6) {
                    if item.temporalKind == .interval, let s = item.startAt, let e = item.endAt {
                        Text("\(formatTime(s)) - \(formatTime(e))")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } else if item.temporalKind == .instant, let inst = item.instantAt {
                        Text("Due/Alert: \(formatTime(inst))")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    if !item.conflictIds.isEmpty {
                        Text("• Conflict Detected")
                            .font(.caption2)
                            .foregroundColor(.orange)
                            .fontWeight(.bold)
                    }
                }
            }

            Spacer()

            if let prio = item.priority {
                Text(prio.uppercased())
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color(.systemGray5))
                    .cornerRadius(4)
            }

            if item.sourceDomain == "calendar" {
                Menu {
                    Button {
                        selectedEventForBriefing = item
                    } label: {
                        Label("View Briefing", systemImage: "sparkles")
                    }

                    Button {
                        selectedEventForDebrief = item
                    } label: {
                        Label("Debrief Event", systemImage: "square.and.pencil")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .font(.title3)
                        .foregroundColor(.accentColor)
                        .padding(.leading, 4)
                }
            }
        }
        .padding(10)
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(8)
    }

    // MARK: - Omitted Sources Footer

    private func omittedSourcesFooter(sources: [OmittedSourcePayload]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(sources, id: \.domain) { src in
                Text("Domain '\(src.domain)' omitted: \(src.reason)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.top, 8)
    }

    // MARK: - State Views

    private var loadingView: some View {
        VStack(spacing: 12) {
            Spacer()
            ProgressView("Loading agenda...")
            Spacer()
        }
    }

    private func errorView(message: String) -> some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(.largeTitle)
                .foregroundColor(.orange)
            Text(message)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button("Retry") {
                Task {
                    await viewModel.loadDailyAgenda()
                }
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
    }

    private var emptyAgendaView: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "calendar.badge.clock")
                .font(.system(size: 48))
                .foregroundColor(.secondary)
            Text("No agenda data available.")
                .font(.headline)
            Text("Pull down to refresh.")
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
        }
    }

    private func formatTime(_ isoString: String) -> String {
        // Simple substring extraction if valid ISO-8601 (e.g. 2026-09-28T14:30:00Z -> 14:30)
        if let tIndex = isoString.firstIndex(of: "T") {
            let afterT = isoString[isoString.index(after: tIndex)...]
            let parts = afterT.split(separator: ":")
            if parts.count >= 2 {
                return "\(parts[0]):\(parts[1])"
            }
        }
        return isoString
    }
}

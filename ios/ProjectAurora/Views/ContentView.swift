import SwiftUI

public struct ContentView: View {
    @StateObject private var viewModel: AssistantViewModel
    @StateObject private var taskViewModel: TaskViewModel
    @StateObject private var commitmentViewModel: CommitmentViewModel
    @ObservedObject private var deepLinkNavigator = DeepLinkNavigator.shared

    @State private var navigateToTasks = false
    @State private var navigateToCommitments = false
    @State private var navigateToReminders = false
    @State private var navigateToAgenda = false
    @State private var showSetupSheet = false

    public init() {
        let tasks = TaskViewModel()
        let commitments = CommitmentViewModel()
        _taskViewModel = StateObject(wrappedValue: tasks)
        _commitmentViewModel = StateObject(wrappedValue: commitments)
        _viewModel = StateObject(wrappedValue: AssistantViewModel(taskViewModel: tasks, commitmentViewModel: commitments))
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // MARK: - Status Bar & Active Session Pill
                statusBar
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                    .background(Color(.systemGroupedBackground))

                Divider()

                // MARK: - Connection Error Banner (if backend unreachable)
                if case .offline(let reason) = viewModel.connectionState {
                    connectionErrorBanner(reason: reason)
                }

                // MARK: - Conversation Feed / Empty State
                if viewModel.messages.isEmpty && viewModel.pendingConfirmation == nil {
                    emptyStateView
                } else {
                    messageListView
                }

                // MARK: - Tool Confirmation Card (if paused for authorization)
                if let tool = viewModel.pendingConfirmation {
                    toolConfirmationCard(tool: tool)
                }

                // MARK: - Error Banner (if any)
                if let err = viewModel.errorMessage {
                    errorBanner(message: err)
                }

                // MARK: - Agent Activity Indicator (during multi-step execution)
                if viewModel.isSending && !viewModel.currentStepActivities.isEmpty {
                    agentActivityView
                }

                // MARK: - Voice Interaction Capsule (during active voice session)
                if viewModel.voiceState != .idle {
                    VoiceInteractionBarView(coordinator: viewModel.voiceCoordinator) {
                        Task { await viewModel.stopVoiceSession() }
                    }
                }

                Divider()

                // MARK: - Message Input Bar
                messageInputBar
                    .padding(.horizontal)
                    .padding(.vertical, 10)
                    .background(Color(.secondarySystemBackground))
            }
            .navigationTitle("Aurora")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        viewModel.startNewConversation()
                    } label: {
                        Label("New Chat", systemImage: "square.and.pencil")
                    }
                    .accessibilityLabel("Start new conversation")
                }

                ToolbarItem(placement: .navigationBarTrailing) {
                    HStack(spacing: 14) {
                        NavigationLink(destination: TaskListView(viewModel: taskViewModel)) {
                            Image(systemName: "checklist")
                        }
                        .accessibilityLabel("Open tasks")

                        NavigationLink(destination: CommitmentListView(viewModel: commitmentViewModel)) {
                            Image(systemName: "hourglass")
                        }
                        .accessibilityLabel("Open commitments")

                        NavigationLink(destination: ReminderListView()) {
                            Image(systemName: "bell")
                        }
                        .accessibilityLabel("Open reminders")

                        NavigationLink(destination: MemoryListView()) {
                            Image(systemName: "brain.head.profile")
                        }
                        .accessibilityLabel("Open memories")

                        NavigationLink(destination: LocationStatusView()) {
                            Image(systemName: "location")
                        }
                        .accessibilityLabel("Open location context")

                        NavigationLink(destination: AgendaTimelineView()) {
                            Image(systemName: "calendar.day.timeline.left")
                        }
                        .accessibilityLabel("Open daily agenda")

                        NavigationLink(destination: CalendarStatusView()) {
                            Image(systemName: "calendar")
                        }
                        .accessibilityLabel("Open calendar schedule")

                        NavigationLink(destination: ContactsStatusView()) {
                            Image(systemName: "person.crop.circle")
                        }
                        .accessibilityLabel("Open contacts")

                        NavigationLink(destination: MediaSelectionView()) {
                            Image(systemName: "photo.on.rectangle")
                        }
                        .accessibilityLabel("Open media selection")

                        NavigationLink(destination: ProactiveView(taskViewModel: taskViewModel, commitmentViewModel: commitmentViewModel)) {
                            Image(systemName: "sparkles")
                        }
                        .accessibilityLabel("Open proactive suggestions")

                        Button {
                            Task { await viewModel.checkConnection() }
                        } label: {

                            Image(systemName: "arrow.clockwise")
                        }
                        .accessibilityLabel("Refresh server status")

                        Button {
                            showSetupSheet = true
                        } label: {
                            Image(systemName: "key.horizontal")
                        }
                        .accessibilityLabel("Reprovision this installation")
                    }
                }
            }
            .task {
                await viewModel.checkConnection()
            }
            .sheet(isPresented: $showSetupSheet) {
                IdentitySetupView {
                    showSetupSheet = false
                    viewModel.dismissError()
                    Task { await viewModel.checkConnection() }
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .auroraAuthenticationRequired)) { _ in
                viewModel.errorMessage = "Aurora authentication is required. Please provision this installation."
            }
            .navigationDestination(isPresented: $navigateToTasks) {
                TaskListView(viewModel: taskViewModel)
            }
            .navigationDestination(isPresented: $navigateToCommitments) {
                CommitmentListView(viewModel: commitmentViewModel)
            }
            .navigationDestination(isPresented: $navigateToReminders) {
                ReminderListView()
            }
            .navigationDestination(isPresented: $navigateToAgenda) {
                AgendaTimelineView()
            }
            .onReceive(deepLinkNavigator.$activeRoute) { route in
                guard let route = route else { return }
                handleRoute(route)
            }
        }
    }

    private func handleRoute(_ route: AuroraRoute) {
        switch route {
        case .voice:
            if viewModel.voiceState == .idle {
                Task { await viewModel.startVoiceSession() }
            }
        case .chat(let prompt):
            viewModel.currentInput = prompt
        case .agenda:
            navigateToAgenda = true
        case .tasks:
            navigateToTasks = true
        case .commitments:
            navigateToCommitments = true
        case .reminders:
            navigateToReminders = true
        case .confirmation:
            // The confirmation ID is a navigation locator hint only.
            // Server remains authoritative; pendingConfirmation is already displayed in the UI if present.
            // Do not approve, reject, or execute.
            break
        }
        deepLinkNavigator.clearRoute()
    }

    // MARK: - Subviews

    private var statusBar: some View {
        HStack(spacing: 8) {
            switch viewModel.connectionState {
            case .checking:
                ProgressView()
                    .controlSize(.small)
                Text("Connecting...")
                    .font(.caption)
                    .foregroundColor(.secondary)

            case .online(let provider, let model, let latency):
                Circle()
                    .fill(Color.green)
                    .frame(width: 8, height: 8)
                Text("Online")
                    .font(.caption.bold())
                    .foregroundColor(.green)
                Text("• \(provider) (\(model)) • \(latency)ms")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)

            case .offline(let reason):
                Circle()
                    .fill(Color.red)
                    .frame(width: 8, height: 8)
                Text("Offline")
                    .font(.caption.bold())
                    .foregroundColor(.red)
                Text("— \(reason)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }

            Spacer()

            if let convId = viewModel.conversationId {
                Text("Session: \(String(convId.prefix(6)))…")
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color(.tertiarySystemFill))
                    .clipShape(Capsule())
            }
        }
    }

    private var emptyStateView: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "sparkles")
                .font(.system(size: 44))
                .foregroundColor(.accentColor)

            Text("How can I help you today?")
                .font(.title3.bold())

            Text("Aurora can reason about requests, use tools (time, safe math, reminders), and ask for your permission before executing actions.")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
                .padding(.horizontal, 36)

            VStack(spacing: 10) {
                samplePromptButton("What is the current time in Tokyo?")
                samplePromptButton("Calculate: (250 * 12) + (450 / 3)")
                samplePromptButton("Remind me to call the dentist tomorrow at 10 AM")
            }
            .padding(.top, 8)

            Spacer()
        }
    }

    private func samplePromptButton(_ prompt: String) -> some View {
        Button {
            viewModel.currentInput = prompt
            Task {
                await viewModel.sendMessage()
            }
        } label: {
            HStack {
                Text(prompt)
                    .font(.footnote)
                    .multilineTextAlignment(.leading)
                    .foregroundColor(.primary)
                Spacer()
                Image(systemName: "arrow.up.circle")
                    .foregroundColor(.accentColor)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .padding(.horizontal, 24)
    }

    private var messageListView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 14) {
                    ForEach(viewModel.messages) { msg in
                        messageBubble(for: msg)
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.messages.count) { _ in
                if let last = viewModel.messages.last {
                    withAnimation {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    private func messageBubble(for message: ChatMessageItem) -> some View {
        HStack(alignment: .top, spacing: 8) {
            if message.role == .user {
                Spacer(minLength: 40)
                Text(message.content)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Color.blue)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            } else if message.role == .assistant {
                Image(systemName: "sparkle")
                    .font(.system(size: 14))
                    .foregroundColor(.accentColor)
                    .padding(.top, 8)

                VStack(alignment: .leading, spacing: 6) {
                    if let activities = message.stepActivities, !activities.isEmpty {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(activities, id: \.self) { act in
                                HStack(spacing: 4) {
                                    Image(systemName: "checkmark.circle.fill")
                                        .font(.system(size: 9))
                                        .foregroundColor(.green)
                                    Text(act)
                                        .font(.system(size: 11, weight: .medium))
                                        .foregroundColor(.secondary)
                                }
                            }
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(Color(.tertiarySystemFill))
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }

                    Text(message.content)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(Color(.secondarySystemFill))
                        .foregroundColor(.primary)
                        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

                    // Phase 5K.4: Citation source badges — server-authoritative metadata only.
                    // iOS renders these as tappable links opening in Safari.
                    // No embedded browser, WebView, JavaScript, cookies, or session management.
                    if let citations = message.citations, !citations.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Sources")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundColor(.secondary)
                                .padding(.leading, 2)
                            ForEach(citations) { citation in
                                if let url = URL(string: citation.url) {
                                    Link(destination: url) {
                                        HStack(spacing: 6) {
                                            Image(systemName: "globe")
                                                .font(.system(size: 10))
                                                .foregroundColor(.accentColor)
                                            VStack(alignment: .leading, spacing: 1) {
                                                Text(citation.domain ?? citation.url)
                                                    .font(.system(size: 11, weight: .medium))
                                                    .foregroundColor(.accentColor)
                                                    .lineLimit(1)
                                                if let title = citation.title {
                                                    Text(title)
                                                        .font(.system(size: 10))
                                                        .foregroundColor(.secondary)
                                                        .lineLimit(1)
                                                }
                                            }
                                            Spacer()
                                            Image(systemName: "arrow.up.right")
                                                .font(.system(size: 9))
                                                .foregroundColor(.secondary)
                                        }
                                        .padding(.horizontal, 10)
                                        .padding(.vertical, 6)
                                        .background(Color(.tertiarySystemFill))
                                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                                    }
                                }
                            }
                        }
                        .padding(.top, 2)
                    }
                }
                Spacer(minLength: 40)

            } else {
                Text(message.content)
                    .font(.caption)
                    .italic()
                    .foregroundColor(.secondary)
                    .padding(8)
            }
        }
        .id(message.id)
    }

    private func toolConfirmationCard(tool: ToolConfirmationDetails) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "shield.lefthalf.filled")
                    .foregroundColor(.orange)
                Text("Action Authorization Required")
                    .font(.subheadline.bold())
                Spacer()
                Text(tool.riskLevel.uppercased())
                    .font(.caption2.bold())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.orange.opacity(0.2))
                    .foregroundColor(.orange)
                    .clipShape(Capsule())
            }

            Text("Aurora wants to execute **\(tool.name)**:")
                .font(.footnote)

            if !tool.arguments.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(tool.arguments.keys.sorted()), id: \.self) { key in
                        if let val = tool.arguments[key] {
                            HStack {
                                Text("\(key):")
                                    .font(.caption.bold())
                                    .foregroundColor(.secondary)
                                Text(val.displayString)
                                    .font(.caption)
                            }
                        }
                    }
                }
                .padding(8)
                .background(Color(.tertiarySystemFill))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            HStack(spacing: 12) {
                Button {
                    Task { await viewModel.respondToConfirmation(approved: true) }
                } label: {
                    Label("Approve Action", systemImage: "checkmark.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)
                .controlSize(.regular)

                Button {
                    Task { await viewModel.respondToConfirmation(approved: false) }
                } label: {
                    Label("Reject", systemImage: "xmark.circle")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.red)
                .controlSize(.regular)
            }
            .padding(.top, 4)

            if viewModel.voiceState != .idle {
                HStack(spacing: 6) {
                    Image(systemName: "waveform")
                        .foregroundColor(.orange)
                        .font(.caption)
                    Text("Listening for 'Yes' or 'No'...")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                .padding(.top, 2)
            }
        }
        .padding(14)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.orange.opacity(0.4), lineWidth: 1)
        )
        .padding(.horizontal)
        .padding(.vertical, 6)
    }

    private func errorBanner(message: String) -> some View {
        let isAuthError = message.localizedCaseInsensitiveContains("authentication") || message.localizedCaseInsensitiveContains("provision")
        return HStack(spacing: 8) {
            Image(systemName: isAuthError ? "lock.trianglebadge.exclamationmark.fill" : "exclamationmark.triangle.fill")
                .foregroundColor(isAuthError ? .red : .yellow)
            Text(message)
                .font(.footnote)
                .foregroundColor(.primary)
                .lineLimit(2)
            Spacer()
            if isAuthError {
                Button("Connect") {
                    showSetupSheet = true
                }
                .font(.caption.bold())
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
            Button {
                viewModel.dismissError()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.secondary)
            }
        }
        .padding(10)
        .background((isAuthError ? Color.red : Color.yellow).opacity(0.15))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .padding(.horizontal)
        .padding(.vertical, 4)
    }

    private func connectionErrorBanner(reason: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "wifi.slash")
                .foregroundColor(.red)
            VStack(alignment: .leading, spacing: 2) {
                Text("Backend Unreachable")
                    .font(.caption.bold())
                    .foregroundColor(.primary)
                Text(reason)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Button {
                Task { await viewModel.checkConnection() }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise")
                    Text("Retry")
                }
            }
            .font(.caption.bold())
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(10)
        .background(Color.red.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .padding(.horizontal)
        .padding(.vertical, 4)
    }

    private var messageInputBar: some View {
        HStack(spacing: 8) {
            Button {
                Task {
                    if viewModel.voiceState == .idle {
                        await viewModel.startVoiceSession()
                    } else {
                        await viewModel.stopVoiceSession()
                    }
                }
            } label: {
                Image(systemName: viewModel.voiceState == .idle ? "mic.circle" : "mic.circle.fill")
                    .font(.title2)
                    .foregroundColor(viewModel.voiceState == .idle ? .secondary : .blue)
            }
            .disabled(viewModel.isSending && viewModel.voiceState == .idle)

            TextField("Message Aurora...", text: $viewModel.currentInput)
                .textFieldStyle(.roundedBorder)
                .disabled(viewModel.pendingConfirmation != nil)
                .onSubmit {
                    Task { await viewModel.sendMessage() }
                }

            if viewModel.isSending {
                ProgressView()
                    .controlSize(.small)
                    .padding(.horizontal, 8)
            } else {
                Button {
                    Task { await viewModel.sendMessage() }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(viewModel.currentInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.pendingConfirmation != nil)
            }
        }
    }

    private var agentActivityView: some View {
        HStack(spacing: 6) {
            ProgressView()
                .controlSize(.mini)
            if let lastActivity = viewModel.currentStepActivities.last {
                Text(lastActivity)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            } else {
                Text("Thinking...")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 4)
    }
}

import Foundation
import Combine

public enum ConnectionState: Equatable {
    case checking
    case online(provider: String, model: String, latencyMs: Int)
    case offline(reason: String)
}

@MainActor
public final class AssistantViewModel: ObservableObject {
    @Published public var connectionState: ConnectionState = .checking
    @Published public var conversationId: String? = nil
    @Published public var messages: [ChatMessageItem] = []
    @Published public var currentInput: String = ""
    @Published public var isSending: Bool = false
    @Published public var errorMessage: String? = nil
    @Published public var serverInfo: HealthResponse? = nil

    // Tool confirmation state
    @Published public var pendingConfirmation: ToolConfirmationDetails? = nil
    @Published public var pendingConfirmationId: String? = nil

    // Agent loop state
    @Published public var activeExecutionId: String? = nil
    @Published public var currentStepActivities: [String] = []

    // Voice session state
    @Published public var voiceState: VoiceSessionState = .idle
    public let voiceCoordinator: VoiceSessionCoordinator

    private let apiClient: AuroraAPIClient
    private let notificationProvider: NotificationProviderProtocol
    public weak var reminderViewModel: ReminderViewModel?
    public weak var taskViewModel: TaskViewModel?
    public weak var commitmentViewModel: CommitmentViewModel?

    public init(
        apiClient: AuroraAPIClient = AuroraAPIClient(),
        notificationProvider: NotificationProviderProtocol = UNNotificationProvider(),
        reminderViewModel: ReminderViewModel? = nil,
        taskViewModel: TaskViewModel? = nil,
        commitmentViewModel: CommitmentViewModel? = nil,
        voiceCoordinator: VoiceSessionCoordinator? = nil
    ) {
        self.apiClient = apiClient
        self.notificationProvider = notificationProvider
        self.reminderViewModel = reminderViewModel
        self.taskViewModel = taskViewModel
        self.commitmentViewModel = commitmentViewModel
        let coordinator = voiceCoordinator ?? VoiceSessionCoordinator()
        self.voiceCoordinator = coordinator
        coordinator.delegate = self
    }

    /// Check connection to backend and fetch active provider & model info
    public func checkConnection() async {
        connectionState = .checking
        let startTime = Date()

        // 1. If Keychain has no credential, attempt automatic personal-development bootstrap
        if KeychainCredentialStore.shared.credential == nil {
            do {
                _ = try await apiClient.devBootstrap()
            } catch {
                // If bootstrap fails (e.g. backend unreachable or not in development), continue to health check
            }
        }

        do {
            let health = try await apiClient.fetchHealth()
            let latency = Int(Date().timeIntervalSince(startTime) * 1000)
            self.serverInfo = health
            self.connectionState = .online(
                provider: health.activeProvider,
                model: health.activeModel,
                latencyMs: latency
            )
        } catch {
            self.serverInfo = nil
            self.connectionState = .offline(reason: error.localizedDescription)
        }
    }

    /// Reset conversation session and start fresh
    public func startNewConversation() {
        self.conversationId = nil
        self.messages.removeAll()
        self.pendingConfirmation = nil
        self.pendingConfirmationId = nil
        self.activeExecutionId = nil
        self.currentStepActivities = []
        self.errorMessage = nil
    }

    /// Clear currently active error message
    public func dismissError() {
        self.errorMessage = nil
    }

    /// Start interactive voice session
    public func startVoiceSession() async {
        guard !isSending else { return }
        await voiceCoordinator.startSession()
    }

    /// Stop active voice session
    public func stopVoiceSession() async {
        await voiceCoordinator.stopSession()
    }

    /// Send a message within the current conversation session
    public func sendMessage(prompt: String? = nil) async {
        let textToSend = prompt ?? currentInput
        let trimmed = textToSend.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isSending else { return }

        // If user typed a text message while assistant is speaking in a voice session:
        if prompt == nil, case .speaking = voiceCoordinator.state {
            voiceCoordinator.stopSpeaking()
        }

        // Optimistically record user message
        let userMessage = ChatMessageItem(role: .user, content: trimmed)
        messages.append(userMessage)
        if prompt == nil {
            currentInput = ""
        }
        isSending = true
        errorMessage = nil
        currentStepActivities = []

        let capturedSessionGen = voiceCoordinator.activeGeneration
        let capturedTurnGen = voiceCoordinator.activeTurnGeneration

        // If credentials absent, attempt bootstrap before sending
        if KeychainCredentialStore.shared.credential == nil {
            do {
                _ = try await apiClient.devBootstrap()
            } catch {
                // If bootstrap fails, proceeding will trigger standard authenticationRequired error
            }
        }

        do {
            let response = try await apiClient.sendChatMessage(
                message: trimmed,
                conversationId: self.conversationId
            )
            // Synchronize active conversation session ID and execution metadata
            self.conversationId = response.conversationId
            self.activeExecutionId = response.executionId
            self.currentStepActivities = response.stepActivities ?? []

            if response.type == "confirmation_required", let tool = response.tool, let confId = response.confirmationId {
                self.pendingConfirmation = tool
                self.pendingConfirmationId = confId
                if prompt != nil && capturedSessionGen == voiceCoordinator.activeGeneration && capturedTurnGen == voiceCoordinator.activeTurnGeneration {
                    let confirmPrompt: String
                    if let rawMsg = response.message?.content, !rawMsg.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        confirmPrompt = TTSTextSanitizer.sanitizeForSpeech(rawMsg)
                    } else {
                        confirmPrompt = "Aurora needs your confirmation to execute \(tool.name). Would you like to proceed or cancel?"
                    }
                    await voiceCoordinator.speakResponse(confirmPrompt)
                }
            } else if let msg = response.message {
                let assistantMessage = ChatMessageItem(
                    role: .assistant,
                    content: msg.content,
                    stepActivities: response.stepActivities,
                    citations: response.citations
                )
                messages.append(assistantMessage)
                if prompt != nil && capturedSessionGen == voiceCoordinator.activeGeneration && capturedTurnGen == voiceCoordinator.activeTurnGeneration {
                    let sanitized = TTSTextSanitizer.sanitizeForSpeech(msg.content)
                    await voiceCoordinator.speakResponse(sanitized)
                }
            }

            // Phase 6B: Process assistant-emitted client actions
            if let actions = response.clientActions, !actions.isEmpty {
                await processClientActions(actions)
            }
        } catch {
            let desc = error.localizedDescription
            if desc.contains("409") || desc.contains("active execution") {
                self.errorMessage = "An action is currently pending confirmation or processing. Please confirm or cancel the action before sending new messages."
            } else {
                self.errorMessage = desc
            }
            if prompt != nil && capturedSessionGen == voiceCoordinator.activeGeneration && capturedTurnGen == voiceCoordinator.activeTurnGeneration {
                let errorSpeech = TTSTextSanitizer.sanitizeForSpeech(self.errorMessage ?? desc)
                await voiceCoordinator.speakResponse(errorSpeech)
            }
        }

        isSending = false
    }

    /// Respond to a pending tool confirmation (Approve or Reject)
    public func respondToConfirmation(approved: Bool) async {
        guard let convId = self.conversationId, let confId = self.pendingConfirmationId else { return }
        guard let executionId = activeExecutionId,
              !executionId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            errorMessage = "This confirmation is missing its execution ID. Refresh or restart the conversation, then request the action again."
            return
        }

        isSending = true
        errorMessage = nil

        let capturedSessionGen = voiceCoordinator.activeGeneration
        let capturedTurnGen = voiceCoordinator.activeTurnGeneration

        do {
            let response = try await apiClient.confirmAction(
                conversationId: convId,
                confirmationId: confId,
                executionId: executionId,
                approved: approved
            )

            // Clear pending confirmation
            self.pendingConfirmation = nil
            self.pendingConfirmationId = nil
            self.activeExecutionId = response.executionId
            self.currentStepActivities = response.stepActivities ?? []

            if let msg = response.message {
                let assistantMessage = ChatMessageItem(
                    role: .assistant,
                    content: msg.content,
                    stepActivities: response.stepActivities,
                    citations: response.citations
                )
                messages.append(assistantMessage)

                if capturedSessionGen == voiceCoordinator.activeGeneration && capturedTurnGen == voiceCoordinator.activeTurnGeneration && voiceState != .idle {
                    let sanitized = TTSTextSanitizer.sanitizeForSpeech(msg.content)
                    await voiceCoordinator.speakResponse(sanitized)
                }
            }

            // Phase 6B: Process assistant-emitted client actions on approved resume
            if let actions = response.clientActions, !actions.isEmpty {
                await processClientActions(actions)
            }
        } catch {
            let desc = error.localizedDescription
            if desc.contains("409") || desc.contains("active execution") {
                self.errorMessage = "The pending action has already been resolved or expired."
                self.pendingConfirmation = nil
                self.pendingConfirmationId = nil
                if capturedSessionGen == voiceCoordinator.activeGeneration && capturedTurnGen == voiceCoordinator.activeTurnGeneration && voiceState != .idle {
                    await voiceCoordinator.speakResponse("The pending action has already been resolved or expired.")
                }
            } else {
                self.errorMessage = desc
                if capturedSessionGen == voiceCoordinator.activeGeneration && capturedTurnGen == voiceCoordinator.activeTurnGeneration && voiceState != .idle {
                    let errorSpeech = TTSTextSanitizer.sanitizeForSpeech(desc)
                    await voiceCoordinator.speakResponse(errorSpeech)
                }
            }
        }

        isSending = false
    }

    // MARK: - Phase 6B Client Actions Processing

    /// Process assistant-emitted client actions for local notifications
    public func processClientActions(_ actions: [ClientActionPayload]) async {
        let isoFractional = ISO8601DateFormatter()
        isoFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let isoStandard = ISO8601DateFormatter()
        isoStandard.formatOptions = [.withInternetDateTime]

        for action in actions {
            if action.action.contains("task") || action.taskId != nil {
                taskViewModel?.applyAssistantAction(action)
                continue
            }
            if action.action.contains("commitment") || action.commitmentId != nil {
                commitmentViewModel?.applyAssistantAction(action)
                continue
            }
            guard let reminderId = action.reminderId,
                  let notificationId = action.notificationId,
                  let title = action.title,
                  let body = action.body,
                  let scheduledAt = action.scheduledAt,
                  let schedDate = isoFractional.date(from: scheduledAt) ?? isoStandard.date(from: scheduledAt),
                  let updateDate = isoFractional.date(from: action.updatedAt) ?? isoStandard.date(from: action.updatedAt) else { continue }
            let notifId = notificationId.isEmpty ? reminderId : notificationId

            // Stale check
            if let existing = reminderViewModel?.reminders.first(where: { $0.id == reminderId }) {
                if existing.updatedAt > updateDate {
                    print("[AssistantViewModel] Stale client action ignored for \(reminderId)")
                    continue
                }
            }

            switch action.action {
            case "schedule_local_notification":
                do {
                    try await notificationProvider.schedule(
                        id: notifId,
                        title: title,
                        body: body,
                        scheduledAt: schedDate
                    )
                } catch {
                    print("[AssistantViewModel] Failed to schedule local notification: \(error)")
                }
            case "reschedule_local_notification":
                await notificationProvider.cancel(ids: [notifId])
                do {
                    try await notificationProvider.schedule(
                        id: notifId,
                        title: title,
                        body: body,
                        scheduledAt: schedDate
                    )
                } catch {
                    print("[AssistantViewModel] Failed to reschedule local notification: \(error)")
                }
            case "cancel_local_notification":
                await notificationProvider.cancel(ids: [notifId])
            default:
                break
            }

            // Patch ReminderViewModel
            if let rvm = reminderViewModel {
                if let idx = rvm.reminders.firstIndex(where: { $0.id == reminderId }) {
                    var item = rvm.reminders[idx]
                    if item.updatedAt <= updateDate {
                        item.title = title
                        item.body = body
                        item.scheduledAt = schedDate
                        item.updatedAt = updateDate
                        if action.action == "cancel_local_notification" {
                            item.status = .cancelled
                        }
                        rvm.reminders[idx] = item
                    }
                }
            }
        }
    }
}

// MARK: - VoiceSessionCoordinatorDelegate
extension AssistantViewModel: VoiceSessionCoordinatorDelegate {
    public func voiceSessionDidUpdateState(_ state: VoiceSessionState) {
        self.voiceState = state
    }

    public func voiceSessionDidFinalizeTranscript(_ text: String) async {
        if pendingConfirmation != nil {
            let intent = VoiceConfirmationInterpreter.interpret(
                transcript: text,
                hasActiveConfirmation: true
            )
            switch intent {
            case .approve:
                await respondToConfirmation(approved: true)
            case .reject:
                await respondToConfirmation(approved: false)
            case .ambiguous:
                await voiceCoordinator.speakResponse("Please say yes to approve or no to cancel.")
            case .noActiveConfirmation:
                await sendMessage(prompt: text)
            }
        } else {
            await sendMessage(prompt: text)
        }
    }
}

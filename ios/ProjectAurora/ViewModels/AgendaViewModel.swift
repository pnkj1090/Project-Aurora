import Foundation
import Combine

extension Notification.Name {
    public static let auroraAgendaChanged = Notification.Name("AuroraAgendaChanged")
}

@MainActor
public final class AgendaViewModel: ObservableObject {
    @Published public var selectedDate: Date = Date() {
        didSet {
            Task {
                await loadDailyAgenda()
            }
        }
    }
    @Published public var agenda: DailyAgendaResponsePayload? = nil
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    // MARK: - Phase 6M Diurnal Horizon Presentation State

    @Published public var diurnalSheetState: DiurnalHorizonSheetState = .idle
    @Published public var activeDayPlan: DailyExecutivePlanPayload? = nil
    @Published public var activeDayPlanProposal: DayPlanProposalPayload? = nil
    @Published public var activeEveningReview: EveningHorizonReviewPayload? = nil
    @Published public var activeEveningReviewProposal: EveningReviewProposalPayload? = nil

    // In-flight mutation task tracking: ViewModel-owned so sheet dismissal does not cancel mutation
    private var activeMutationTask: Task<Void, Never>? = nil

    private let apiClient: AuroraAPIClient
    private var cancellables = Set<AnyCancellable>()

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone.current
        return f
    }()

    public init(apiClient: AuroraAPIClient = AuroraAPIClient()) {
        self.apiClient = apiClient

        NotificationCenter.default.publisher(for: .auroraAgendaChanged)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in
                Task { [weak self] in
                    await self?.loadDailyAgenda()
                }
            }
            .store(in: &cancellables)
    }

    /// Fetches the daily agenda for the currently selected date.
    public func loadDailyAgenda() async {
        isLoading = true
        errorMessage = nil

        let dateString = Self.dateFormatter.string(from: selectedDate)
        let timezoneIdentifier = TimeZone.current.identifier

        do {
            let result = try await apiClient.fetchDailyAgenda(
                date: dateString,
                timezone: timezoneIdentifier
            )
            self.agenda = result
        } catch {
            self.errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    public func nextDay() {
        if let next = Calendar.current.date(byAdding: .day, value: 1, to: selectedDate) {
            selectedDate = next
        }
    }

    public func previousDay() {
        if let prev = Calendar.current.date(byAdding: .day, value: -1, to: selectedDate) {
            selectedDate = prev
        }
    }

    public func goToToday() {
        selectedDate = Date()
    }

    // MARK: - Phase 6M: Day Plan Workflow

    /// Loads read-only daily executive morning synthesis.
    public func loadDailyExecutivePlan(targetDate: Date? = nil) async {
        let dateToUse = targetDate ?? selectedDate
        let dateString = Self.dateFormatter.string(from: dateToUse)
        let tzIdentifier = TimeZone.current.identifier

        diurnalSheetState = .loadingSynthesis
        do {
            let plan = try await apiClient.getDailyExecutivePlan(
                targetDate: dateString,
                explicitTz: tzIdentifier
            )
            activeDayPlan = plan
            activeDayPlanProposal = nil
            diurnalSheetState = .synthesisLoaded
        } catch {
            diurnalSheetState = .failedPresentation(message: error.localizedDescription)
        }
    }

    /// Proposes a server-held DayPlanProposal from current synthesis.
    public func createDayPlanProposal(selectedFocusTitle: String? = nil, userNotes: String? = nil) async {
        guard case .synthesisLoaded = diurnalSheetState, let plan = activeDayPlan else { return }
        diurnalSheetState = .generatingProposal

        let req = ProposeDayPlanRequestPayload(
            targetDate: plan.targetDate,
            timezone: TimeZone.current.identifier,
            selectedFocusTitle: selectedFocusTitle,
            userNotes: userNotes
        )

        do {
            let proposal = try await apiClient.proposeDayPlan(payload: req)
            activeDayPlanProposal = proposal
            diurnalSheetState = .proposalReady
        } catch {
            diurnalSheetState = .failedPresentation(message: error.localizedDescription)
        }
    }

    /// Executes or aborts the active DayPlanProposal.
    /// Mutation task is owned by ViewModel and persists across sheet dismissal.
    /// Server proposal.status is authoritative; HTTP 200 does NOT imply COMPLETED.
    @discardableResult
    public func applyActiveDayPlan(approved: Bool) -> Task<Void, Never>? {
        guard case .proposalReady = diurnalSheetState, let proposal = activeDayPlanProposal else { return nil }
        let proposalId = proposal.proposalId

        if approved {
            diurnalSheetState = .applying(proposalId: proposalId)
        } else {
            diurnalSheetState = .aborting(proposalId: proposalId)
        }

        let task = Task { [weak self] in
            guard let self = self else { return }
            do {
                let resultProposal = try await self.apiClient.applyDayPlan(proposalId: proposalId, approved: approved)
                self.activeDayPlanProposal = resultProposal

                // Server status is authoritative: inspect proposal.status
                switch resultProposal.status {
                case .completed:
                    self.diurnalSheetState = .completedPresentation
                case .partiallyExecuted:
                    self.diurnalSheetState = .partiallyExecutedPresentation
                case .failed:
                    self.diurnalSheetState = .proposalFailedPresentation(
                        failureReason: resultProposal.failureReason,
                        operations: resultProposal.orderedOperations
                    )
                case .aborted:
                    self.diurnalSheetState = .abortedPresentation
                default:
                    self.diurnalSheetState = .failedPresentation(message: "Unexpected server proposal status: \(resultProposal.status.rawValue)")
                }
            } catch let apiErr as APIError {
                switch apiErr {
                case .networkError:
                    // Transport failure / timeout: contain uncertainty safely
                    self.diurnalSheetState = .mutationOutcomeUnknown(proposalId: proposalId)
                case .conflict(let code, let msg):
                    self.diurnalSheetState = .failedPresentation(message: "Conflict (\(code)): \(msg)")
                default:
                    self.diurnalSheetState = .failedPresentation(message: apiErr.localizedDescription)
                }
            } catch {
                let nsError = error as NSError
                if nsError.domain == NSURLErrorDomain {
                    self.diurnalSheetState = .mutationOutcomeUnknown(proposalId: proposalId)
                } else {
                    self.diurnalSheetState = .failedPresentation(message: error.localizedDescription)
                }
            }
        }
        activeMutationTask = task
        return task
    }

    // MARK: - Phase 6M: Evening Review Workflow

    /// Loads read-only evening review synthesis.
    public func loadEveningHorizonReview(targetDate: Date? = nil) async {
        let dateToUse = targetDate ?? selectedDate
        let dateString = Self.dateFormatter.string(from: dateToUse)
        let tzIdentifier = TimeZone.current.identifier

        diurnalSheetState = .loadingSynthesis
        do {
            let review = try await apiClient.getEveningHorizonReview(
                targetDate: dateString,
                explicitTz: tzIdentifier
            )
            activeEveningReview = review
            activeEveningReviewProposal = nil
            diurnalSheetState = .synthesisLoaded
        } catch {
            diurnalSheetState = .failedPresentation(message: error.localizedDescription)
        }
    }

    /// Proposes a server-held EveningReviewProposal from current synthesis.
    public func createEveningReviewProposal(rolloverTaskIds: [String]? = nil, reflectionNotes: String? = nil) async {
        guard case .synthesisLoaded = diurnalSheetState, let review = activeEveningReview else { return }
        diurnalSheetState = .generatingProposal

        let req = ProposeEveningReviewRequestPayload(
            targetDate: review.targetDate,
            timezone: TimeZone.current.identifier,
            rolloverTaskIds: rolloverTaskIds,
            reflectionNotes: reflectionNotes
        )

        do {
            let proposal = try await apiClient.proposeEveningReview(payload: req)
            activeEveningReviewProposal = proposal
            diurnalSheetState = .proposalReady
        } catch {
            diurnalSheetState = .failedPresentation(message: error.localizedDescription)
        }
    }

    /// Executes or aborts the active EveningReviewProposal.
    /// Mutation task is owned by ViewModel and persists across sheet dismissal.
    /// Server proposal.status is authoritative; HTTP 200 does NOT imply COMPLETED.
    @discardableResult
    public func applyActiveEveningReview(approved: Bool) -> Task<Void, Never>? {
        guard case .proposalReady = diurnalSheetState, let proposal = activeEveningReviewProposal else { return nil }
        let proposalId = proposal.proposalId

        if approved {
            diurnalSheetState = .applying(proposalId: proposalId)
        } else {
            diurnalSheetState = .aborting(proposalId: proposalId)
        }

        let task = Task { [weak self] in
            guard let self = self else { return }
            do {
                let resultProposal = try await self.apiClient.applyEveningReview(proposalId: proposalId, approved: approved)
                self.activeEveningReviewProposal = resultProposal

                // Server status is authoritative: inspect proposal.status
                switch resultProposal.status {
                case .completed:
                    self.diurnalSheetState = .completedPresentation
                case .partiallyExecuted:
                    self.diurnalSheetState = .partiallyExecutedPresentation
                case .failed:
                    self.diurnalSheetState = .proposalFailedPresentation(
                        failureReason: resultProposal.failureReason,
                        operations: resultProposal.orderedOperations
                    )
                case .aborted:
                    self.diurnalSheetState = .abortedPresentation
                default:
                    self.diurnalSheetState = .failedPresentation(message: "Unexpected server proposal status: \(resultProposal.status.rawValue)")
                }
            } catch let apiErr as APIError {
                switch apiErr {
                case .networkError:
                    self.diurnalSheetState = .mutationOutcomeUnknown(proposalId: proposalId)
                case .conflict(let code, let msg):
                    self.diurnalSheetState = .failedPresentation(message: "Conflict (\(code)): \(msg)")
                default:
                    self.diurnalSheetState = .failedPresentation(message: apiErr.localizedDescription)
                }
            } catch {
                let nsError = error as NSError
                if nsError.domain == NSURLErrorDomain {
                    self.diurnalSheetState = .mutationOutcomeUnknown(proposalId: proposalId)
                } else {
                    self.diurnalSheetState = .failedPresentation(message: error.localizedDescription)
                }
            }
        }
        activeMutationTask = task
        return task
    }

    // MARK: - Recovery & Navigation Helpers

    /// "Start Fresh": discards unconfirmed local presentation state and requests fresh synthesis.
    /// Does NOT retry previous mutation and does NOT automatically abort.
    public func startFreshDayPlan() async {
        activeDayPlanProposal = nil
        await loadDailyExecutivePlan()
    }

    /// "Start Fresh": discards unconfirmed local presentation state and requests fresh synthesis.
    /// Does NOT retry previous mutation and does NOT automatically abort.
    public func startFreshEveningReview() async {
        activeEveningReviewProposal = nil
        await loadEveningHorizonReview()
    }

    /// Trigger recovery from an unresolved or failed state.
    public func triggerRecovery(isDayPlan: Bool) async {
        if isDayPlan {
            await startFreshDayPlan()
        } else {
            await startFreshEveningReview()
        }
    }

    /// Resets diurnal sheet presentation state to idle.
    public func resetDiurnalState() {
        diurnalSheetState = .idle
        activeDayPlan = nil
        activeDayPlanProposal = nil
        activeEveningReview = nil
        activeEveningReviewProposal = nil
    }
}

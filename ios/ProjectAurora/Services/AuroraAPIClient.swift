import Foundation

public enum APIError: LocalizedError, Equatable {
    case invalidURL
    case authenticationRequired
    case unauthorized
    case missingExecutionCorrelation
    case networkError(String)
    case serverError(statusCode: Int, message: String)
    case preconditionFailed(code: String, message: String)
    case conflict(code: String, message: String)
    case scheduleRecoveryConflict(ScheduleRecoveryErrorDetail)
    case decodingError(String)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "The request URL is invalid."
        case .authenticationRequired, .unauthorized:
            return "Aurora authentication is required. Please provision this installation again."
        case .missingExecutionCorrelation:
            return "This confirmation is missing its execution ID. Refresh or restart the conversation, then request the action again."
        case .networkError(let details):
            return "Network error: \(details)"
        case .serverError(let code, let message):
            return "Server error (\(code)): \(message)"
        case .preconditionFailed(let code, let message):
            return "Precondition failed (\(code)): \(message)"
        case .conflict(let code, let message):
            return "Conflict (\(code)): \(message)"
        case .scheduleRecoveryConflict(let detail):
            return "Conflict (\(detail.error)): \(detail.message)"
        case .decodingError(let details):
            return "Failed to decode response: \(details)"
        }
    }
}

public actor AuroraAPIClient {
    private let baseURL: URL
    private let session: URLSession
    private let credentialProvider: @Sendable () -> String?

    public init(
        baseURL: URL = AppConfig.backendBaseURL,
        session: URLSession = .shared,
        credentialProvider: (@Sendable () -> String?)? = nil
    ) {
        self.baseURL = baseURL
        self.session = session
        self.credentialProvider = credentialProvider ?? { KeychainCredentialStore.shared.credential }
    }

    private func addBearerCredential(to request: inout URLRequest) throws {
        guard let credential = credentialProvider(), !credential.isEmpty else {
            throw APIError.authenticationRequired
        }
        request.setValue("Bearer \(credential)", forHTTPHeaderField: "Authorization")
    }

    private func checkAuthenticationResponse(_ response: HTTPURLResponse) throws {
        guard response.statusCode == 401 else { return }
        KeychainCredentialStore.shared.clear()
        NotificationCenter.default.post(name: .auroraAuthenticationRequired, object: nil)
        throw APIError.authenticationRequired
    }

    private func performRequest(_ originalRequest: URLRequest) async throws -> (Data, URLResponse) {
        var request = originalRequest
        let path = request.url?.path ?? ""
        let protected = path == "/api/v1/tasks" || path.hasPrefix("/api/v1/tasks/")
            || path == "/api/v1/commitments" || path.hasPrefix("/api/v1/commitments/")
            || path == "/api/v1/chat" || path.hasPrefix("/api/v1/chat/")
            || path.hasPrefix("/api/v1/conversations/")
            || path == "/api/v1/agenda" || path.hasPrefix("/api/v1/agenda/")
            || path.hasPrefix("/api/v1/schedule/recovery")
            || (path.hasPrefix("/api/v1/proactive/candidates/") && path.hasSuffix("/accept"))
        if protected { try addBearerCredential(to: &request) }
        let result = try await session.data(for: request)
        if protected, let response = result.1 as? HTTPURLResponse {
            try checkAuthenticationResponse(response)
        }
        return result
    }

    public func provisionInstallation(secret: String) async throws -> ProvisionResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/auth/provision")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(ProvisionRequest(provisioningSecret: secret))
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else { throw APIError.networkError("Invalid HTTP response") }
        guard (200...299).contains(httpResponse.statusCode) else {
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: "Provisioning failed. Check the one-time secret and try again.")
        }
        do {
            let result = try JSONDecoder().decode(ProvisionResponse.self, from: data)
            try KeychainCredentialStore.shared.save(result.credential)
            return result
        } catch let error as APIError { throw error }
        catch { throw APIError.decodingError(error.localizedDescription) }
    }

    /// Automated personal development bootstrap: requests server to issue and redeem a credential for local loopback.
    public func devBootstrap() async throws -> ProvisionResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/auth/dev-bootstrap")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Development bootstrap failed"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }
        do {
            let result = try JSONDecoder().decode(ProvisionResponse.self, from: data)
            try KeychainCredentialStore.shared.save(result.credential)
            return result
        } catch let error as APIError { throw error }
        catch { throw APIError.decodingError(error.localizedDescription) }
    }

    /// Health check verifying server connectivity, provider, and active model.
    public func fetchHealth() async throws -> HealthResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/health")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Unknown server error"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            let decoder = JSONDecoder()
            return try decoder.decode(HealthResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Send a message within a conversation session to the backend Conversation Engine.
    public func sendChatMessage(message: String, conversationId: String? = nil) async throws -> ChatResponsePayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/chat")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 45.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let payload = ChatRequestPayload(message: message, conversationId: conversationId)
        request.httpBody = try JSONEncoder().encode(payload)

        let (data, response) = try await performRequest(request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw APIError.serverError(statusCode: httpResponse.statusCode, message: detail)
            }
            let rawMessage = String(data: data, encoding: .utf8) ?? "Unknown server error"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: rawMessage)
        }

        do {
            let decoder = JSONDecoder()
            return try decoder.decode(ChatResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Approve or reject an action paused for user confirmation.
    public func confirmAction(conversationId: String, confirmationId: String, executionId: String, approved: Bool) async throws -> ChatResponsePayload {
        guard !executionId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw APIError.missingExecutionCorrelation
        }
        let endpoint = baseURL.appendingPathComponent("api/v1/chat/confirm")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 45.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let payload = ConfirmActionPayload(
            conversationId: conversationId,
            confirmationId: confirmationId,
            executionId: executionId,
            approved: approved
        )
        request.httpBody = try JSONEncoder().encode(payload)

        let (data, response) = try await performRequest(request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw APIError.serverError(statusCode: httpResponse.statusCode, message: detail)
            }
            let rawMessage = String(data: data, encoding: .utf8) ?? "Unknown server error"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: rawMessage)
        }

        do {
            let decoder = JSONDecoder()
            return try decoder.decode(ChatResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Compatibility entry point for older callers that do not have server-issued execution correlation.
    /// It intentionally fails locally and never sends an uncorrelated confirmation request.
    @available(*, deprecated, message: "Pass the server-issued executionId to confirmAction.")
    public func confirmAction(conversationId: String, confirmationId: String, approved: Bool) async throws -> ChatResponsePayload {
        throw APIError.missingExecutionCorrelation
    }

    // MARK: - Memory API

    private func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { d in
            let container = try d.singleValueContainer()
            let str = try container.decode(String.self)
            let isoFractional = ISO8601DateFormatter()
            isoFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            let isoStandard = ISO8601DateFormatter()
            isoStandard.formatOptions = [.withInternetDateTime]
            if let date = isoFractional.date(from: str) ?? isoStandard.date(from: str) {
                return date
            }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Invalid ISO8601 date string: \(str)")
        }
        return decoder
    }

    private func makeEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    /// Fetch list of memories with optional filtering, search query, and pagination
    public func fetchMemories(
        type: MemoryType? = nil,
        status: MemoryStatus? = nil,
        query: String? = nil,
        limit: Int = 50,
        offset: Int = 0,
        includeArchived: Bool = false,
        includeExpired: Bool = false
    ) async throws -> MemoryListResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/memories"), resolvingAgainstBaseURL: true)!
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
            URLQueryItem(name: "include_archived", value: String(includeArchived)),
            URLQueryItem(name: "include_expired", value: String(includeExpired))
        ]
        if let type = type {
            queryItems.append(URLQueryItem(name: "type", value: type.rawValue))
        }
        if let status = status {
            queryItems.append(URLQueryItem(name: "status", value: status.rawValue))
        }
        if let query = query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "q", value: query))
        }
        components.queryItems = queryItems

        guard let url = components.url else {
            throw APIError.networkError("Invalid URL components")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch memories"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(MemoryListResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Fetch details for a specific memory
    public func fetchMemory(id: String) async throws -> MemoryItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/memories/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch memory"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(MemoryItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Create an explicit user memory
    public func createMemory(payload: MemoryCreatePayload) async throws -> MemoryItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/memories")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to create memory"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(MemoryItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Update allowed fields on an existing memory
    public func updateMemory(id: String, payload: MemoryUpdatePayload) async throws -> MemoryItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/memories/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "PATCH"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to update memory"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(MemoryItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Soft delete (archive) a memory
    public func deleteMemory(id: String) async throws -> MemoryDeleteResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/memories/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to delete memory"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(MemoryDeleteResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 5L.2 Memory Candidates API

    /// Fetch list of pending memory candidates for user review
    public func fetchMemoryCandidates(limit: Int = 20, offset: Int = 0) async throws -> CandidateReviewListResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/memories/candidates"), resolvingAgainstBaseURL: true)!
        components.queryItems = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset))
        ]

        guard let url = components.url else {
            throw APIError.networkError("Invalid URL components")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch memory candidates"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CandidateReviewListResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Fetch a single memory candidate by ID
    public func fetchMemoryCandidate(id: String) async throws -> CandidateReviewItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/memories/candidates/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch candidate"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CandidateReviewItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Approve a pending memory candidate with optional edits or conflict resolution action
    public func approveMemoryCandidate(id: String, payload: CandidateApprovePayload) async throws -> CandidateApproveResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/memories/candidates/\(id)/approve")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw APIError.serverError(statusCode: httpResponse.statusCode, message: detail)
            }
            let message = String(data: data, encoding: .utf8) ?? "Failed to approve memory candidate"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CandidateApproveResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Reject and discard a pending memory candidate
    public func rejectMemoryCandidate(id: String) async throws -> CandidateRejectResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/memories/candidates/\(id)/reject")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw APIError.serverError(statusCode: httpResponse.statusCode, message: detail)
            }
            let message = String(data: data, encoding: .utf8) ?? "Failed to reject memory candidate"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CandidateRejectResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Tasks API

    /// Fetch list of tasks with filtering, search query, and pagination
    public func fetchTasks(
        status: TaskStatus? = nil,
        priority: TaskPriority? = nil,
        query: String? = nil,
        limit: Int = 50,
        offset: Int = 0,
        includeInactive: Bool = false
    ) async throws -> TaskListResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/tasks"), resolvingAgainstBaseURL: true)!
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
            URLQueryItem(name: "include_inactive", value: String(includeInactive))
        ]
        if let status = status {
            queryItems.append(URLQueryItem(name: "status", value: status.rawValue))
        }
        if let priority = priority {
            queryItems.append(URLQueryItem(name: "priority", value: priority.rawValue))
        }
        if let query = query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "q", value: query))
        }
        components.queryItems = queryItems

        guard let url = components.url else {
            throw APIError.networkError("Invalid URL components")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch tasks"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(TaskListResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Fetch single task by ID
    public func fetchTask(id: String) async throws -> TaskItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/tasks/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch task"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(TaskItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Create an actionable task
    public func createTask(payload: TaskCreateRequest) async throws -> TaskItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/tasks")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to create task"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(TaskItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Update user-editable fields on an existing task
    public func updateTask(id: String, payload: TaskUpdateRequest) async throws -> TaskItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/tasks/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "PATCH"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to update task"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(TaskItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Mark a pending task as in progress.
    public func startTask(id: String) async throws -> TaskItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/tasks/\(id)/start")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else { throw APIError.networkError("Invalid HTTP response") }
        guard (200...299).contains(httpResponse.statusCode) else {
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: String(data: data, encoding: .utf8) ?? "Failed to start task")
        }
        do { return try makeDecoder().decode(TaskItem.self, from: data) }
        catch { throw APIError.decodingError(error.localizedDescription) }
    }

    public func linkTask(id: String, toCommitment commitmentId: String) async throws -> TaskItem {
        try await taskRelationshipRequest(path: "api/v1/tasks/\(id)/link/\(commitmentId)", method: "POST")
    }

    public func unlinkTask(id: String) async throws -> TaskItem {
        try await taskRelationshipRequest(path: "api/v1/tasks/\(id)/unlink", method: "POST")
    }

    private func taskRelationshipRequest(path: String, method: String) async throws -> TaskItem {
        let endpoint = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else { throw APIError.networkError("Invalid HTTP response") }
        guard (200...299).contains(httpResponse.statusCode) else {
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: String(data: data, encoding: .utf8) ?? "Failed to update task relationship")
        }
        do { return try makeDecoder().decode(TaskItem.self, from: data) }
        catch { throw APIError.decodingError(error.localizedDescription) }
    }

    /// Mark task completed
    public func completeTask(id: String) async throws -> TaskItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/tasks/\(id)/complete")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to complete task"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(TaskItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Mark task cancelled
    public func cancelTask(id: String) async throws -> TaskItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/tasks/\(id)/cancel")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to cancel task"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(TaskItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Soft delete a task
    public func deleteTask(id: String) async throws -> TaskDeleteResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/tasks/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to delete task"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(TaskDeleteResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Commitments API

    /// Fetch list of commitments with filtering, search query, and pagination
    public func fetchCommitments(
        status: CommitmentStatus? = nil,
        priority: CommitmentPriority? = nil,
        query: String? = nil,
        limit: Int = 50,
        offset: Int = 0,
        includeInactive: Bool = false
    ) async throws -> CommitmentListResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/commitments"), resolvingAgainstBaseURL: true)!
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
            URLQueryItem(name: "include_inactive", value: String(includeInactive))
        ]
        if let status = status {
            queryItems.append(URLQueryItem(name: "status", value: status.rawValue))
        }
        if let priority = priority {
            queryItems.append(URLQueryItem(name: "priority", value: priority.rawValue))
        }
        if let query = query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "q", value: query))
        }
        components.queryItems = queryItems

        guard let url = components.url else {
            throw APIError.networkError("Invalid URL components")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch commitments"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CommitmentListResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Fetch single commitment by ID
    public func fetchCommitment(id: String) async throws -> CommitmentItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/commitments/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch commitment"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CommitmentItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Create a tracked commitment
    public func createCommitment(payload: CommitmentCreateRequest) async throws -> CommitmentItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/commitments")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to create commitment"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CommitmentItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Update user-editable fields on an existing commitment
    public func updateCommitment(id: String, payload: CommitmentUpdateRequest) async throws -> CommitmentItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/commitments/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "PATCH"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to update commitment"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CommitmentItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Mark commitment completed
    public func completeCommitment(id: String) async throws -> CommitmentItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/commitments/\(id)/complete")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to complete commitment"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CommitmentItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Mark commitment cancelled
    public func cancelCommitment(id: String) async throws -> CommitmentItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/commitments/\(id)/cancel")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to cancel commitment"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CommitmentItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Soft delete a commitment
    public func deleteCommitment(id: String) async throws -> CommitmentDeleteResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/commitments/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to delete commitment"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CommitmentDeleteResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Reminder Methods

    /// Fetch reminders with optional status, task_id, commitment_id, and query filtering
    public func fetchReminders(
        status: ReminderStatus? = nil,
        taskId: String? = nil,
        commitmentId: String? = nil,
        query: String? = nil,
        limit: Int = 50,
        offset: Int = 0
    ) async throws -> ReminderListResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/reminders"), resolvingAgainstBaseURL: false)
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: "\(limit)"),
            URLQueryItem(name: "offset", value: "\(offset)")
        ]
        if let status = status {
            queryItems.append(URLQueryItem(name: "status", value: status.rawValue))
        }
        if let taskId = taskId {
            queryItems.append(URLQueryItem(name: "task_id", value: taskId))
        }
        if let commitmentId = commitmentId {
            queryItems.append(URLQueryItem(name: "commitment_id", value: commitmentId))
        }
        if let query = query, !query.isEmpty {
            queryItems.append(URLQueryItem(name: "q", value: query))
        }
        components?.queryItems = queryItems

        guard let url = components?.url else {
            throw APIError.networkError("Invalid URL")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch reminders"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ReminderListResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Fetch a single reminder by ID
    public func fetchReminder(id: String) async throws -> ReminderItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/reminders/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch reminder"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ReminderItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Create a new reminder
    public func createReminder(payload: ReminderCreateRequest) async throws -> ReminderItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/reminders")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            request.httpBody = try makeEncoder().encode(payload)
        } catch {
            throw APIError.decodingError("Encoding error: \(error.localizedDescription)")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to create reminder"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ReminderItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Update an existing reminder
    public func updateReminder(id: String, payload: ReminderUpdateRequest) async throws -> ReminderItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/reminders/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "PATCH"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            request.httpBody = try makeEncoder().encode(payload)
        } catch {
            throw APIError.decodingError("Encoding error: \(error.localizedDescription)")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to update reminder"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ReminderItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Snooze a reminder forward
    public func snoozeReminder(id: String, durationMinutes: Int = 15) async throws -> ReminderItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/reminders/\(id)/snooze")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let payload = ["duration_minutes": durationMinutes]
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to snooze reminder"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ReminderItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Cancel a reminder
    public func cancelReminder(id: String) async throws -> ReminderItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/reminders/\(id)/cancel")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to cancel reminder"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ReminderItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Soft delete a reminder
    public func deleteReminder(id: String) async throws -> ReminderDeleteResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/reminders/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to delete reminder"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ReminderDeleteResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Location API

    /// Fetch current location snapshot from backend
    public func fetchCurrentLocation() async throws -> LocationItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/location/current")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch current location"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(LocationItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Report an ephemeral current location snapshot to backend
    public func reportCurrentLocation(item: LocationItem) async throws -> LocationItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/location/current")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            request.httpBody = try makeEncoder().encode(item)
        } catch {
            throw APIError.decodingError("Encoding error: \(error.localizedDescription)")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to report current location"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(LocationItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Calendar API

    /// Fetch read-only calendar events for an explicit date range from backend
    public func fetchCalendarEvents(startAt: Date, endAt: Date, maxResults: Int = 10) async throws -> [CalendarEventItem] {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/calendar/events"), resolvingAgainstBaseURL: false)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]

        components?.queryItems = [
            URLQueryItem(name: "start_at", value: formatter.string(from: startAt)),
            URLQueryItem(name: "end_at", value: formatter.string(from: endAt)),
            URLQueryItem(name: "max_results", value: "\(maxResults)")
        ]

        guard let url = components?.url else {
            throw APIError.networkError("Invalid URL for calendar events query")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch calendar events"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            let decoded = try makeDecoder().decode(CalendarEventsResponse.self, from: data)
            return decoded.events
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Report an ephemeral calendar snapshot to backend
    public func reportCalendarEvents(events: [CalendarEventItem]) async throws -> [CalendarEventItem] {
        let endpoint = baseURL.appendingPathComponent("api/v1/calendar/events")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let payload = CalendarSnapshotIngestRequest(events: events)
        do {
            request.httpBody = try makeEncoder().encode(payload)
        } catch {
            throw APIError.decodingError("Encoding error: \(error.localizedDescription)")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to report calendar events"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            let decoded = try makeDecoder().decode(CalendarEventsResponse.self, from: data)
            return decoded.events
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Contacts Operations (Phase 5F)

    /// Search contacts from backend
    public func searchContacts(query: String? = nil, maxResults: Int = 10) async throws -> [ContactItem] {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/contacts"), resolvingAgainstBaseURL: true)!
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "max_results", value: String(maxResults))
        ]
        if let q = query, !q.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "query", value: q.trimmingCharacters(in: .whitespacesAndNewlines)))
        }
        components.queryItems = queryItems

        guard let url = components.url else {
            throw APIError.networkError("Invalid URL components")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to search contacts"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            let decoded = try makeDecoder().decode(ContactSearchResponse.self, from: data)
            return decoded.contacts
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Report a bounded ephemeral contacts snapshot to backend
    public func reportContactsSnapshot(contacts: [ContactItem]) async throws -> [ContactItem] {
        let endpoint = baseURL.appendingPathComponent("api/v1/contacts/snapshot")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let payload = ContactSnapshotIngestRequest(contacts: contacts)
        do {
            request.httpBody = try makeEncoder().encode(payload)
        } catch {
            throw APIError.decodingError("Encoding error: \(error.localizedDescription)")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to report contacts snapshot"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            let decoded = try makeDecoder().decode(ContactSearchResponse.self, from: data)
            return decoded.contacts
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Assets (Phase 5G: Metadata Only)

    /// Register a temporary asset reference with the backend (metadata only, no binary content).
    public func ingestAsset(request: AssetIngestRequest) async throws -> AssetReference {
        let endpoint = baseURL.appendingPathComponent("api/v1/assets")
        var urlRequest = URLRequest(url: endpoint)
        urlRequest.httpMethod = "POST"
        urlRequest.timeoutInterval = 10.0
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            urlRequest.httpBody = try makeEncoder().encode(request)
        } catch {
            throw APIError.decodingError("Encoding error: \(error.localizedDescription)")
        }

        let (data, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to ingest asset"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(AssetReference.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Retrieve a specific temporary asset reference by ID.
    public func fetchAsset(assetId: String) async throws -> AssetReference {
        let endpoint = baseURL.appendingPathComponent("api/v1/assets/\(assetId)")
        var urlRequest = URLRequest(url: endpoint)
        urlRequest.httpMethod = "GET"
        urlRequest.timeoutInterval = 10.0
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch asset"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(AssetReference.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Immediately remove an asset reference.
    public func deleteAsset(assetId: String) async throws -> Bool {
        let endpoint = baseURL.appendingPathComponent("api/v1/assets/\(assetId)")
        var urlRequest = URLRequest(url: endpoint)
        urlRequest.httpMethod = "DELETE"
        urlRequest.timeoutInterval = 10.0
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to delete asset"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        return true
    }

    // MARK: - Proactive Proposals (Phase 5H)

    /// Fetch a bounded, user-scoped list of proactive candidates.
    public func fetchProactiveCandidates(
        limit: Int = 20,
        offset: Int = 0,
        status: String? = nil
    ) async throws -> [ProactiveCandidateItem] {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("api/v1/proactive/candidates"),
            resolvingAgainstBaseURL: true
        )
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
        ]
        if let status = status {
            queryItems.append(URLQueryItem(name: "status", value: status))
        }
        components?.queryItems = queryItems

        guard let url = components?.url else {
            throw APIError.networkError("Failed to build proactive candidates URL")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch proactive candidates"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            let decoded = try makeDecoder().decode(ProactiveCandidateListResponse.self, from: data)
            return decoded.candidates
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Retrieve a specific proactive proposal by ID.
    public func fetchProactiveCandidate(id: String) async throws -> ProactiveCandidateItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/proactive/candidates/\(id)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch candidate"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ProactiveCandidateItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Explicitly dismiss a proactive proposal.
    public func dismissProactiveCandidate(id: String) async throws -> ProactiveCandidateItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/proactive/candidates/\(id)/dismiss")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to dismiss candidate"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ProactiveCandidateItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Cancel a proactive proposal.
    public func cancelProactiveCandidate(id: String) async throws -> ProactiveCandidateItem {
        let endpoint = baseURL.appendingPathComponent("api/v1/proactive/candidates/\(id)/cancel")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to cancel candidate"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ProactiveCandidateItem.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Trigger on-demand domain proposal evaluation (Tasks, Commitments, Calendar, Reminders).
    public func evaluateProposals() async throws -> [ProactiveCandidateItem] {
        let endpoint = baseURL.appendingPathComponent("api/v1/proactive/evaluate")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 10.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to trigger evaluation"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode([ProactiveCandidateItem].self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Accept an eligible proactive proposal and initiate execution resolution.
    public func acceptProactiveCandidate(id: String, conversationId: String? = nil) async throws -> ProactiveResolutionResponse {
        let endpoint = baseURL.appendingPathComponent("api/v1/proactive/candidates/\(id)/accept")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let reqBody = AcceptProactiveRequest(conversationId: conversationId)
        request.httpBody = try JSONEncoder().encode(reqBody)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to accept candidate"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ProactiveResolutionResponse.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 6D: Agenda & Schedule Endpoints

    /// Fetch unified daily agenda for an explicit local date.
    public func fetchDailyAgenda(
        date: String? = nil,
        timezone: String? = nil,
        workStartHour: Int = 9,
        workEndHour: Int = 18,
        includeConflicts: Bool = true,
        includeFreeSlots: Bool = true
    ) async throws -> DailyAgendaResponsePayload {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/agenda/daily"), resolvingAgainstBaseURL: false)
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "work_start_hour", value: "\(workStartHour)"),
            URLQueryItem(name: "work_end_hour", value: "\(workEndHour)"),
            URLQueryItem(name: "include_conflicts", value: includeConflicts ? "true" : "false"),
            URLQueryItem(name: "include_free_slots", value: includeFreeSlots ? "true" : "false")
        ]
        if let d = date { queryItems.append(URLQueryItem(name: "date", value: d)) }
        if let tz = timezone { queryItems.append(URLQueryItem(name: "timezone", value: tz)) }
        components?.queryItems = queryItems

        guard let url = components?.url else { throw APIError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch daily agenda"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DailyAgendaResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Fetch detected schedule conflicts across a rolling lookahead window.
    public func fetchScheduleConflicts(
        windowHours: Int = 24,
        timezone: String? = nil
    ) async throws -> ConflictScanResponsePayload {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/agenda/conflicts"), resolvingAgainstBaseURL: false)
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "window_hours", value: "\(windowHours)")
        ]
        if let tz = timezone { queryItems.append(URLQueryItem(name: "timezone", value: tz)) }
        components?.queryItems = queryItems

        guard let url = components?.url else { throw APIError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch schedule conflicts"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ConflictScanResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Propose feasible work slot for a task.
    public func proposeScheduleSlot(
        taskId: String,
        durationMinutes: Int,
        date: String? = nil,
        timezone: String? = nil,
        workStartHour: Int = 9,
        workEndHour: Int = 18
    ) async throws -> ProposedSlotResponsePayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/propose-slot")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        var bodyDict: [String: Any] = [
            "task_id": taskId,
            "duration_minutes": durationMinutes,
            "work_start_hour": workStartHour,
            "work_end_hour": workEndHour
        ]
        if let d = date { bodyDict["date"] = d }
        if let tz = timezone { bodyDict["timezone"] = tz }

        request.httpBody = try JSONSerialization.data(withJSONObject: bodyDict)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to evaluate slot proposal"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ProposedSlotResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 6E: Schedule Coordination & Conflict Resolution Endpoints

    /// Request deterministic schedule coordination analysis.
    public func coordinateSchedule(
        date: String? = nil,
        timezone: String? = nil,
        workStartHour: Int = 9,
        workEndHour: Int = 18,
        defaultDurationMinutes: Int = 30,
        taskIds: [String]? = nil
    ) async throws -> CoordinateScheduleResponsePayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/coordinate")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        var bodyDict: [String: Any] = [
            "work_start_hour": workStartHour,
            "work_end_hour": workEndHour,
            "default_duration_minutes": defaultDurationMinutes
        ]
        if let d = date { bodyDict["date"] = d }
        if let tz = timezone { bodyDict["timezone"] = tz }
        if let tids = taskIds { bodyDict["task_ids"] = tids }

        request.httpBody = try JSONSerialization.data(withJSONObject: bodyDict)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to analyze schedule coordination"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(CoordinateScheduleResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Request deterministic conflict resolution strategy analysis.
    public func resolveScheduleConflicts(
        date: String? = nil,
        timezone: String? = nil,
        windowHours: Int = 24,
        defaultDurationMinutes: Int = 30,
        reschedulingRequested: Bool = false
    ) async throws -> ResolveConflictsResponsePayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/resolve-conflicts")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        var bodyDict: [String: Any] = [
            "window_hours": windowHours,
            "default_duration_minutes": defaultDurationMinutes,
            "rescheduling_requested": reschedulingRequested
        ]
        if let d = date { bodyDict["date"] = d }
        if let tz = timezone { bodyDict["timezone"] = tz }

        request.httpBody = try JSONSerialization.data(withJSONObject: bodyDict)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to analyze conflict resolution strategies"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ResolveConflictsResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 6I Schedule Recovery Methods

    /// Propose a new ScheduleRecoveryProposal across overloaded or drifting tasks and reminders.
    public func proposeScheduleRecovery(payload: ProposeScheduleRecoveryPayload) async throws -> ScheduleRecoveryProposal {
        let endpoint = baseURL.appendingPathComponent("api/v1/schedule/recovery/propose")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to propose schedule recovery"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ScheduleRecoveryProposal.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Retrieve proposal snapshot by ID.
    public func getScheduleRecoveryProposal(proposalId: String) async throws -> ScheduleRecoveryProposal {
        let endpoint = baseURL.appendingPathComponent("api/v1/schedule/recovery/proposals/\(proposalId)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to get proposal snapshot"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ScheduleRecoveryProposal.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Cancel an active proposal.
    public func cancelScheduleRecoveryProposal(proposalId: String, reason: String? = nil) async throws -> ScheduleRecoveryProposal {
        let endpoint = baseURL.appendingPathComponent("api/v1/schedule/recovery/proposals/\(proposalId)/cancel")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let payload = CancelRecoveryProposalPayload(reason: reason)
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to cancel proposal"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ScheduleRecoveryProposal.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Apply a confirmed proposal through the 24-step execution pipeline.
    public func applyScheduleRecoveryProposal(proposalId: String, payload: ApplyRecoveryProposalPayload) async throws -> RecoveryExecutionResult {
        let endpoint = baseURL.appendingPathComponent("api/v1/schedule/recovery/proposals/\(proposalId)/apply")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            if let container = try? makeDecoder().decode(FastAPIErrorContainer<ScheduleRecoveryErrorDetail>.self, from: data) {
                throw APIError.scheduleRecoveryConflict(container.detail)
            } else if let detail = try? makeDecoder().decode(ScheduleRecoveryErrorDetail.self, from: data) {
                throw APIError.scheduleRecoveryConflict(detail)
            } else {
                let message = String(data: data, encoding: .utf8) ?? "Proposal execution conflict"
                throw APIError.conflict(code: "CONFLICT", message: message)
            }
        }

        if httpResponse.statusCode == 412 {
            if let container = try? makeDecoder().decode(FastAPIErrorContainer<ScheduleRecoveryErrorDetail>.self, from: data) {
                throw APIError.preconditionFailed(code: container.detail.error, message: container.detail.message)
            } else if let detail = try? makeDecoder().decode(ScheduleRecoveryErrorDetail.self, from: data) {
                throw APIError.preconditionFailed(code: detail.error, message: detail.message)
            } else {
                let message = String(data: data, encoding: .utf8) ?? "Precondition failed"
                throw APIError.preconditionFailed(code: "PRECONDITION_FAILED", message: message)
            }
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to apply recovery proposal"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(RecoveryExecutionResult.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Get execution result for a proposal.
    public func getScheduleRecoveryResult(proposalId: String) async throws -> RecoveryExecutionResult {
        let endpoint = baseURL.appendingPathComponent("api/v1/schedule/recovery/proposals/\(proposalId)/result")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to get execution result"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(RecoveryExecutionResult.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Evaluate proposal in dry-run mode without mutating domain state.
    public func evaluateScheduleRecovery(payload: EvaluateRecoveryProposalPayload) async throws -> RecoveryEvaluationResult {
        let endpoint = baseURL.appendingPathComponent("api/v1/schedule/recovery/evaluate")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to evaluate schedule recovery"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(RecoveryEvaluationResult.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 6K Schedule Disruption Integration

    /// Diagnoses schedule disruption, evaluates focus capacity deficits, and discovers recovery candidate tasks and reminders.
    /// Pure read-only operation; fails closed with APIError.serverError (HTTP 412) if required domains are unverified.
    public func diagnoseScheduleDisruption(
        payload: DiagnoseScheduleDisruptionPayload
    ) async throws -> ScheduleDisruptionDiagnostic {
        let endpoint = baseURL.appendingPathComponent("api/v1/schedule/recovery/diagnose")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 412 {
            var errorCode = "PRECONDITION_FAILED"
            var errorMessage = "Precondition failed"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    errorCode = detailDict["error"] as? String ?? errorCode
                    errorMessage = detailDict["message"] as? String ?? errorMessage
                } else if let detailStr = json["detail"] as? String {
                    errorMessage = detailStr
                    if detailStr.contains("TASK_DOMAIN_UNAVAILABLE") { errorCode = "TASK_DOMAIN_UNAVAILABLE" }
                    else if detailStr.contains("NO_CALENDAR_SNAPSHOT") { errorCode = "NO_CALENDAR_SNAPSHOT" }
                    else if detailStr.contains("STALE_CALENDAR_SNAPSHOT") { errorCode = "STALE_CALENDAR_SNAPSHOT" }
                    else if detailStr.contains("CALENDAR_SNAPSHOT_SATURATED") { errorCode = "CALENDAR_SNAPSHOT_SATURATED" }
                    else if detailStr.contains("TASK_UNIVERSE_SATURATED") { errorCode = "TASK_UNIVERSE_SATURATED" }
                }
            }
            throw APIError.preconditionFailed(code: errorCode, message: errorMessage)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to diagnose schedule disruption"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ScheduleDisruptionDiagnostic.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 6M Diurnal Horizon Endpoints

    /// Synthesizes read-only executive morning day-plan.
    public func getDailyExecutivePlan(
        targetDate: String? = nil,
        explicitTz: String? = nil
    ) async throws -> DailyExecutivePlanPayload {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/agenda/daily/executive-plan"), resolvingAgainstBaseURL: false)
        var queryItems: [URLQueryItem] = []
        if let d = targetDate { queryItems.append(URLQueryItem(name: "target_date", value: d)) }
        if let tz = explicitTz { queryItems.append(URLQueryItem(name: "explicit_tz", value: tz)) }
        if !queryItems.isEmpty { components?.queryItems = queryItems }

        guard let url = components?.url else { throw APIError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch daily executive plan"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DailyExecutivePlanPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Generates a server-held DayPlanProposal in DRAFT status.
    public func proposeDayPlan(
        payload: ProposeDayPlanRequestPayload
    ) async throws -> DayPlanProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/daily/plan/propose")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to propose daily plan"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DayPlanProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Executes or aborts a confirmed DayPlanProposal under two-tier governed execution.
    public func applyDayPlan(
        proposalId: String,
        approved: Bool
    ) async throws -> DayPlanProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/daily/plan/apply")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let reqPayload = ApplyDayPlanRequestPayload(proposalId: proposalId, approved: approved)
        request.httpBody = try makeEncoder().encode(reqPayload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            var code = "CONFLICT"
            var message = "Proposal execution conflict"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    code = detailDict["code"] as? String ?? code
                    message = detailDict["message"] as? String ?? message
                } else if let detailStr = json["detail"] as? String {
                    message = detailStr
                    code = detailStr
                }
            }
            throw APIError.conflict(code: code, message: message)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to apply daily plan"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DayPlanProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Synthesizes read-only evening review with completed/overdue items and rollover candidates.
    public func getEveningHorizonReview(
        targetDate: String? = nil,
        explicitTz: String? = nil
    ) async throws -> EveningHorizonReviewPayload {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/agenda/daily/evening-review"), resolvingAgainstBaseURL: false)
        var queryItems: [URLQueryItem] = []
        if let d = targetDate { queryItems.append(URLQueryItem(name: "target_date", value: d)) }
        if let tz = explicitTz { queryItems.append(URLQueryItem(name: "explicit_tz", value: tz)) }
        if !queryItems.isEmpty { components?.queryItems = queryItems }

        guard let url = components?.url else { throw APIError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch evening review"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(EveningHorizonReviewPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Generates a server-held EveningReviewProposal in DRAFT status.
    public func proposeEveningReview(
        payload: ProposeEveningReviewRequestPayload
    ) async throws -> EveningReviewProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/daily/evening-review/propose")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to propose evening review"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(EveningReviewProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Executes or aborts a confirmed EveningReviewProposal under two-tier governed execution.
    public func applyEveningReview(
        proposalId: String,
        approved: Bool
    ) async throws -> EveningReviewProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/daily/evening-review/apply")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let reqPayload = ApplyEveningReviewRequestPayload(proposalId: proposalId, approved: approved)
        request.httpBody = try makeEncoder().encode(reqPayload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            var code = "CONFLICT"
            var message = "Proposal execution conflict"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    code = detailDict["code"] as? String ?? code
                    message = detailDict["message"] as? String ?? message
                } else if let detailStr = json["detail"] as? String {
                    message = detailStr
                    code = detailStr
                }
            }
            throw APIError.conflict(code: code, message: message)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to apply evening review"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(EveningReviewProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Weekly Strategic Horizon API (Phase 6N)

    /// Synthesizes rolling 7-day strategic horizon projection across Calendar, Tasks, and Commitments.
    public func getWeeklyStrategicHorizon(
        startDate: String? = nil,
        timezone: String? = nil,
        workStartHour: Int? = nil,
        workEndHour: Int? = nil
    ) async throws -> WeeklyStrategicHorizon {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/agenda/weekly/horizon"), resolvingAgainstBaseURL: false)
        var queryItems: [URLQueryItem] = []
        if let d = startDate { queryItems.append(URLQueryItem(name: "start_date", value: d)) }
        if let tz = timezone { queryItems.append(URLQueryItem(name: "timezone", value: tz)) }
        if let s = workStartHour { queryItems.append(URLQueryItem(name: "work_start_hour", value: String(s))) }
        if let e = workEndHour { queryItems.append(URLQueryItem(name: "work_end_hour", value: String(e))) }
        if !queryItems.isEmpty { components?.queryItems = queryItems }

        guard let url = components?.url else { throw APIError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch weekly strategic horizon"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyStrategicHorizon.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Generates a server-held WeeklyPlanProposal in DRAFT status.
    public func proposeWeeklyPlan(
        payload: ProposeWeeklyPlanRequestPayload
    ) async throws -> WeeklyPlanProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/weekly/propose-plan")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to propose weekly plan"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyPlanProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Executes (approved=true) or aborts (approved=false) a confirmed WeeklyPlanProposal under two-tier governed execution.
    public func applyWeeklyPlan(
        proposalId: String,
        approved: Bool
    ) async throws -> WeeklyPlanProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/weekly/apply-plan")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let reqPayload = ApplyWeeklyPlanRequestPayload(proposalId: proposalId, approved: approved)
        request.httpBody = try makeEncoder().encode(reqPayload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            var code = "CONFLICT"
            var message = "Proposal execution conflict"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    code = detailDict["code"] as? String ?? code
                    message = detailDict["message"] as? String ?? message
                } else if let detailStr = json["detail"] as? String {
                    message = detailStr
                    code = detailStr
                }
            }
            throw APIError.conflict(code: code, message: message)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to apply weekly plan"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyPlanProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Synthesizes rolling 7-day retrospective lookback over completed tasks, commitments, and velocity.
    public func getWeeklyRetrospective(
        endDate: String? = nil,
        timezone: String? = nil
    ) async throws -> WeeklyRetrospective {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/agenda/weekly/retrospective"), resolvingAgainstBaseURL: false)
        var queryItems: [URLQueryItem] = []
        if let d = endDate { queryItems.append(URLQueryItem(name: "end_date", value: d)) }
        if let tz = timezone { queryItems.append(URLQueryItem(name: "timezone", value: tz)) }
        if !queryItems.isEmpty { components?.queryItems = queryItems }

        guard let url = components?.url else { throw APIError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch weekly retrospective"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyRetrospective.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Generates a server-held WeeklyRetrospectiveProposal in DRAFT status.
    public func proposeWeeklyRetrospective(
        payload: ProposeWeeklyRetrospectiveRequestPayload
    ) async throws -> WeeklyRetrospectiveProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/weekly/propose-retrospective")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to propose weekly retrospective"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyRetrospectiveProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Executes (approved=true) or aborts (approved=false) a confirmed WeeklyRetrospectiveProposal under two-tier governed execution.
    public func applyWeeklyRetrospective(
        proposalId: String,
        approved: Bool
    ) async throws -> WeeklyRetrospectiveProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/weekly/apply-retrospective")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let reqPayload = ApplyWeeklyRetrospectiveRequestPayload(proposalId: proposalId, approved: approved)
        request.httpBody = try makeEncoder().encode(reqPayload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            var code = "CONFLICT"
            var message = "Proposal execution conflict"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    code = detailDict["code"] as? String ?? code
                    message = detailDict["message"] as? String ?? message
                } else if let detailStr = json["detail"] as? String {
                    message = detailStr
                    code = detailStr
                }
            }
            throw APIError.conflict(code: code, message: message)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to apply weekly retrospective"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyRetrospectiveProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Retrieves an existing proposal by ID, decoded via server-discriminated WeeklyProposalPayload.
    public func getWeeklyProposal(
        proposalId: String
    ) async throws -> WeeklyProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/weekly/proposals/\(proposalId)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch proposal status"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Aborts an existing proposal by ID, decoded via server-discriminated WeeklyProposalPayload.
    public func abortWeeklyProposal(
        proposalId: String
    ) async throws -> WeeklyProposalPayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/weekly/proposals/\(proposalId)/abort")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            var code = "CONFLICT"
            var message = "Proposal abort conflict"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    code = detailDict["code"] as? String ?? code
                    message = detailDict["message"] as? String ?? message
                } else if let detailStr = json["detail"] as? String {
                    message = detailStr
                    code = detailStr
                }
            }
            throw APIError.conflict(code: code, message: message)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to abort proposal"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(WeeklyProposalPayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 6O: Event Briefing & Debrief Orchestration

    /// Synthesizes read-only pre-event briefing for an upcoming calendar event.
    public func getEventBriefing(
        eventId: String
    ) async throws -> EventBriefing {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/events/\(eventId)/briefing")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch event briefing"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(EventBriefing.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Generates a server-held debrief proposal in DRAFT status.
    public func proposeEventDebrief(
        eventId: String,
        payload: ProposeDebriefRequestPayload
    ) async throws -> DebriefProposal {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/events/\(eventId)/debrief/propose")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try makeEncoder().encode(payload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to propose event debrief"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DebriefProposal.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Retrieves a server-held debrief proposal by ID.
    public func getDebriefProposal(
        proposalId: String
    ) async throws -> DebriefProposal {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/debrief/proposals/\(proposalId)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch debrief proposal"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DebriefProposal.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Executes or aborts a confirmed DebriefProposal under two-tier governed execution.
    public func applyDebriefProposal(
        proposalId: String,
        approved: Bool
    ) async throws -> DebriefProposal {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/debrief/proposals/\(proposalId)/apply")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let reqPayload = ApplyDebriefRequestPayload(approved: approved)
        request.httpBody = try makeEncoder().encode(reqPayload)

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            var code = "CONFLICT"
            var message = "Debrief proposal conflict"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    code = detailDict["code"] as? String ?? code
                    message = detailDict["message"] as? String ?? message
                } else if let detailStr = json["detail"] as? String {
                    message = detailStr
                    code = detailStr
                }
            }
            throw APIError.conflict(code: code, message: message)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to apply debrief proposal"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DebriefProposal.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    /// Aborts a pending debrief proposal.
    public func abortDebriefProposal(
        proposalId: String
    ) async throws -> DebriefProposal {
        let endpoint = baseURL.appendingPathComponent("api/v1/agenda/debrief/proposals/\(proposalId)/abort")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        if httpResponse.statusCode == 409 {
            var code = "CONFLICT"
            var message = "Debrief proposal conflict"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let detailDict = json["detail"] as? [String: Any] {
                    code = detailDict["code"] as? String ?? code
                    message = detailDict["message"] as? String ?? message
                } else if let detailStr = json["detail"] as? String {
                    message = detailStr
                    code = detailStr
                }
            }
            throw APIError.conflict(code: code, message: message)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to abort debrief proposal"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(DebriefProposal.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }

    // MARK: - Phase 6Q Conversation History Methods

    /// Retrieve conversation history for a given conversation ID from the production endpoint.
    public func fetchConversationHistory(conversationId: String) async throws -> ConversationHistoryResponsePayload {
        let endpoint = baseURL.appendingPathComponent("api/v1/conversations/\(conversationId)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 15.0
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await performRequest(request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.networkError("Invalid HTTP response")
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Failed to fetch conversation history"
            throw APIError.serverError(statusCode: httpResponse.statusCode, message: message)
        }

        do {
            return try makeDecoder().decode(ConversationHistoryResponsePayload.self, from: data)
        } catch {
            throw APIError.decodingError(error.localizedDescription)
        }
    }
}

// MARK: - Phase 6Q Conversation History DTOs

public struct MessageOutPayload: Codable, Equatable, Sendable {
    public let role: String
    public let content: String

    public init(role: String, content: String) {
        self.role = role
        self.content = content
    }
}

public struct ConversationHistoryResponsePayload: Codable, Equatable, Sendable {
    public let conversationId: String
    public let messages: [MessageOutPayload]
    public let createdAt: String
    public let updatedAt: String

    enum CodingKeys: String, CodingKey {
        case conversationId = "conversation_id"
        case messages
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(
        conversationId: String,
        messages: [MessageOutPayload],
        createdAt: String,
        updatedAt: String
    ) {
        self.conversationId = conversationId
        self.messages = messages
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}




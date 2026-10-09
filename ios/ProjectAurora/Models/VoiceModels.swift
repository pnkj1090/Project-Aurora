import Foundation

// MARK: - Voice Session State
public enum VoiceSessionState: Equatable, Sendable {
    case idle
    case preparing
    case requestingPermission
    case ready
    case listening(amplitude: Float)
    case transcribing(partialText: String)
    case processing
    case speaking(speakingText: String)
    case interrupted
    case stopping
    case failed(VoiceSessionError)
}

// MARK: - Voice Permission State
public enum VoicePermissionState: Equatable, Sendable {
    case notDetermined
    case authorized
    case denied
    case restricted
}

// MARK: - Voice Permission Type
public enum VoicePermissionType: Equatable, Sendable {
    case microphone
    case speechRecognition
}

// MARK: - Speech Unavailable Reason
public enum SpeechUnavailableReason: Equatable, Sendable {
    case onDeviceNotSupported
    case notAvailable
    case restricted
}

// MARK: - Voice Session Error
public enum VoiceSessionError: LocalizedError, Equatable, Sendable {
    case microphoneUnavailable
    case permissionDenied(VoicePermissionType)
    case speechUnavailable(SpeechUnavailableReason)
    case unsupportedLocale(identifier: String)
    case audioSessionConfigurationFailed(String)
    case audioInterruption(String)
    case audioRouteChange(String)
    case transcriptionFailed(String)
    case synthesisFailed(String)
    case cancelled

    public var errorDescription: String? {
        switch self {
        case .microphoneUnavailable:
            return "Microphone is unavailable on this device."
        case .permissionDenied(let type):
            switch type {
            case .microphone:
                return "Microphone permission was denied. Please enable it in Settings."
            case .speechRecognition:
                return "Speech recognition permission was denied. Please enable it in Settings."
            }
        case .speechUnavailable(let reason):
            switch reason {
            case .onDeviceNotSupported:
                return "On-device speech recognition is not supported on this device/locale."
            case .notAvailable:
                return "Speech recognition service is currently unavailable."
            case .restricted:
                return "Speech recognition is restricted on this device."
            }
        case .unsupportedLocale(let identifier):
            return "Speech recognition does not support locale '\(identifier)'."
        case .audioSessionConfigurationFailed(let message):
            return "Audio session configuration failed: \(message)"
        case .audioInterruption(let message):
            return "Audio session interrupted: \(message)"
        case .audioRouteChange(let message):
            return "Audio route changed: \(message)"
        case .transcriptionFailed(let message):
            return "Transcription failed: \(message)"
        case .synthesisFailed(let message):
            return "Speech synthesis failed: \(message)"
        case .cancelled:
            return "Voice session was cancelled."
        }
    }
}

// MARK: - Speech Transcription Result
public struct SpeechTranscriptionResult: Equatable, Sendable {
    public let text: String
    public let isFinal: Bool
    public let confidence: Double
    public let timestamp: TimeInterval

    public init(
        text: String,
        isFinal: Bool,
        confidence: Double = 1.0,
        timestamp: TimeInterval = Date().timeIntervalSince1970
    ) {
        self.text = text
        self.isFinal = isFinal
        self.confidence = confidence
        self.timestamp = timestamp
    }
}

// MARK: - Voice Configuration
public struct VoiceConfiguration: Equatable, Sendable {
    public var vadEnergyThresholdDbfs: Float
    public var vadSustainedDurationSeconds: TimeInterval
    public var silenceTimeoutSeconds: TimeInterval
    public var speechRate: Float
    public var speechPitch: Float
    public var autoListenAfterSpeaking: Bool
    public var conversationalSilenceTimeoutSeconds: TimeInterval

    public init(
        vadEnergyThresholdDbfs: Float = -32.0,
        vadSustainedDurationSeconds: TimeInterval = 0.16,
        silenceTimeoutSeconds: TimeInterval = 1.5,
        speechRate: Float = 0.5,
        speechPitch: Float = 1.0,
        autoListenAfterSpeaking: Bool = true,
        conversationalSilenceTimeoutSeconds: TimeInterval = 6.0
    ) {
        self.vadEnergyThresholdDbfs = vadEnergyThresholdDbfs
        self.vadSustainedDurationSeconds = vadSustainedDurationSeconds
        self.silenceTimeoutSeconds = silenceTimeoutSeconds
        self.speechRate = speechRate
        self.speechPitch = speechPitch
        self.autoListenAfterSpeaking = autoListenAfterSpeaking
        self.conversationalSilenceTimeoutSeconds = conversationalSilenceTimeoutSeconds
    }
}

import Foundation
import AVFoundation

// MARK: - TTSTextSanitizer
public struct TTSTextSanitizer {
    public static func sanitizeForSpeech(_ rawText: String) -> String {
        var text = rawText

        // 1. Code blocks: replace ```...``` with "code snippet omitted."
        let codeBlockRegex = try? NSRegularExpression(pattern: "```[\\s\\S]*?```", options: [])
        if let regex = codeBlockRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "code snippet omitted. ")
        }

        // 2. Inline code: `code` -> code
        let inlineCodeRegex = try? NSRegularExpression(pattern: "`([^`]+)`", options: [])
        if let regex = inlineCodeRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "$1")
        }

        // 3. URLs: https://... -> link to domain
        let urlRegex = try? NSRegularExpression(pattern: "https?:\\/\\/(?:www\\.)?([^\\/\\s]+)[^\\s]*", options: [])
        if let regex = urlRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "link to $1")
        }

        // 4. ISO Timestamps: 2026-10-07T22:18:40... -> natural date/time
        let isoTimestampRegex = try? NSRegularExpression(pattern: "\\b(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})(?:[+-]\\d{2}:?\\d{2}|Z)?\\b", options: [])
        if let regex = isoTimestampRegex {
            let nsText = text as NSString
            let matches = regex.matches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count))
            for match in matches.reversed() {
                let fullString = nsText.substring(with: match.range)
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                var date = formatter.date(from: fullString)
                if date == nil {
                    formatter.formatOptions = [.withInternetDateTime]
                    date = formatter.date(from: fullString)
                }

                if let validDate = date {
                    let displayFormatter = DateFormatter()
                    displayFormatter.locale = Locale(identifier: "en_US_POSIX")
                    displayFormatter.timeZone = TimeZone(secondsFromGMT: 0)
                    displayFormatter.dateFormat = "MMMM d, yyyy 'at' h:mm a"
                    let readable = displayFormatter.string(from: validDate)
                    text = (text as NSString).replacingCharacters(in: match.range, with: readable)
                }
            }
        }

        // 5. Aurora citation markers: [doc:1], [source:abc], [citation:42], [1], [2]
        let citationRegex = try? NSRegularExpression(pattern: "\\[(?:doc:\\d+|source:[^\\]]+|citation:\\d+|\\d+)\\]", options: [.caseInsensitive])
        if let regex = citationRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "")
        }

        // 6. User bracketed text: [Important: call Mom] -> Important: call Mom
        let userBracketRegex = try? NSRegularExpression(pattern: "\\[([^\\]]+)\\]", options: [])
        if let regex = userBracketRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "$1")
        }

        // 7. Markdown headers: # Header -> Header
        let headerRegex = try? NSRegularExpression(pattern: "(?m)^#{1,6}\\s+", options: [])
        if let regex = headerRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "")
        }

        // 8. Markdown bold/italics: **bold**, *italic*, __bold__, _italic_
        let boldItalicRegex = try? NSRegularExpression(pattern: "(\\*\\*|__|\\*|_)(.*?)\\1", options: [])
        if let regex = boldItalicRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "$2")
        }

        // 9. Markdown list markers: "- ", "* ", "1. " -> natural pause
        let listRegex = try? NSRegularExpression(pattern: "(?m)^\\s*[-*\\d.]+\\s+", options: [])
        if let regex = listRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: "")
        }

        // 10. Collapse excess whitespace
        let spaceRegex = try? NSRegularExpression(pattern: "\\s+", options: [])
        if let regex = spaceRegex {
            text = regex.stringByReplacingMatches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count), withTemplate: " ")
        }

        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - SpeechOutputManager Delegate
public protocol SpeechOutputManagerDelegate: AnyObject, Sendable {
    func speechOutputDidStart(generation: UInt64)
    func speechOutputDidFinish(generation: UInt64)
    func speechOutputDidCancel(generation: UInt64)
}

// MARK: - SpeechOutputManaging Protocol
public protocol SpeechOutputManaging: AnyObject {
    var isSpeaking: Bool { get }
    func speak(text: String) async -> UInt64
    func stopSpeaking()
}

// MARK: - SpeechOutputManager
@MainActor
public final class SpeechOutputManager: NSObject, SpeechOutputManaging, AVSpeechSynthesizerDelegate {
    private let synthesizer: AVSpeechSynthesizer
    private var ttsGeneration: UInt64 = 0
    private var configuration: VoiceConfiguration
    public weak var delegate: SpeechOutputManagerDelegate?

    public init(
        synthesizer: AVSpeechSynthesizer = AVSpeechSynthesizer(),
        configuration: VoiceConfiguration = VoiceConfiguration()
    ) {
        self.synthesizer = synthesizer
        self.configuration = configuration
        super.init()
        self.synthesizer.delegate = self
    }

    public var isSpeaking: Bool {
        synthesizer.isSpeaking
    }

    public var currentGeneration: UInt64 {
        ttsGeneration
    }

    /// Speaks the provided text after deterministic sanitization
    @discardableResult
    public func speak(text: String) async -> UInt64 {
        // Immediate cancellation of any prior speech
        stopSpeaking()

        ttsGeneration &+= 1
        let activeGeneration = ttsGeneration

        let sanitizedText = TTSTextSanitizer.sanitizeForSpeech(text)
        guard !sanitizedText.isEmpty else {
            delegate?.speechOutputDidFinish(generation: activeGeneration)
            return activeGeneration
        }

        let utterance = AVSpeechUtterance(string: sanitizedText)
        utterance.rate = configuration.speechRate
        utterance.pitchMultiplier = configuration.speechPitch
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.current.identifier) ?? AVSpeechSynthesisVoice(language: "en-US")

        synthesizer.speak(utterance)
        return activeGeneration
    }

    /// Halts TTS playback immediately and increments generation fencing
    public func stopSpeaking() {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        ttsGeneration &+= 1
    }

    // MARK: - AVSpeechSynthesizerDelegate Callbacks

    public nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self = self else { return }
            self.delegate?.speechOutputDidStart(generation: self.ttsGeneration)
        }
    }

    public nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self = self else { return }
            self.delegate?.speechOutputDidFinish(generation: self.ttsGeneration)
        }
    }

    public nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self = self else { return }
            self.delegate?.speechOutputDidCancel(generation: self.ttsGeneration)
        }
    }
}

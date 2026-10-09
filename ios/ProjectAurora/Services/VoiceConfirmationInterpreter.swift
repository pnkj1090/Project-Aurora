import Foundation

public enum VoiceConfirmationIntent: Equatable, Sendable {
    case approve
    case reject
    case ambiguous(rawTranscript: String)
    case noActiveConfirmation
}

public struct VoiceConfirmationInterpreter: Sendable {
    private static let approvedGrammar: Set<String> = [
        "yes",
        "yeah",
        "yep",
        "sure",
        "confirm",
        "approve",
        "do it",
        "go ahead",
        "okay",
        "ok",
        "proceed",
        "please do",
        "yes please",
        "yes do it",
        "sounds good"
    ]

    private static let rejectionGrammar: Set<String> = [
        "no",
        "nope",
        "cancel",
        "reject",
        "don't do it",
        "do not do it",
        "never mind",
        "nevermind",
        "stop",
        "abort",
        "discard",
        "no thanks",
        "don't",
        "dont"
    ]

    private static let disqualifyingWords: Set<String> = [
        "but",
        "and",
        "instead",
        "also",
        "if",
        "or",
        "actually",
        "change",
        "modify",
        "rename",
        "move",
        "reschedule",
        "later",
        "tomorrow",
        "after",
        "before",
        "not",
        "maybe"
    ]

    private static let disqualifyingPhrases: [String] = [
        "make it",
        "what about",
        "how about",
        "change the",
        "at ",
        "to "
    ]

    public static func interpret(
        transcript: String,
        hasActiveConfirmation: Bool
    ) -> VoiceConfirmationIntent {
        guard hasActiveConfirmation else {
            return .noActiveConfirmation
        }

        let normalized = transcript
            .lowercased()
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: ".,!?;:'\""))

        guard !normalized.isEmpty else {
            return .ambiguous(rawTranscript: transcript)
        }

        // 1. Guard against multi-word disqualifying phrases
        for phrase in disqualifyingPhrases {
            if normalized.contains(phrase) {
                return .ambiguous(rawTranscript: transcript)
            }
        }

        // 2. Tokenize words and guard against conditional/modifying words
        let tokens = normalized
            .components(separatedBy: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",-")))
            .filter { !$0.isEmpty }

        for token in tokens {
            if disqualifyingWords.contains(token) {
                // Special case: "do not do it" is explicitly in rejectionGrammar, so if the whole string is in rejectionGrammar, allow it
                if rejectionGrammar.contains(normalized) {
                    return .reject
                }
                return .ambiguous(rawTranscript: transcript)
            }
        }

        // 3. Exact match against approved grammar
        if approvedGrammar.contains(normalized) {
            return .approve
        }

        // 4. Exact match against rejection grammar
        if rejectionGrammar.contains(normalized) {
            return .reject
        }

        // 5. Any outside speech is ambiguous
        return .ambiguous(rawTranscript: transcript)
    }
}

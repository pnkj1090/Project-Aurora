import Foundation
import AppIntents

/// App Shortcuts provider exposing Project Aurora's App Intents to Siri and Shortcuts.
public struct AuroraShortcutsProvider: AppShortcutsProvider {
    public static var shortcutTileColor: ShortcutTileColor {
        .navy
    }

    @AppShortcutsBuilder
    public static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: OpenAuroraVoiceIntent(),
            phrases: [
                "Talk to \(.applicationName)",
                "Open Voice in \(.applicationName)",
                "Start voice conversation with \(.applicationName)"
            ],
            shortTitle: "Voice Conversation",
            systemImageName: "waveform"
        )
        AppShortcut(
            intent: AskAuroraIntent(),
            phrases: [
                "Ask \(.applicationName)",
                "Send a message with \(.applicationName)",
                "Chat with \(.applicationName)"
            ],
            shortTitle: "Ask Aurora",
            systemImageName: "sparkles"
        )
        AppShortcut(
            intent: OpenAgendaIntent(),
            phrases: [
                "Show my agenda in \(.applicationName)",
                "Open agenda in \(.applicationName)"
            ],
            shortTitle: "Open Agenda",
            systemImageName: "calendar"
        )
    }
}

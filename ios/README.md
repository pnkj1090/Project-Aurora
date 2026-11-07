# Project Aurora — iOS Native Client

The iOS client is a modern, native **SwiftUI** application designed for **iOS 17+**. It provides conversational chat, hands-free voice interaction, proactive agenda timelines, Siri and App Shortcuts integration, and local device context bridges.

---

## Architecture Overview

The app follows the **MVVM (Model-View-ViewModel)** pattern with dedicated service layers and system providers:

```
ios/ProjectAurora/
├── ProjectAuroraApp.swift          # App entry point & root NavigationStack
├── Config/
│   └── AppConfig.swift             # Dynamic API base URL (Simulator vs Physical LAN)
├── Models/                         # Codable Swift data models matching backend schemas
│   ├── APIModels.swift             # Chat messages, tool definitions, confirm payloads
│   ├── AgendaModels.swift          # Timeline items, horizon plans, recovery proposals
│   ├── MemoryModels.swift          # Long-term memories & candidate review models
│   ├── VoiceModels.swift           # Voice session states and transcription buffers
│   └── ContextModels.swift         # Snapshots for Calendar, Contacts, Location, Assets
├── ViewModels/                     # ObservableObject view models managing UI state
│   ├── AssistantViewModel.swift    # Chat stream, tool execution cards, agent stepper
│   ├── AgendaViewModel.swift       # Daily agenda timeline, morning brief, evening debrief
│   ├── ScheduleRecoveryViewModel.swift # Conflict detection & recovery proposal flow
│   ├── MemoryViewModel.swift       # Memory list & candidate review approval
│   └── ...                         # Task, Commitment, Reminder, Contact, Location ViewModels
├── Views/                          # Modular SwiftUI views and sheets
│   ├── ContentView.swift           # Main chat interface, status bar, and navigation toolbar
│   ├── VoiceInteractionBarView.swift # Floating voice activity capsule
│   ├── AgendaTimelineView.swift    # Interactive diurnal schedule timeline
│   ├── EventBriefingSheet.swift    # Pre-meeting attendee & context brief
│   ├── ScheduleDisruptionSheet.swift # Transit & calendar conflict inspector
│   └── MemoryCandidateReviewSheet.swift # Review sheet for candidate memories
├── Services/                       # API client, audio managers, system bridges
│   ├── AuroraAPIClient.swift       # Asynchronous HTTP networking client
│   ├── VoiceSessionCoordinator.swift # SFSpeechRecognizer & audio session lifecycle
│   ├── SpeechOutputManager.swift   # AVSpeechSynthesizer text-to-speech speaker
│   ├── VoiceConfirmationInterpreter.swift # Hands-free natural voice approval parser
│   ├── KeychainCredentialStore.swift # Secure local storage for auth tokens
│   ├── CalendarProvider.swift      # EventKit calendar integration
│   ├── ContactsProvider.swift      # ContactsKit contact lookup & sync
│   ├── LocationProvider.swift      # CoreLocation spatial tracking
│   ├── PhotoProvider.swift         # PhotoKit asset picker & ephemeral ingest
│   └── DeepLinkNavigator.swift     # Uniform routing for intents & notifications
└── Intents/                        # iOS App Intents & Siri Shortcuts
    ├── AuroraIntents.swift         # OpenAuroraVoiceIntent, OpenAgendaIntent, AskAuroraIntent
    └── AuroraShortcutsProvider.swift # Siri App Shortcuts registration
```

---

## Setup & Running

### Prerequisites
* **macOS**: Sonoma 14.0 or newer
* **Xcode**: 15.0 or newer
* **Target OS**: iOS 17.0+ (Simulator or Physical Device)

### 1. Backend Connectivity

Open `ios/ProjectAurora/Config/AppConfig.swift`:
```swift
public static var backendBaseURL: URL {
    #if targetEnvironment(simulator)
    // Points directly to localhost when running in Simulator
    return URL(string: "http://127.0.0.1:8000")!
    #else
    // Set to your Mac's LAN IP when testing on a physical iPhone
    return URL(string: "http://192.168.1.8:8000")!
    #endif
}
```

* Ensure the backend server is running: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

### 2. Opening in Xcode

1. Open `ios/ProjectAurora.xcodeproj` in Xcode.
2. Select your build scheme (`ProjectAurora`) and target device (e.g. **iPhone 15 Pro Simulator** or your tethered device).
3. Press **Cmd + R** to build and run.

---

## App Intents & Siri Integration

Aurora implements native iOS **App Intents** (`AppIntents` framework):

* **`OpenAuroraVoiceIntent`**: Launches the app directly into the hands-free voice session experience.
* **`OpenAgendaIntent`**: Launches directly to the unified daily agenda timeline.
* **`AskAuroraIntent`**: Conforms to `ForegroundContinuableIntent`, executing background AI queries via Siri with automatic foreground handoff if tool authorization is required.

---

## Running iOS Tests

Unit and integration tests are located in `ios/ProjectAuroraTests/`:

```bash
# In Xcode:
Press Cmd + U

# Or via terminal using xcodebuild:
xcodebuild test \
  -project ios/ProjectAurora.xcodeproj \
  -scheme ProjectAurora \
  -destination 'platform=iOS Simulator,name=iPhone 15 Pro,OS=latest'
```


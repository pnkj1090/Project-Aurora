import SwiftUI

public struct VoiceInteractionBarView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator
    let onStop: () -> Void

    public init(coordinator: VoiceSessionCoordinator, onStop: @escaping () -> Void) {
        self.coordinator = coordinator
        self.onStop = onStop
    }

    public var body: some View {
        HStack(spacing: 12) {
            // Visual waveform / pulse indicator
            statusIcon
                .frame(width: 24, height: 24)

            // State and partial transcription
            VStack(alignment: .leading, spacing: 2) {
                Text(statusText)
                    .font(.caption.weight(.semibold))
                    .foregroundColor(statusColor)

                if case .transcribing(let partialText) = coordinator.state, !partialText.isEmpty {
                    Text(partialText)
                        .font(.subheadline)
                        .foregroundColor(.primary)
                        .lineLimit(1)
                } else if case .speaking(let speakingText) = coordinator.state, !speakingText.isEmpty {
                    Text(speakingText)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }

            Spacer()

            // Explicit Stop button
            Button {
                onStop()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(statusColor.opacity(0.3), lineWidth: 1)
        )
        .padding(.horizontal)
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch coordinator.state {
        case .listening:
            Image(systemName: "waveform")
                .symbolEffect(.variableColor.iterative.reversing)
                .foregroundColor(.blue)
        case .transcribing:
            Image(systemName: "waveform.badge.magnifyingglass")
                .foregroundColor(.blue)
        case .processing:
            ProgressView()
                .controlSize(.small)
        case .speaking:
            Image(systemName: "speaker.wave.2.fill")
                .foregroundColor(.green)
        case .interrupted:
            Image(systemName: "pause.circle.fill")
                .foregroundColor(.orange)
        case .failed:
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundColor(.red)
        default:
            Image(systemName: "mic.fill")
                .foregroundColor(.secondary)
        }
    }

    private var statusText: String {
        switch coordinator.state {
        case .idle:
            return "Voice Idle"
        case .preparing:
            return "Preparing..."
        case .requestingPermission:
            return "Requesting Permissions..."
        case .ready:
            return "Voice Ready"
        case .listening:
            return "Listening..."
        case .transcribing:
            return "Hearing..."
        case .processing:
            return "Processing..."
        case .speaking:
            return "Speaking..."
        case .interrupted:
            return "Interrupted"
        case .stopping:
            return "Stopping..."
        case .failed(let err):
            return "Error: \(err.localizedDescription)"
        }
    }

    private var statusColor: Color {
        switch coordinator.state {
        case .listening, .transcribing:
            return .blue
        case .speaking:
            return .green
        case .interrupted:
            return .orange
        case .failed:
            return .red
        default:
            return .secondary
        }
    }
}

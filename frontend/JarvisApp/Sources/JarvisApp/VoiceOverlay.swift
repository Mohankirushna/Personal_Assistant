import AppKit
import SwiftUI

/// A small, non-activating panel that can sit above any macOS app without
/// taking keyboard focus or moving the user away from their current work.
@MainActor
final class VoiceOverlayController {
    private var panel: NSPanel?

    func show(using state: AppState) {
        if panel == nil {
            let panel = NSPanel(
                contentRect: NSRect(x: 0, y: 0, width: 440, height: 178),
                styleMask: [.borderless, .nonactivatingPanel],
                backing: .buffered,
                defer: false
            )
            panel.isFloatingPanel = true
            panel.level = .floating
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            panel.isOpaque = false
            panel.backgroundColor = .clear
            panel.hasShadow = true
            panel.hidesOnDeactivate = false
            // Always clickable so the close button works — the panel is
            // small and corner-docked, so capturing clicks within its own
            // frame doesn't meaningfully get in the way of other apps.
            panel.ignoresMouseEvents = false
            panel.contentView = NSHostingView(rootView: VoiceOverlayView().environmentObject(state))
            self.panel = panel
        }
        guard let panel else { return }
        position(panel)
        panel.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    private func position(_ panel: NSPanel) {
        let screen = NSScreen.main?.visibleFrame ?? .zero
        let x = screen.maxX - panel.frame.width - 24
        let y = screen.maxY - panel.frame.height - 24
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }
}

private struct VoiceOverlayView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: appState.voiceOverlayIsReplying ? "waveform" : "mic.fill")
                    .foregroundStyle(appState.voiceOverlayIsReplying ? .green : .blue)
                Text(appState.voiceOverlayIsReplying ? "Jarvis" : "Listening")
                    .font(.headline)
                Spacer()
                Text("Hey Jarvis")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button {
                    appState.dismissVoiceOverlay()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
            if !appState.voiceOverlayTranscript.isEmpty {
                Text(appState.voiceOverlayTranscript)
                    .font(.subheadline)
                    .lineLimit(2)
            }
            if !appState.voiceOverlayReply.isEmpty {
                Text(appState.voiceOverlayReply)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            if appState.pendingConfirmation != nil {
                HStack {
                    Spacer()
                    Button("Deny") { appState.resolveConfirmation(approved: false) }
                        .buttonStyle(.bordered)
                    Button("Allow") { appState.resolveConfirmation(approved: true) }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .padding(14)
        .frame(width: 440, height: 178, alignment: .topLeading)
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(.white.opacity(0.16), lineWidth: 1)
        }
    }
}

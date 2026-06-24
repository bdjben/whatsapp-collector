import AppKit
import SwiftUI

struct AIPromptWindow: View {
    @EnvironmentObject private var store: CollectorStore
    @State private var draft = ""
    @State private var isEditing = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader(
                title: "AI Prompt",
                subtitle: "View, copy, or temporarily edit the prompt you can give to local AI agents.",
                systemImage: "doc.on.doc"
            )

            Text("Edited prompt text is not saved as an app default. Keep any edited prompt you care about somewhere else before closing this window.")
                .font(.callout)
                .foregroundStyle(.secondary)

            Group {
                if isEditing {
                    TextEditor(text: $draft)
                        .font(.system(.body, design: .monospaced))
                } else {
                    ScrollView {
                        Text(draft)
                            .font(.system(.body, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(6)
                    }
                }
            }
            .padding(6)
            .background(.background, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(Color(nsColor: .separatorColor).opacity(0.45))
            }

            HStack {
                Button {
                    if isEditing {
                        isEditing = false
                    } else if confirmEditing() {
                        isEditing = true
                    }
                } label: {
                    Label(isEditing ? "Done Editing" : "Edit", systemImage: isEditing ? "checkmark.circle" : "pencil")
                }

                Button {
                    draft = store.defaultAIPrompt()
                    isEditing = false
                } label: {
                    Label("Restore Default", systemImage: "arrow.counterclockwise")
                }

                Spacer()

                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(draft, forType: .string)
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(22)
        .onAppear {
            draft = store.aiPrompt
        }
        .background(PromptCloseGuard(shouldWarn: shouldWarnBeforeClose))
    }

    private func shouldWarnBeforeClose() -> Bool {
        draft != store.defaultAIPrompt()
    }

    private func confirmEditing() -> Bool {
        let alert = NSAlert()
        alert.messageText = "Temporary Prompt Editing"
        alert.informativeText = "Changes you make here do not become the default WhatsApp Collector prompt and are not saved by the app. Save edited prompt text somewhere else if you want to keep it."
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Edit Prompt")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }
}

private struct PromptCloseGuard: NSViewRepresentable {
    var shouldWarn: () -> Bool

    func makeCoordinator() -> Coordinator {
        Coordinator(shouldWarn: shouldWarn)
    }

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        DispatchQueue.main.async {
            context.coordinator.shouldWarn = shouldWarn
            view.window?.delegate = context.coordinator
        }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {
        context.coordinator.shouldWarn = shouldWarn
        DispatchQueue.main.async {
            view.window?.delegate = context.coordinator
        }
    }

    final class Coordinator: NSObject, NSWindowDelegate {
        var shouldWarn: () -> Bool

        init(shouldWarn: @escaping () -> Bool) {
            self.shouldWarn = shouldWarn
        }

        func windowShouldClose(_ sender: NSWindow) -> Bool {
            guard shouldWarn() else { return true }
            let alert = NSAlert()
            alert.messageText = "Close Prompt Editor?"
            alert.informativeText = "The prompt editor is a convenience, not a repository of saved edited prompts. Your edited prompt text will be lost when this window closes unless you copied it elsewhere."
            alert.alertStyle = .warning
            alert.addButton(withTitle: "Keep Editing")
            alert.addButton(withTitle: "Close Without Saving")
            return alert.runModal() == .alertSecondButtonReturn
        }
    }
}

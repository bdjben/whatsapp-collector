import SwiftUI

struct DiagnosticsView: View {
    @EnvironmentObject private var store: CollectorStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(
                title: "Diagnostics",
                subtitle: "Raw bridge responses, paths, and recent errors for troubleshooting.",
                systemImage: "stethoscope"
            )
            .padding([.horizontal, .top], 22)

            StatusBanner(busyState: store.busyState, error: store.lastError)
                .padding(.horizontal, 22)

            GroupBox {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 10) {
                    GridRow {
                        Text("Debug port")
                            .foregroundStyle(.secondary)
                        Text("\(store.configuration.debugPort)")
                    }
                    GridRow {
                        Text("Target URL")
                            .foregroundStyle(.secondary)
                        Text(store.configuration.targetUrl)
                            .textSelection(.enabled)
                    }
                    GridRow {
                        Text("Marker")
                            .foregroundStyle(.secondary)
                        Text("\(store.configuration.markerTitle) · \(store.configuration.markerUrlSubstring)")
                            .textSelection(.enabled)
                    }
                    GridRow {
                        Text("Export")
                            .foregroundStyle(.secondary)
                        Text(DisplayFormatters.shortPath(store.configuration.outputPath))
                            .textSelection(.enabled)
                    }
                }
                .padding(.vertical, 4)
            } label: {
                Text("Configuration")
            }
            .padding(.horizontal, 22)

            TextEditor(text: $store.diagnostics)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
                .padding(8)
                .overlay {
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color(nsColor: .separatorColor).opacity(0.45))
                }
                .padding([.horizontal, .bottom], 22)
        }
    }
}

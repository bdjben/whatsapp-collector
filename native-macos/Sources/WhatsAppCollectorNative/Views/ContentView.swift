import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: CollectorStore
    @SceneStorage("selectedSection") private var selectedSectionRaw = AppSection.dashboard.rawValue

    private var selection: Binding<AppSection> {
        Binding(
            get: { AppSection(rawValue: selectedSectionRaw) ?? .dashboard },
            set: { selectedSectionRaw = $0.rawValue }
        )
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: selection)
        } detail: {
            detail
                .navigationTitle(selection.wrappedValue.navigationTitle)
                .toolbar {
                    ToolbarItemGroup {
                        Button {
                            Task { await store.refreshStatus() }
                        } label: {
                            Label("Refresh", systemImage: "arrow.clockwise")
                        }
                        .help("Refresh status")

                        Button {
                            Task { await store.runExport() }
                        } label: {
                            Label("Run Export", systemImage: "square.and.arrow.down")
                        }
                        .help("Run export")
                        .disabled(store.isBusy)
                    }
                }
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch selection.wrappedValue {
        case .dashboard:
            DashboardView()
        case .labels:
            LabelsView()
        case .export:
            ExportPreviewView()
        case .automation:
            AutomationView()
        case .diagnostics:
            DiagnosticsView()
        case .help:
            HelpView()
        }
    }
}

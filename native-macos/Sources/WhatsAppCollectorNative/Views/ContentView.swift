import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: CollectorStore
    @EnvironmentObject private var updateMonitor: UpdateMonitor
    @Environment(\.appActions) private var appActions

    private var selection: Binding<AppSection> {
        Binding(
            get: { store.selectedSection },
            set: { store.selectedSection = $0 }
        )
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: selection)
        } detail: {
            VStack(spacing: 0) {
                UpdateStatusBanner(
                    state: updateMonitor.state,
                    checkNow: appActions.checkForUpdates,
                    openRelease: appActions.openLatestRelease
                )
                .padding(.horizontal, 22)
                .padding(.top, 14)
                .padding(.bottom, 10)

                Divider()

                detail
            }
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

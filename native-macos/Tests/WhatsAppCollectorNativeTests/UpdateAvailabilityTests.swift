import Foundation
import Sparkle
import Testing
@testable import WhatsAppCollectorNative

@Test func release122IsAnUpgradeForExistingInstallations() {
    #expect(VersionComparator.isVersion("1.1.22", newerThan: "0.4.21"))
    #expect(SUStandardVersionComparator().compareVersion("1.1.22", toVersion: "0.4.21") == .orderedDescending)
    #expect(VersionComparator.isVersion("1.1.22", newerThan: "1.1.22") == false)
}

@Test func release122AppcastDrivesTheUpdateBanner() throws {
    let xml = """
    <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item>
    <title>1.1.22</title><sparkle:version>1.1.22</sparkle:version>
    <sparkle:shortVersionString>1.1.22</sparkle:shortVersionString>
    <enclosure url="https://github.com/bdjben/whatsapp-collector/releases/download/v1.1.22/WhatsApp-Collector-macOS.zip" length="1" />
    </item></channel></rss>
    """
    let update = try UpdateAvailabilityService().parseLatestUpdate(from: xml)
    let state = UpdateAvailabilityState(currentVersion: "0.4.21", latestVersion: update.version)
    #expect(state.updateAvailable)
    #expect(state.title == "Update available: Version 1.1.22")
    #expect(update.downloadURL?.lastPathComponent == "WhatsApp-Collector-macOS.zip")
}

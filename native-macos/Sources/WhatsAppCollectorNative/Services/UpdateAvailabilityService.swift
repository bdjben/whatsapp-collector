import Foundation

struct UpdateAvailabilityService: Sendable {
    func latestUpdate(from appcastURL: URL) async throws -> AppcastUpdate {
        var request = URLRequest(url: appcastURL)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 20
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        guard let xml = String(data: data, encoding: .utf8) else {
            throw URLError(.cannotDecodeContentData)
        }
        return try parseLatestUpdate(from: xml)
    }

    func parseLatestUpdate(from xml: String) throws -> AppcastUpdate {
        let item = firstMatch(#"<item\b[^>]*>(.*?)</item>"#, in: xml) ?? xml
        let version = firstMatch(#"<sparkle:shortVersionString>([^<]+)</sparkle:shortVersionString>"#, in: item)
            ?? firstMatch(#"<sparkle:version>([^<]+)</sparkle:version>"#, in: item)
        guard let version, version.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false else {
            throw URLError(.cannotParseResponse)
        }
        let title = firstMatch(#"<title>([^<]+)</title>"#, in: item)
        let urlString = firstMatch(#"<enclosure\b[^>]*\burl="([^"]+)""#, in: item)
        return AppcastUpdate(
            version: decodeXML(version),
            title: title.map(decodeXML),
            downloadURL: urlString.flatMap { URL(string: decodeXML($0)) }
        )
    }

    private func firstMatch(_ pattern: String, in text: String) -> String? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]) else {
            return nil
        }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard
            let match = regex.firstMatch(in: text, options: [], range: range),
            match.numberOfRanges > 1,
            let resultRange = Range(match.range(at: 1), in: text)
        else {
            return nil
        }
        return String(text[resultRange]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func decodeXML(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&apos;", with: "'")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
    }
}

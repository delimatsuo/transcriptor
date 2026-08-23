import Foundation

public struct JoinRequest: Equatable, Sendable {
    public let sessionID: String
    public let streamKey: String
    public let gateway: String?   // nil → caller's default

    public init(sessionID: String, streamKey: String, gateway: String? = nil) {
        self.sessionID = sessionID
        self.streamKey = streamKey
        self.gateway = gateway
    }
}

public enum JoinLink {
    /// Accepts either a full deep link `tars-companion://join?session=X&key=Y[&gateway=Z]`
    /// or the compact form `X:Y`. Trims whitespace. Returns nil for anything else
    /// (missing/empty session or key, wrong scheme/host, malformed URL).
    public static func parse(_ input: String) -> JoinRequest? {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        if trimmed.contains("://") {
            guard let components = URLComponents(string: trimmed),
                  components.scheme?.lowercased() == "tars-companion",
                  components.host?.lowercased() == "join" else {
                return nil
            }
            guard let queryItems = components.queryItems else { return nil }
            guard let session = queryItems.first(where: { $0.name == "session" })?.value,
                  !session.isEmpty,
                  let key = queryItems.first(where: { $0.name == "key" })?.value,
                  !key.isEmpty else {
                return nil
            }
            let gateway = queryItems.first(where: { $0.name == "gateway" })?.value
            let cleanGateway = (gateway != nil && !gateway!.isEmpty) ? gateway : nil
            return JoinRequest(sessionID: session, streamKey: key, gateway: cleanGateway)
        } else {
            let parts = trimmed.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            guard parts.count == 2 else { return nil }
            let session = String(parts[0])
            let key = String(parts[1])
            guard !session.isEmpty, !key.isEmpty else { return nil }
            return JoinRequest(sessionID: session, streamKey: key, gateway: nil)
        }
    }
}

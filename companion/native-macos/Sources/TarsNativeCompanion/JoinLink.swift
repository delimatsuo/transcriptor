import Foundation

public struct JoinRequest: Equatable, Sendable {
    public let sessionID: String
    public let streamKey: String

    public init(sessionID: String, streamKey: String) {
        self.sessionID = sessionID
        self.streamKey = streamKey
    }
}

public enum JoinLink {
    /// Accepts either a full deep link `tars-companion://join?session=X&key=Y`
    /// or the compact form `X:Y`. Trims whitespace. Returns nil for anything else
    /// (missing/empty session or key, wrong scheme/host, malformed URL, or any gateway query item).
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
            if queryItems.contains(where: { $0.name.lowercased() == "gateway" }) {
                return nil
            }
            guard let session = queryItems.first(where: { $0.name == "session" })?.value,
                  !session.isEmpty,
                  let key = queryItems.first(where: { $0.name == "key" })?.value,
                  !key.isEmpty else {
                return nil
            }
            return JoinRequest(sessionID: session, streamKey: key)
        } else {
            let parts = trimmed.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            guard parts.count == 2 else { return nil }
            let session = String(parts[0])
            let key = String(parts[1])
            guard !session.isEmpty, !key.isEmpty else { return nil }
            return JoinRequest(sessionID: session, streamKey: key)
        }
    }

    public static func receive(
        _ input: String,
        log: (String) -> Void = { NSLog("%@", $0) },
        onAdmit: (JoinRequest) -> Void
    ) {
        log("TarsCompanion: URL recebida")
        guard let request = parse(input) else {
            log("TarsCompanion: link inválido")
            return
        }
        onAdmit(request)
    }
}

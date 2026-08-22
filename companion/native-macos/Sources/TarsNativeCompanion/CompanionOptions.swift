import Foundation

/// Pure, testable command-line option parsing for the `tars-companion` CLI.
/// No I/O happens here — permission preflight, capture startup, and printing
/// all remain the executable target's responsibility (`TarsCompanionCLI`).
public struct CompanionOptions: Equatable, Sendable {
    public enum Sources: String, Sendable {
        case systemAudio = "system_audio"
        case microphone
        case both
    }

    public var sessionID: String = "default"
    public var gatewayBase: String = "ws://127.0.0.1:8000/api/stream/native"
    public var streamKey: String = ""
    public var sources: Sources = .systemAudio

    public init(
        sessionID: String = "default",
        gatewayBase: String = "ws://127.0.0.1:8000/api/stream/native",
        streamKey: String = "",
        sources: Sources = .systemAudio
    ) {
        self.sessionID = sessionID
        self.gatewayBase = gatewayBase
        self.streamKey = streamKey
        self.sources = sources
    }

    /// Parses CLI arguments (including the program name at index 0, matching
    /// `CommandLine.arguments`). Recognized flags: `--session-id`,
    /// `--gateway`, `--stream-key`, `--sources system_audio|microphone|both`.
    /// `--token` is a deprecated alias for `--stream-key`; using it prints a
    /// one-line deprecation note to stderr. Unknown tokens are ignored so
    /// future/unrelated flags don't break parsing. A recognized flag missing
    /// its value, or an unrecognized `--sources` value, throws
    /// `CompanionError.invalid` — silent misconfiguration is not acceptable
    /// once the gateway requires a stream key to authenticate.
    public static func parse(_ args: [String]) throws -> CompanionOptions {
        var options = CompanionOptions()
        var index = 1
        while index < args.count {
            let arg = args[index]
            switch arg {
            case "--session-id":
                guard index + 1 < args.count else {
                    throw CompanionError.invalid("--session-id requires a value")
                }
                options.sessionID = args[index + 1]
                index += 2
            case "--gateway":
                guard index + 1 < args.count else {
                    throw CompanionError.invalid("--gateway requires a value")
                }
                options.gatewayBase = args[index + 1]
                index += 2
            case "--stream-key":
                guard index + 1 < args.count else {
                    throw CompanionError.invalid("--stream-key requires a value")
                }
                options.streamKey = args[index + 1]
                index += 2
            case "--token":
                guard index + 1 < args.count else {
                    throw CompanionError.invalid("--token requires a value")
                }
                FileHandle.standardError.write(Data(
                    "[tars-companion] --token is deprecated; use --stream-key instead.\n".utf8
                ))
                options.streamKey = args[index + 1]
                index += 2
            case "--sources":
                guard index + 1 < args.count else {
                    throw CompanionError.invalid("--sources requires a value")
                }
                let rawValue = args[index + 1]
                guard let parsed = Sources(rawValue: rawValue) else {
                    throw CompanionError.invalid(
                        "unknown --sources value '\(rawValue)' (expected system_audio, microphone, or both)"
                    )
                }
                options.sources = parsed
                index += 2
            default:
                index += 1
            }
        }
        return options
    }

    /// Builds `<gatewayBase>/<sessionID>`, appending `?stream_key=` with the
    /// key percent-encoded when non-empty. `URLComponents`'s default query
    /// encoding leaves `/`, `+`, and `=` unescaped (they're legal generic-URI
    /// query characters), which would corrupt a stream key containing them
    /// or make it ambiguous with the `stream_key=` delimiter itself. Encoding
    /// against the RFC 3986 unreserved set via `percentEncodedQuery` (set
    /// directly, bypassing `queryItems`' own looser encoding) guarantees every
    /// byte of the key survives intact.
    public func gatewayURL() throws -> URL {
        let base = "\(gatewayBase)/\(sessionID)"
        guard var components = URLComponents(string: base) else {
            throw CompanionError.invalid("invalid gateway URL: \(base)")
        }
        if streamKey.isEmpty {
            components.percentEncodedQuery = nil
        } else {
            guard let encodedKey = streamKey.addingPercentEncoding(withAllowedCharacters: .companionUnreserved) else {
                throw CompanionError.invalid("unable to percent-encode stream key")
            }
            components.percentEncodedQuery = "stream_key=\(encodedKey)"
        }
        guard let url = components.url else {
            throw CompanionError.invalid("invalid gateway URL: \(base)")
        }
        return url
    }
}

private extension CharacterSet {
    /// RFC 3986 `unreserved` characters: ALPHA / DIGIT / "-" / "." / "_" / "~".
    /// Built from explicit ASCII ranges rather than `.alphanumerics`, which is
    /// a Unicode-general set (accented letters, other scripts) and would let
    /// non-ASCII bytes through un-encoded — invalid in a URL and outside the
    /// RFC 3986 definition this set is meant to express. Deliberately
    /// narrower than `.urlQueryAllowed` so every reserved and sub-delim
    /// character in a stream key gets percent-encoded too.
    static let companionUnreserved = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )
}

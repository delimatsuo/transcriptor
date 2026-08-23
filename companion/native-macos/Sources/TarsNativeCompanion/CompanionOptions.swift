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

    /// Builds the keyless `<gatewayBase>/<sessionID>` WebSocket URL.
    public func gatewayURL() throws -> URL {
        let base = "\(gatewayBase)/\(sessionID)"
        guard let url = URL(string: base) else {
            throw CompanionError.invalid("invalid gateway URL: \(base)")
        }
        return url
    }

    /// Derives the two-element WebSocket subprotocol array `["tars-stream", streamKey]`
    /// for gateway authentication.
    public func webSocketProtocols() throws -> [String] {
        return try NativeStreamHandshake.protocols(streamKey: streamKey)
    }
}

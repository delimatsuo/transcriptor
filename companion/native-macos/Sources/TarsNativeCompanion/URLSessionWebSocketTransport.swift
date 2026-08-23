import Foundation

public enum NativeStreamHandshake {
    private static func isValidTokenScalar(_ scalar: Unicode.Scalar) -> Bool {
        let val = scalar.value
        return (val >= 0x61 && val <= 0x7A) // a-z
            || (val >= 0x41 && val <= 0x5A) // A-Z
            || (val >= 0x30 && val <= 0x39) // 0-9
            || val == 0x5F                  // _
            || val == 0x2D                  // -
    }

    public static func protocols(streamKey: String) throws -> [String] {
        guard !streamKey.isEmpty else {
            throw CompanionError.invalid("stream key must not be empty")
        }
        guard streamKey.unicodeScalars.allSatisfy({ isValidTokenScalar($0) }) else {
            throw CompanionError.invalid("stream key contains invalid characters")
        }
        return ["tars-stream", streamKey]
    }
}

/// One gateway connection, built fresh by `ReconnectingAudioSink` on every
/// (re)connect. `resume()` alone is optimistic — URLSession happily "starts" a
/// WebSocket task against a dead host and only reports the failure on the first
/// read/write — so `connect()` is not considered successful until a ping has
/// made the round trip.
@available(macOS 13.0, *)
public final class URLSessionWebSocketTransport: AudioStreamTransport, @unchecked Sendable {
    private let url: URL
    private let protocols: [String]
    private let session: URLSession
    private let lock = NSLock()
    private var webSocketTask: URLSessionWebSocketTask?

    public init(
        url: URL,
        protocols: [String] = [],
        session: URLSession = URLSession(configuration: .default)
    ) {
        self.url = url
        self.protocols = protocols
        self.session = session
    }

    public func connect() async throws {
        let task: URLSessionWebSocketTask
        if protocols.isEmpty {
            task = session.webSocketTask(with: url)
        } else {
            task = session.webSocketTask(with: url, protocols: protocols)
        }
        lock.withLock { webSocketTask = task }
        task.resume()
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            task.sendPing { error in
                if let error = error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    public func send(_ data: Data) async throws {
        try await send(.data(data))
    }

    public func sendText(_ text: String) async throws {
        try await send(.string(text))
    }

    private func send(_ message: URLSessionWebSocketTask.Message) async throws {
        guard let task = lock.withLock({ webSocketTask }) else {
            throw CompanionError.invalid("gateway transport is not connected")
        }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            task.send(message) { error in
                if let error = error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    public func cancel() {
        let task = lock.withLock { () -> URLSessionWebSocketTask? in
            let current = webSocketTask
            webSocketTask = nil
            return current
        }
        task?.cancel(with: .goingAway, reason: nil)
    }
}

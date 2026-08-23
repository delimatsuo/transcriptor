import Foundation

/// One gateway connection, built fresh by `ReconnectingAudioSink` on every
/// (re)connect. `resume()` alone is optimistic — URLSession happily "starts" a
/// WebSocket task against a dead host and only reports the failure on the first
/// read/write — so `connect()` is not considered successful until a ping has
/// made the round trip.
@available(macOS 13.0, *)
public final class URLSessionWebSocketTransport: AudioStreamTransport, @unchecked Sendable {
    private let url: URL
    private let session: URLSession
    private let lock = NSLock()
    private var webSocketTask: URLSessionWebSocketTask?

    public init(url: URL, session: URLSession = URLSession(configuration: .default)) {
        self.url = url
        self.session = session
    }

    public func connect() async throws {
        let task = session.webSocketTask(with: url)
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

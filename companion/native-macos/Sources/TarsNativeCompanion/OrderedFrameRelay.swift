import Foundation

/// Bridges a synchronous audio callback to an `async` sink **without losing
/// order**.
///
/// The obvious bridge — `for frame in frames { Task { await sink.receive(frame) } }`
/// — spawns one unstructured task per frame, and unstructured tasks carry no
/// ordering guarantee whatsoever: two 50 ms PCM frames can reach the sink
/// swapped. Downstream that is not a crash, it is a subtly garbled transcript,
/// which is the worst kind of bug to diagnose.
///
/// This relay accepts frames synchronously (safe to call straight from a
/// ScreenCaptureKit sample handler or an AVAudioEngine tap), buffers them in an
/// `AsyncStream`, and forwards them from a single long-lived task. Yield order
/// is delivery order, full stop.
public final class OrderedFrameRelay: @unchecked Sendable {

    /// Audio and coverage gaps travel the same channel so a gap keeps its
    /// position relative to the audio it describes.
    private enum Item: Sendable {
        case frame(AudioFrame)
        case gap(CoverageGap)
    }

    private let continuation: AsyncStream<Item>.Continuation
    private let forwarder: Task<Void, Never>

    public init(sink: CaptureFrameSink) {
        var escapedContinuation: AsyncStream<Item>.Continuation!
        // Unbounded: dropping audio is the sink's decision (it has the buffer
        // policy and the gap accounting), never a silent side effect here.
        let stream = AsyncStream<Item>(bufferingPolicy: .unbounded) { continuation in
            escapedContinuation = continuation
        }
        self.continuation = escapedContinuation
        self.forwarder = Task {
            for await item in stream {
                switch item {
                case .frame(let frame):
                    try? await sink.receive(frame)
                case .gap(let gap):
                    try? await sink.receiveGap(gap)
                }
            }
        }
    }

    /// Synchronous and non-blocking — safe on a realtime audio callback.
    public func yield(_ frame: AudioFrame) {
        continuation.yield(.frame(frame))
    }

    public func yieldGap(_ gap: CoverageGap) {
        continuation.yield(.gap(gap))
    }

    /// Closes the stream and waits for everything already yielded to reach the
    /// sink, so a caller's `stop()` does not race the tail of the buffer.
    public func finish() async {
        continuation.finish()
        await forwarder.value
    }
}

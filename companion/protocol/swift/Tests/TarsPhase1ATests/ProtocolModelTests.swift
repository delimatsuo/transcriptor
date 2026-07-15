import Foundation
import TarsPhase1A
import XCTest

private struct VectorStream: Decodable {
    let sessionId: String
    let streamId: String
    let captureGeneration: Int
    let source: Source
}

private struct VectorManifest: Decodable {
    let vectorVersion: String
    let identityEncoding: String
    let stream: VectorStream
    let knownCoverage: KnownCoverageMetadata
    let expectedAudioEventId: String
    let expectedTranscriptTerminalId: String
    let expectedUnknownGapTerminalId: String
    let audioMetadata: AudioChunkMetadata
    let transcriptMetadata: TerminalOutcomeMetadata
    let unknownGapMetadata: TerminalOutcomeMetadata
}

final class ProtocolModelTests: XCTestCase {
    private func protocolRoot() -> URL {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<4 {
            url.deleteLastPathComponent()
        }
        return url
    }

    private func vectorData() throws -> Data {
        try Data(
            contentsOf: protocolRoot()
                .appendingPathComponent("vectors")
                .appendingPathComponent("protocol-v1-vectors.json")
        )
    }

    private func manifest() throws -> VectorManifest {
        try JSONDecoder().decode(VectorManifest.self, from: vectorData())
    }

    private func streamKey(_ manifest: VectorManifest) throws -> StreamKey {
        try StreamKey(
            sessionId: manifest.stream.sessionId,
            streamId: manifest.stream.streamId,
            captureGeneration: manifest.stream.captureGeneration,
            source: manifest.stream.source
        )
    }

    func testSharedCanonicalIdentityVectors() throws {
        let manifest = try manifest()
        XCTAssertEqual(manifest.vectorVersion, "phase1a-protocol-v1")
        XCTAssertEqual(manifest.identityEncoding, "utf8-fields-separated-by-nul")
        let key = try streamKey(manifest)
        let known = try manifest.knownCoverage.validated(key: key)

        XCTAssertEqual(try known.coverageId, manifest.knownCoverage.coverageId)
        XCTAssertEqual(
            try deterministicEventId(eventType: "audio.chunk", key: key, identity: "0"),
            manifest.expectedAudioEventId
        )
        XCTAssertEqual(
            try deterministicTerminalId(
                kind: .transcript,
                coverageToken: known.coverageId,
                resultOrdinal: 0
            ),
            manifest.expectedTranscriptTerminalId
        )

        guard case .unknownEnd(let unknownMetadata) = manifest.unknownGapMetadata.coverage else {
            return XCTFail("expected unknown-end gap coverage")
        }
        let unknown = try unknownMetadata.validated(key: key)
        XCTAssertEqual(
            try deterministicTerminalId(
                kind: .gap,
                coverageToken: unknown.identityToken,
                resultOrdinal: 0
            ),
            manifest.expectedUnknownGapTerminalId
        )
    }

    func testSwiftBindingsValidateSharedMetadataAndSchemaFields() throws {
        let manifest = try manifest()
        XCTAssertNoThrow(try manifest.audioMetadata.validate())
        XCTAssertNoThrow(try manifest.transcriptMetadata.validate())
        XCTAssertNoThrow(try manifest.unknownGapMetadata.validate())

        let schemaData = try Data(
            contentsOf: protocolRoot()
                .appendingPathComponent("schema")
                .appendingPathComponent("protocol-v1.schema.json")
        )
        let schema = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: schemaData) as? [String: Any]
        )
        let definitions = try XCTUnwrap(schema["$defs"] as? [String: Any])

        try assertBinding(
            manifest.audioMetadata,
            definition: "audioChunk",
            definitions: definitions
        )
        try assertBinding(
            manifest.transcriptMetadata,
            definition: "terminalOutcome",
            definitions: definitions
        )
        try assertBinding(
            manifest.unknownGapMetadata,
            definition: "terminalOutcome",
            definitions: definitions
        )
    }

    func testInvalidIdentityBoundsAndTamperedMetadataFailClosed() throws {
        XCTAssertThrowsError(
            try StreamKey(
                sessionId: "bad id",
                streamId: "stream_mic",
                captureGeneration: 0,
                source: .microphone
            )
        )
        let key = try streamKey(manifest())
        XCTAssertThrowsError(
            try KnownCoverage(
                key: key,
                firstSequence: 0,
                lastSequenceInclusive: 0,
                firstSample: 0,
                lastSampleExclusive: 800,
                firstCapturedAtMonotonicNs: 100,
                lastCapturedAtMonotonicNs: 99
            )
        )
        XCTAssertThrowsError(
            try deterministicEventId(
                eventType: "audio.chunk",
                key: key,
                identity: String(repeating: "x", count: 129)
            )
        )

        let original = String(decoding: try vectorData(), as: UTF8.self)
        let tampered = original.replacingOccurrences(
            of: "evt_9a728a2e6bcaa94c0c0da221164f1f9e84cf4fc5a37da4dfd1b9ca3d85ccd2d9",
            with: "evt_0000000000000000000000000000000000000000000000000000000000000000"
        )
        let tamperedManifest = try JSONDecoder().decode(
            VectorManifest.self,
            from: Data(tampered.utf8)
        )
        XCTAssertThrowsError(try tamperedManifest.audioMetadata.validate())
    }

    func testBindingRoundTripsAreDeterministicAndContentFree() throws {
        let manifest = try manifest()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]

        let first = try encoder.encode(manifest.transcriptMetadata)
        let decoded = try JSONDecoder().decode(TerminalOutcomeMetadata.self, from: first)
        let second = try encoder.encode(decoded)
        XCTAssertEqual(first, second)
        XCTAssertLessThanOrEqual(first.count, 65_536)

        let rendered = String(decoding: first, as: UTF8.self)
        for forbidden in ["transcriptText", "noteText", "payload", "credential"] {
            XCTAssertFalse(rendered.contains(forbidden))
        }
    }

    private func assertBinding<Value: Encodable>(
        _ value: Value,
        definition: String,
        definitions: [String: Any]
    ) throws {
        let selected = try XCTUnwrap(definitions[definition] as? [String: Any])
        let allOf = try XCTUnwrap(selected["allOf"] as? [[String: Any]])
        let concrete = try XCTUnwrap(allOf.last)
        let required = Set(try XCTUnwrap(concrete["required"] as? [String]))
        let properties = Set(
            try XCTUnwrap(concrete["properties"] as? [String: Any]).keys
        )
        let encoded = try JSONEncoder().encode(value)
        let object = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        )
        let keys = Set(object.keys)
        XCTAssertTrue(required.isSubset(of: keys))
        XCTAssertTrue(keys.isSubset(of: properties))
    }
}

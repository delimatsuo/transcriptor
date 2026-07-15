# Phase 1A Synthetic Byte Fixture Manifest

**Status:** Fixed input specification for offline protocol conformance; contains no recorded or generated speech

**Version:** `phase1a-bytes-v1`

**Authorization boundary:** This manifest is documentation. It does not authorize implementation, network access, provider calls, native capture, cloud mutation, push, deployment, or real data.

## Purpose

Phase 1A validates framing, ordering, retries, fencing, bounds, coverage, and terminal outcomes. It does not measure transcription quality. Tests generate the byte sequences below in memory, verify the SHA-256 digest before use, and reject every unlisted fixture or digest.

All fixtures are 3,200 bytes. When a test needs audio metadata, it labels the bytes as 100 milliseconds of 16 kHz, mono, signed 16-bit little-endian PCM; the deterministic provider simulator treats the bytes as opaque input and never interprets speech.

| Fixture ID | Language-independent generation rule | SHA-256 |
| --- | --- | --- |
| `zero-3200-v1` | 3,200 bytes of `0x00` | `5a312281df4bd8dfbb4d4a94ad0bf44d01bb8cfced1206b90e21b4ca0568cdb1` |
| `counter-3200-v1` | For zero-based byte index `i`, emit `i mod 256` | `78ad7b2c3cf464e4e219f6044605741a65a8197287a6951d142870af42c3397d` |
| `lcg-3200-v1` | Start `x = 0x13579bdf`; for each byte set `x = (1103515245*x + 12345) mod 2^31`, then emit `x mod 256` | `0a93dffb664217df4f004a088bdbf71c1a44b2416af59def297cd3668ede05fd` |

## Required handling

- Generate fixtures in memory; do not commit generated payloads or write them to disk.
- Verify the complete digest before framing or chunking.
- Keep fixture digests in test manifests and evidence reports, not runtime logs.
- Use fixture IDs, synthetic session IDs, and fake organization/user IDs from a reserved test namespace only.
- Reject environment-provided paths, arbitrary bytes, microphones, system-audio inputs, documents, transcript text, and candidate/customer identifiers.
- Run the conformance process with networking disabled and abort on credential, ADC, secret, project, or endpoint lookup.

## Derived cases

Tests may derive chunks, duplicates, omissions, reordering, corruption, truncation, and bounded overflows from a verified fixture. Every derived case records the base fixture ID and deterministic transformation parameters in the test report. A transformed payload is never accepted as a new base fixture without a new manifest version and digest.

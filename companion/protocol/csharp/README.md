# Pure C# protocol-v2 vector runner

`ProtocolV2Vectors.csproj` and `Program.cs` implement only canonical protocol
identity bytes and the committed v2 vectors. The runner has no package
references, networking, device APIs, provider APIs, filesystem access, or
credential lookup. It is not a WASAPI implementation. Run it only in the
network-denied G2-A harness when a .NET SDK is available.

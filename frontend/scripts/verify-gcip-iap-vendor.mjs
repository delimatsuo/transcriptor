import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const vendorRoot = resolve(frontendRoot, "vendor/gcip-iap/2.0.1");
const artifactPath = resolve(vendorRoot, "index.mjs");
const licensePath = resolve(vendorRoot, "LICENSE");
const provenancePath = resolve(vendorRoot, "PROVENANCE.json");

const expected = {
  name: "gcip-iap",
  version: "2.0.1",
  upstreamRepository: "https://github.com/GoogleCloudPlatform/iap-gcip-web-toolkit",
  npmTarballUrl: "https://registry.npmjs.org/gcip-iap/-/gcip-iap-2.0.1.tgz",
  npmTarballIntegrity:
    "sha512-/bTN8SNl0xm/On9olQrttVIEKMsLPcclHopZ2K0R9Z0pC3f10rsH4BAsePkdTz9f8sdXhaazaT898i3xznznLg==",
  sourceMember: "package/dist/index.esm.js",
  artifactSha256: "619d6959518fb09feb9127fca70ace4714bd443b4515fafa3786b1203ce04048",
  licenseMember: "package/LICENSE",
  licenseSha256: "accc36c817ac5ede473d05732e14afc11cb6e55ef5919bc826b653f631165043",
  artifact_modified: false,
  permittedBareImports: ["whatwg-fetch", "url-polyfill", "promise-polyfill"],
};

function fail(message) {
  throw new Error(`gcip-iap vendor verification failed: ${message}`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function readProvenance() {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(provenancePath, "utf8"));
  } catch (error) {
    fail(`PROVENANCE.json is absent or invalid (${error instanceof Error ? error.message : "unknown error"})`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    fail("PROVENANCE.json must contain an object");
  }
  return parsed;
}

function assertEqual(actual, expectedValue, label) {
  if (actual !== expectedValue) fail(`${label} does not match the approved value`);
}

const provenance = readProvenance();
for (const key of [
  "name",
  "version",
  "upstreamRepository",
  "npmTarballUrl",
  "npmTarballIntegrity",
  "sourceMember",
  "artifactSha256",
  "licenseMember",
  "licenseSha256",
  "artifact_modified",
]) {
  assertEqual(provenance[key], expected[key], `provenance.${key}`);
}
if (
  !Array.isArray(provenance.permittedBareImports) ||
  provenance.permittedBareImports.length !== expected.permittedBareImports.length ||
  provenance.permittedBareImports.some((value, index) => value !== expected.permittedBareImports[index])
) {
  fail("provenance.permittedBareImports does not match the approved import set");
}

let artifact;
let license;
try {
  artifact = readFileSync(artifactPath);
  license = readFileSync(licensePath);
} catch (error) {
  fail(`approved artifact or license is absent (${error instanceof Error ? error.message : "unknown error"})`);
}
assertEqual(sha256(artifact), expected.artifactSha256, "index.mjs SHA-256");
assertEqual(sha256(license), expected.licenseSha256, "LICENSE SHA-256");

const source = artifact.toString("utf8");
const bareImports = [...source.matchAll(/\bimport\s*["']([^"']+)["']/g)].map(
  (match) => match[1],
);
if (
  bareImports.length !== expected.permittedBareImports.length ||
  bareImports.some((value, index) => value !== expected.permittedBareImports[index])
) {
  fail(`unexpected bare imports: ${JSON.stringify(bareImports)}`);
}
for (const forbidden of ["require(", "node-forge", "vm2", "@types/node"]) {
  if (source.includes(forbidden)) fail(`forbidden token ${forbidden} is present`);
}

console.log(
  `gcip-iap 2.0.1 vendor verified: index.mjs ${expected.artifactSha256}, LICENSE ${expected.licenseSha256}`,
);

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { isMap, isScalar, isSeq, parseDocument, visit } from "yaml";
import {
  GOOGLE_PROVIDER_ID,
  assertGoogleOnlyProviderSelection,
  createGoogleOnlyAuthenticationHandler,
  isGoogleOnlyProviderSelection,
  parseIapLoginRequest,
  readFirebaseWebConfig,
} from "./iapLogin.ts";

const firebaseEnvironment = {
  NEXT_PUBLIC_FIREBASE_API_KEY: "public-test-key-1234567890",
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "tars-example.firebaseapp.com",
  NEXT_PUBLIC_FIREBASE_PROJECT_ID: "tars-example",
  NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "tars-example.firebasestorage.app",
  NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "123456789012",
  NEXT_PUBLIC_FIREBASE_APP_ID: "1:123456789012:web:abc123",
};

const firebaseConfig = {
  apiKey: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_APP_ID,
};

const validLoginUrl =
  "https://tars.ellaexecutivesearch.com/iap-login?mode=login&apiKey=public-test-key-1234567890&redirect_uri=https%3A%2F%2Fiap.googleapis.com%2Fv1beta1%2Fgcip%2Fresources%2FA1B2C3D4E5F60718%3AhandleRedirect&state=opaque-state";

const expectedPublicFirebaseValues = {
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "transcriptor-490222.firebaseapp.com",
  NEXT_PUBLIC_FIREBASE_PROJECT_ID: "transcriptor-490222",
  NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "transcriptor-490222.firebasestorage.app",
  NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "33726443105",
  NEXT_PUBLIC_FIREBASE_APP_ID: "1:33726443105:web:3089b7d56549e143130420",
};

test("Firebase web configuration fails closed when incomplete or placeholder-valued", () => {
  assert.equal(readFirebaseWebConfig({}), null);
  assert.equal(
    readFirebaseWebConfig({
      ...firebaseEnvironment,
      NEXT_PUBLIC_FIREBASE_API_KEY: "REPLACE_WITH_FIREBASE_WEB_API_KEY",
    }),
    null,
  );
  assert.equal(
    readFirebaseWebConfig({
      ...firebaseEnvironment,
      NEXT_PUBLIC_FIREBASE_APP_ID: "not-a-firebase-app-id",
    }),
    null,
  );
  assert.deepEqual(readFirebaseWebConfig(firebaseEnvironment), {
    apiKey: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_API_KEY,
    authDomain: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    projectId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    storageBucket: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
    appId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_APP_ID,
  });
});

test("IAP login request requires exact mode, project key, redirect URI, and state", () => {
  const parsed = parseIapLoginRequest(validLoginUrl);
  assert.deepEqual(parsed, {
    mode: "login",
    apiKey: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_API_KEY,
    redirectUri:
      "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    state: "opaque-state",
    tenantId: null,
  });

  for (const malformed of [
    "https://tars.ellaexecutivesearch.com/iap-login",
    validLoginUrl.replace("mode=login", "mode=unknown"),
    validLoginUrl.replace("&state=opaque-state", ""),
    validLoginUrl.replace("redirect_uri=https%3A%2F%2Fiap.googleapis.com%2Fv1beta1%2Fgcip%2Fresources%2FA1B2C3D4E5F60718%3AhandleRedirect", "redirect_uri=javascript%3Aalert(1)"),
    `${validLoginUrl}&mode=login`,
    `${validLoginUrl}&state=duplicate-state`,
    `${validLoginUrl}&apiKey=conflicting-test-key`,
    `${validLoginUrl}&redirect_uri=${encodeURIComponent("https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect")}`,
    `${validLoginUrl}&tid=tenant-a&tid=tenant-b`,
  ]) {
    assert.equal(parseIapLoginRequest(malformed), null, malformed);
  }
});

test("IAP rejects a single attacker-controlled redirect host", () => {
  const evilRedirect = new URL(validLoginUrl);
  evilRedirect.searchParams.set(
    "redirect_uri",
    "https://evil.example/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
  );
  assert.equal(parseIapLoginRequest(evilRedirect), null);
});

test("IAP rejects raw terminal query and fragment delimiters", () => {
  const validCallback =
    "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect";
  for (const delimiter of ["?", "#"]) {
    const request = new URL(validLoginUrl);
    request.searchParams.set("redirect_uri", `${validCallback}${delimiter}`);
    assert.equal(parseIapLoginRequest(request), null, delimiter);
  }
});

test("IAP rejects conflicting valid redirect_uri values in either parameter order", () => {
  const approvedCallback =
    "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect";
  const alternateCallback =
    "https://iap.googleapis.com/v1beta1/gcip/resources/B2C3D4E5F60718:handleRedirect";

  const alternateAlone = new URL(validLoginUrl);
  alternateAlone.searchParams.set("redirect_uri", alternateCallback);
  assert.notEqual(parseIapLoginRequest(alternateAlone), null);

  const approvedFirst = new URL(validLoginUrl);
  approvedFirst.searchParams.append("redirect_uri", alternateCallback);
  assert.equal(parseIapLoginRequest(approvedFirst), null);

  const alternateFirst = new URL(validLoginUrl);
  alternateFirst.searchParams.set("redirect_uri", alternateCallback);
  alternateFirst.searchParams.append("redirect_uri", approvedCallback);
  assert.equal(parseIapLoginRequest(alternateFirst), null);
});

test("IAP accepts only the exact Google callback origin and resource route", () => {
  for (const redirect of [
    "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    "https://iap.googleapis.com/v1beta1/gcip/resources/opaque.segment~v2:handleRedirect",
  ]) {
    const sameOrigin = new URL(validLoginUrl);
    sameOrigin.searchParams.set("redirect_uri", redirect);
    assert.notEqual(parseIapLoginRequest(sameOrigin), null, redirect);
  }

  const longResource = "a".repeat(3900);
  const acceptedLongRedirect = new URL(validLoginUrl);
  acceptedLongRedirect.searchParams.set(
    "redirect_uri",
    `https://iap.googleapis.com/v1beta1/gcip/resources/${longResource}:handleRedirect`,
  );
  assert.notEqual(parseIapLoginRequest(acceptedLongRedirect), null, "within total redirect bound");

  const overlongResource = "a".repeat(4096);
  for (const redirect of [
    "https://api.tars.ellaexecutivesearch.com/api/auth/bootstrap",
    "http://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    "http://api.tars.ellaexecutivesearch.com/api/auth/bootstrap",
    "http://localhost:8000/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    "https://iap.googleapis.com/v1beta1/gcip/resources/:handleRedirect",
    `https://iap.googleapis.com/v1beta1/gcip/resources/${overlongResource}:handleRedirect`,
    "https://iap.googleapis.com/v1beta1/gcip/resources/./A1B2C3D4E5F60718:handleRedirect",
    "https://iap.googleapis.com/v1beta1/gcip/resources/%2e%2E/A1B2C3D4E5F60718:handleRedirect",
    "https://iap.googleapis.com\\v1beta1\\gcip\\resources\\A1B2C3D4E5F60718:handleRedirect",
    "https://@iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    "https://iap%2Egoogleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    "https://IAP.GOOGLEAPIS.COM/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    "https://iap.googleapis.com:443/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect",
    "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718%2Fextra:handleRedirect",
    "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect?return=%2F",
    "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718:handleRedirect#fragment",
    "https://iap.googleapis.com/v1beta1/gcip/resources/A1B2C3D4E5F60718",
  ]) {
    const nonProduction = new URL(validLoginUrl);
    nonProduction.searchParams.set("redirect_uri", redirect);
    assert.equal(parseIapLoginRequest(nonProduction), null, redirect);
  }
});

test("sign-out may omit redirect data, but cannot provide only one redirect field", () => {
  const base = "https://tars.ellaexecutivesearch.com/iap-login?mode=signout&apiKey=public-test-key-1234567890";
  assert.deepEqual(parseIapLoginRequest(base), {
    mode: "signout",
    apiKey: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_API_KEY,
    redirectUri: null,
    state: null,
    tenantId: null,
  });
  assert.equal(parseIapLoginRequest(`${base}&state=only-state`), null);
  assert.equal(parseIapLoginRequest(`${base}&redirect_uri=https%3A%2F%2Fexample.com`), null);
});

test("the handler accepts exactly one Google provider and rejects every alternate or ambiguous hint", () => {
  assert.equal(isGoogleOnlyProviderSelection([GOOGLE_PROVIDER_ID]), true);
  assert.equal(isGoogleOnlyProviderSelection([]), false);
  assert.equal(isGoogleOnlyProviderSelection([GOOGLE_PROVIDER_ID, "password"]), false);
  assert.equal(isGoogleOnlyProviderSelection(["password"]), false);
  assert.doesNotThrow(() => assertGoogleOnlyProviderSelection(undefined));
  assert.doesNotThrow(() =>
    assertGoogleOnlyProviderSelection({ tenantId: "tenant-a", providerIds: [GOOGLE_PROVIDER_ID] }),
  );
  assert.throws(() =>
    assertGoogleOnlyProviderSelection({ tenantId: "tenant-a", providerIds: ["password"] }),
  );
  assert.throws(() =>
    assertGoogleOnlyProviderSelection({ tenantId: "tenant-a", providerIds: [GOOGLE_PROVIDER_ID, "oidc.example"] }),
  );
  assert.doesNotThrow(() =>
    assertGoogleOnlyProviderSelection({ tenantId: "tenant-a", providerIds: [] }),
  );
});

test("startSignIn treats absent or empty provider hints as non-authoritative", async () => {
  const credential = {} as never;
  const controller = createGoogleOnlyAuthenticationHandler(
    firebaseConfig,
    {},
    async () => credential,
  );

  for (const providerIds of [undefined, []] as const) {
    const pending = controller.handler.startSignIn({} as never, {
      tenantId: "tenant-a",
      providerIds,
    });
    assert.equal(controller.requestSignIn(), true);
    assert.equal(await pending, credential);
  }

  for (const providerIds of [["password"], [GOOGLE_PROVIDER_ID, "password"], [""], null]) {
    assert.throws(() =>
      controller.handler.startSignIn({} as never, {
        tenantId: "tenant-a",
        providerIds: providerIds as never,
      }),
    );
  }
  controller.dispose();
});

function fakeUser(tokenResult: unknown, providerData: unknown[] = []): never {
  return {
    providerData,
    getIdTokenResult: async () => tokenResult,
  } as never;
}

test("processUser trusts only the active Google sign-in provider claim", async () => {
  const controller = createGoogleOnlyAuthenticationHandler(firebaseConfig);
  const googleActiveUser = fakeUser(
    { claims: { firebase: { sign_in_provider: GOOGLE_PROVIDER_ID } } },
    [{ providerId: "password" }],
  );
  assert.equal(await controller.handler.processUser(googleActiveUser), googleActiveUser);

  const negativeTokenResults = [
    { claims: { firebase: { sign_in_provider: "password" } } },
    { claims: { firebase: { sign_in_provider: "saml.example" } } },
    {},
    { claims: {} },
    { claims: { firebase: null } },
    { claims: { firebase: [] } },
    { claims: { firebase: { sign_in_provider: 7 } } },
    null,
  ];
  for (const tokenResult of negativeTokenResults) {
    await assert.rejects(controller.handler.processUser(fakeUser(tokenResult)));
  }

  // A Google-linked account can still arrive with a non-Google active
  // provider. Linked providers are not sufficient authorization evidence.
  const googleLinkedPasswordUser = fakeUser(
    { claims: { firebase: { sign_in_provider: "password" } } },
    [{ providerId: GOOGLE_PROVIDER_ID }],
  );
  await assert.rejects(controller.handler.processUser(googleLinkedPasswordUser));
  controller.dispose();
});

test("handler disposal is an irreversible tombstone for pending and late work", async () => {
  let resolvePopup: ((credential: never) => void) | null = null;
  const popup = new Promise<never>((resolve) => {
    resolvePopup = resolve;
  });
  const controller = createGoogleOnlyAuthenticationHandler(
    firebaseConfig,
    {},
    () => popup,
  );
  const pending = controller.handler.startSignIn({} as never, {
    tenantId: "tenant-a",
    providerIds: [],
  });
  assert.equal(controller.requestSignIn(), true);
  controller.dispose();
  controller.dispose();
  await assert.rejects(pending);

  resolvePopup?.({} as never);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(controller.requestSignIn(), false);
  assert.throws(() => controller.handler.getAuth("public-test-key-1234567890", null));
  assert.throws(() =>
    controller.handler.startSignIn({} as never, {
      tenantId: "tenant-a",
      providerIds: [],
    }),
  );
  await assert.rejects(controller.handler.processUser(fakeUser({
    claims: { firebase: { sign_in_provider: GOOGLE_PROVIDER_ID } },
  })));
});

test("dispose wins over a late active-provider token result", async () => {
  let resolveToken: ((result: unknown) => void) | null = null;
  const tokenResult = new Promise<unknown>((resolve) => {
    resolveToken = resolve;
  });
  const controller = createGoogleOnlyAuthenticationHandler(firebaseConfig);
  const processing = controller.handler.processUser({
    getIdTokenResult: () => tokenResult,
  } as never);
  controller.dispose();
  resolveToken?.({ claims: { firebase: { sign_in_provider: GOOGLE_PROVIDER_ID } } });
  await assert.rejects(processing);
});

test("handler runtime checks remain fail closed even when called directly", async () => {
  const controller = (await import("./iapLogin.ts")).createGoogleOnlyAuthenticationHandler({
    apiKey: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_API_KEY,
    authDomain: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    projectId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    storageBucket: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
    appId: firebaseEnvironment.NEXT_PUBLIC_FIREBASE_APP_ID,
  });
  assert.throws(() => controller.handler.getAuth("different-project-key", null));
  assert.throws(() => controller.handler.startSignIn({} as never, {
    tenantId: "tenant-a",
    providerIds: ["password"],
  }),
  );
  await assert.rejects(
    controller.handler.processUser({ providerData: [] } as never),
  );
  controller.dispose();
});

test("the source has one federated sign-in method and no password, anonymous, or generic provider escape hatch", () => {
  const source = readFileSync(new URL("./iapLogin.ts", import.meta.url), "utf8");
  assert.match(source, /new GoogleAuthProvider\(\)/);
  assert.match(source, /signInWithPopup\(auth, provider\)/);
  assert.match(source, /getIdTokenResult\(\)/);
  assert.match(source, /claims\.firebase/);
  assert.match(source, /sign_in_provider === GOOGLE_PROVIDER_ID/);
  assert.doesNotMatch(source, /providerData/);
  for (const forbidden of [
    "signInWithEmailAndPassword",
    "createUserWithEmailAndPassword",
    "signInAnonymously",
    "signInWithCredential",
    "OAuthProvider",
    "SAMLAuthProvider",
    "EmailAuthProvider",
  ]) {
    assert.doesNotMatch(source, new RegExp(forbidden));
  }
});

test("the route keeps browser-only SDK startup behind a browser guard and dynamic imports", () => {
  const source = readFileSync(new URL("../app/iap-login/page.tsx", import.meta.url), "utf8");
  assert.match(source, /typeof window === "undefined"/);
  assert.match(source, /await import\("@\/lib\/iapLogin"\)/);
  assert.match(source, /await import\("\.\.\/\.\.\/\.\.\/vendor\/gcip-iap\/2\.0\.1\/index\.mjs"\)/);
  assert.doesNotMatch(source, /^import .*gcip-iap/m);
  assert.match(source, /await import\("@\/lib\/iapLogin"\);\s*if \(disposed\) return;/);
  assert.match(source, /await import\("\.\.\/\.\.\/\.\.\/vendor\/gcip-iap\/2\.0\.1\/index\.mjs"\)[\s\S]*if \(disposed\) \{\s*controller\.dispose\(\)/);
  assert.match(source, /await authentication\.start\(\);\s*if \(disposed\) \{\s*controller\.dispose\(\)/);
});

test("the vendored gcip-iap artifact and provenance are exact and browser-only", () => {
  const artifact = readFileSync(
    new URL("../../vendor/gcip-iap/2.0.1/index.mjs", import.meta.url),
  );
  const license = readFileSync(
    new URL("../../vendor/gcip-iap/2.0.1/LICENSE", import.meta.url),
  );
  const provenance = JSON.parse(
    readFileSync(new URL("../../vendor/gcip-iap/2.0.1/PROVENANCE.json", import.meta.url), "utf8"),
  );
  assert.equal(
    createHash("sha256").update(artifact).digest("hex"),
    "619d6959518fb09feb9127fca70ace4714bd443b4515fafa3786b1203ce04048",
  );
  assert.equal(
    createHash("sha256").update(license).digest("hex"),
    "accc36c817ac5ede473d05732e14afc11cb6e55ef5919bc826b653f631165043",
  );
  assert.deepEqual(provenance, {
    name: "gcip-iap",
    version: "2.0.1",
    upstreamRepository: "https://github.com/GoogleCloudPlatform/iap-gcip-web-toolkit",
    npmTarballUrl: "https://registry.npmjs.org/gcip-iap/-/gcip-iap-2.0.1.tgz",
    npmTarballIntegrity: "sha512-/bTN8SNl0xm/On9olQrttVIEKMsLPcclHopZ2K0R9Z0pC3f10rsH4BAsePkdTz9f8sdXhaazaT898i3xznznLg==",
    sourceMember: "package/dist/index.esm.js",
    artifactSha256: "619d6959518fb09feb9127fca70ace4714bd443b4515fafa3786b1203ce04048",
    licenseMember: "package/LICENSE",
    licenseSha256: "accc36c817ac5ede473d05732e14afc11cb6e55ef5919bc826b653f631165043",
    artifact_modified: false,
    permittedBareImports: ["whatwg-fetch", "url-polyfill", "promise-polyfill"],
  });
  const source = artifact.toString("utf8");
  assert.match(source, /import["']whatwg-fetch["']/);
  assert.match(source, /import["']url-polyfill["']/);
  assert.match(source, /import["']promise-polyfill["']/);
  for (const forbidden of ["require(", "node-forge", "vm2", "@types/node"]) {
    assert.doesNotMatch(source, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  const gitignoreLines = readFileSync(new URL("../../../.gitignore", import.meta.url), "utf8")
    .split(/\r?\n/);
  const jsonIgnoreIndex = gitignoreLines.indexOf("*.json");
  const provenanceAllowIndex = gitignoreLines.indexOf(
    "!frontend/vendor/gcip-iap/2.0.1/PROVENANCE.json",
  );
  assert.ok(jsonIgnoreIndex >= 0, "the broad JSON ignore rule must remain present");
  assert.equal(
    provenanceAllowIndex,
    jsonIgnoreIndex + 1,
    "vendor provenance must be unignored immediately after the broad JSON rule",
  );
});

function hasExactFirebaseApiKeySecret(source: string): boolean {
  let document;
  try {
    document = parseDocument(source, { uniqueKeys: true });
  } catch {
    return false;
  }
  if (document.errors.length > 0 || document.warnings.length > 0 || !isMap(document.contents)) {
    return false;
  }

  let hasUnsafeNode = false;
  visit(document.contents, {
    Alias() {
      hasUnsafeNode = true;
    },
    Node(_key, node) {
      if (node.tag !== undefined) hasUnsafeNode = true;
    },
    Pair(_key, pair) {
      if (isScalar(pair.key) && pair.key.value === "<<") hasUnsafeNode = true;
    },
  });
  if (hasUnsafeNode) return false;

  const directMapPairs = (
    map: { items: Array<{ key: unknown; value: unknown }> },
    key: string,
  ) => map.items.filter((pair) => isScalar(pair.key) && pair.key.value === key);

  const root = document.contents;
  const runConfigPairs = directMapPairs(root, "runConfig");
  if (runConfigPairs.length !== 1 || !isMap(runConfigPairs[0]?.value)) return false;
  const runConfig = runConfigPairs[0].value;
  const minInstancesPairs = directMapPairs(runConfig, "minInstances");
  const maxInstancesPairs = directMapPairs(runConfig, "maxInstances");
  const minInstances = minInstancesPairs[0]?.value;
  const maxInstances = maxInstancesPairs[0]?.value;
  if (
    minInstancesPairs.length !== 1 ||
    maxInstancesPairs.length !== 1 ||
    !isScalar(minInstances) ||
    typeof minInstances.value !== "number" ||
    minInstances.value !== 0 ||
    !isScalar(maxInstances) ||
    typeof maxInstances.value !== "number" ||
    maxInstances.value !== 1
  ) {
    return false;
  }

  const envPairs = directMapPairs(root, "env");
  if (envPairs.length !== 1 || !isSeq(envPairs[0]?.value)) return false;
  const env = envPairs[0].value;
  if (!isSeq(env)) return false;
  const variables = env.items.map((entry) => {
    if (!isMap(entry)) return null;
    const variablePairs = directMapPairs(entry, "variable");
    if (variablePairs.length !== 1) return null;
    const variable = variablePairs[0]?.value;
    return isScalar(variable) && typeof variable.value === "string" ? variable.value : null;
  });
  if (variables.some((variable) => variable === null)) return false;
  const matchingIndexes = variables.reduce<number[]>((indexes, variable, index) => {
    if (variable === "NEXT_PUBLIC_FIREBASE_API_KEY") indexes.push(index);
    return indexes;
  }, []);
  if (matchingIndexes.length !== 1) return false;
  const matchingIndex = matchingIndexes[0];
  if (matchingIndex === undefined) return false;

  for (const [variableName, expectedValue] of Object.entries(expectedPublicFirebaseValues)) {
    const publicIndexes = variables.reduce<number[]>((indexes, variable, index) => {
      if (variable === variableName) indexes.push(index);
      return indexes;
    }, []);
    if (publicIndexes.length !== 1) return false;
    const publicEntry = env.items[publicIndexes[0]];
    if (!isMap(publicEntry)) return false;
    const valuePairs = directMapPairs(publicEntry, "value");
    const secretPairs = directMapPairs(publicEntry, "secret");
    const value = valuePairs[0]?.value;
    if (
      valuePairs.length !== 1 ||
      secretPairs.length !== 0 ||
      !isScalar(value) ||
      value.value !== expectedValue
    ) {
      return false;
    }
  }

  const entry = env.items[matchingIndex];
  if (!isMap(entry)) return false;
  const secretPairs = directMapPairs(entry, "secret");
  const secret = secretPairs[0]?.value;
  const valuePairs = directMapPairs(entry, "value");
  return (
    secretPairs.length === 1 &&
    isScalar(secret) &&
    secret.value === "tars-firebase-web-api-key@2" &&
    valuePairs.length === 0
  );
}

test("App Hosting commits exact public Firebase identifiers and keeps the API key secret-only", () => {
  const source = readFileSync(new URL("../../apphosting.yaml", import.meta.url), "utf8");
  assert.doesNotMatch(source, /REPLACE_WITH_FIREBASE_/);
  assert.equal(hasExactFirebaseApiKeySecret(source), true);
  for (const staleOrMalformed of [
    "tars-firebase-web-api-key@1",
    "tars-firebase-web-api-key@20",
    "tars-firebase-web-api-key@2-bad",
  ]) {
    assert.equal(
      hasExactFirebaseApiKeySecret(
        source.replace("tars-firebase-web-api-key@2", staleOrMalformed),
      ),
      false,
      staleOrMalformed,
    );
  }
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source}\n  - variable: NEXT_PUBLIC_FIREBASE_API_KEY\n    secret: tars-firebase-web-api-key@3`,
    ),
    false,
    "duplicate competing secret entry",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source.replace("tars-firebase-web-api-key@2", "tars-firebase-web-api-key@3")}\n# - variable: NEXT_PUBLIC_FIREBASE_API_KEY\n#   secret: tars-firebase-web-api-key@2`,
    ),
    false,
    "comment-spoofed @2 must not satisfy a live @3 entry",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      source.replace(
        "    secret: tars-firebase-web-api-key@2",
        "    secret: tars-firebase-web-api-key@2\n    value: accidentally-inline-secret",
      ),
    ),
    false,
    "API key entry must not also contain a value field",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      source.replace(
        "    secret: tars-firebase-web-api-key@2",
        '    "variable": NEXT_PUBLIC_FIREBASE_API_KEY\n    secret: tars-firebase-web-api-key@2',
      ),
    ),
    false,
    "quoted duplicate variable key",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source}\n  - { variable: NEXT_PUBLIC_FIREBASE_API_KEY, secret: tars-firebase-web-api-key@3 }`,
    ),
    false,
    "flow-style duplicate entry",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source}\n  - { variable: NEXT_PUBLIC_FIREBASE_API_KEY, "variable": NEXT_PUBLIC_FIREBASE_API_KEY, secret: tars-firebase-web-api-key@3 }`,
    ),
    false,
    "flow-style duplicate variable key",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      source.replace(
        "    secret: tars-firebase-web-api-key@2",
        "    secret:\n      value: tars-firebase-web-api-key@2",
      ),
    ),
    false,
    "nested secret must not satisfy the direct secret contract",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      source.replace(
        "    secret: tars-firebase-web-api-key@2",
        '    secret: tars-firebase-web-api-key@2\n    "value": accidentally-inline-secret',
      ),
    ),
    false,
    "quoted value key must be rejected",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      source.replace(
        "    secret: tars-firebase-web-api-key@2",
        "    secret: tars-firebase-web-api-key@2\n    value:",
      ),
    ),
    false,
    "empty value key must be rejected",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source.replace(
        "env:\n",
        "competingSecret: &competing tars-firebase-web-api-key@3\nenv:\n",
      )}\n  - variable: NEXT_PUBLIC_FIREBASE_API_KEY\n    secret: *competing`,
    ),
    false,
    "alias-competing @3 must be rejected",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source}\n  - <<: { variable: NEXT_PUBLIC_FIREBASE_API_KEY, secret: tars-firebase-web-api-key@3 }`,
    ),
    false,
    "merge-competing entry must be rejected",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source.replace(
        "env:\n",
        "sharedFields: &sharedFields\n  value: accidentally-inline-secret\nenv:\n",
      )}\n  - variable: NEXT_PUBLIC_FIREBASE_API_KEY\n    secret: tars-firebase-web-api-key@2\n    <<: *sharedFields`,
    ),
    false,
    "merged/inherited value field must be rejected",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      source.replace(
        "  - variable: NEXT_PUBLIC_FIREBASE_API_KEY",
        "  - variable: !custom NEXT_PUBLIC_FIREBASE_API_KEY",
      ),
    ),
    false,
    "custom-tagged variable must be rejected",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      source.replace(
        "    secret: tars-firebase-web-api-key@2",
        "    secret: !custom tars-firebase-web-api-key@2",
      ),
    ),
    false,
    "custom-tagged secret must be rejected",
  );
  const warningOnly = `%FOO\n---\n${source}`;
  const warningDocument = parseDocument(warningOnly, { uniqueKeys: true });
  assert.equal(warningDocument.errors.length, 0);
  assert.ok(warningDocument.warnings.length > 0, "unknown directive must be warning-only");
  assert.equal(hasExactFirebaseApiKeySecret(warningOnly), false, "warnings must fail closed");

  const aliasOnly = source.replace(
    "runConfig:\n",
    "extraAnchor: &extra keep\nextraAlias: *extra\nrunConfig:\n",
  );
  const aliasDocument = parseDocument(aliasOnly, { uniqueKeys: true });
  assert.equal(aliasDocument.errors.length, 0);
  assert.equal(aliasDocument.warnings.length, 0);
  assert.equal(hasExactFirebaseApiKeySecret(aliasOnly), false, "aliases must fail closed");

  const inlineMerge = source.replace(
    "    secret: tars-firebase-web-api-key@2",
    "    secret: tars-firebase-web-api-key@2\n    <<: { value: accidentally-inline-secret }",
  );
  const mergeDocument = parseDocument(inlineMerge, { uniqueKeys: true });
  assert.equal(mergeDocument.errors.length, 0);
  assert.equal(mergeDocument.warnings.length, 0);
  assert.equal(hasExactFirebaseApiKeySecret(inlineMerge), false, "merge keys must fail closed");

  const explicitTag = source.replace(
    "    secret: tars-firebase-web-api-key@2",
    "    secret: !!str tars-firebase-web-api-key@2",
  );
  const explicitTagDocument = parseDocument(explicitTag, { uniqueKeys: true });
  assert.equal(explicitTagDocument.errors.length, 0);
  assert.equal(explicitTagDocument.warnings.length, 0);
  assert.equal(hasExactFirebaseApiKeySecret(explicitTag), false, "explicit tags must fail closed");

  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source.replace(
        "value: transcriptor-490222.firebaseapp.com",
        "value: attacker.example",
      )}\n# variable: NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN\n#   value: transcriptor-490222.firebaseapp.com`,
    ),
    false,
    "expected public value in a comment must not satisfy a wrong live value",
  );
  assert.equal(
    hasExactFirebaseApiKeySecret(
      `${source.replace("  minInstances: 0", "  minInstances: 1")}\n# runConfig:\n#   minInstances: 0\n#   maxInstances: 1`,
    ),
    false,
    "expected runConfig in a comment must not satisfy a wrong live value",
  );
  assert.doesNotMatch(source, /variable: NEXT_PUBLIC_FIREBASE_API_KEY\s+value:/);
  assert.doesNotMatch(source, /AIza[0-9A-Za-z_-]{20,}/);
});

test("frontend auth dependencies use normal peer resolution without a suppression file", () => {
  const packageJson = JSON.parse(
    readFileSync(new URL("../../package.json", import.meta.url), "utf8"),
  ) as {
    dependencies?: Record<string, string>;
    overrides?: Record<string, string>;
    scripts?: Record<string, string>;
    devDependencies?: Record<string, string>;
  };
  assert.equal(packageJson.dependencies?.firebase, "11.10.0");
  assert.equal(packageJson.dependencies?.["promise-polyfill"], "8.3.0");
  assert.equal(packageJson.dependencies?.["url-polyfill"], "1.1.14");
  assert.equal(packageJson.dependencies?.["whatwg-fetch"], "3.6.20");
  assert.equal(packageJson.dependencies?.["gcip-iap"], undefined);
  assert.equal(packageJson.devDependencies?.yaml, "2.9.0");
  assert.equal(existsSync(new URL("../../.npmrc", import.meta.url)), false);
  assert.doesNotMatch(JSON.stringify(packageJson), /legacy-peer-deps/);
  assert.equal(packageJson.overrides?.nanoid, "3.3.18");
  assert.equal(packageJson.scripts?.["verify:vendor"], "node scripts/verify-gcip-iap-vendor.mjs");
  assert.equal(packageJson.scripts?.pretest, "npm run verify:vendor");
  assert.equal(packageJson.scripts?.prebuild, "npm run verify:vendor");
});

test("firebase.json uses the exact source App Hosting deploy template", () => {
  const config = JSON.parse(readFileSync(new URL("../../../firebase.json", import.meta.url), "utf8")) as {
    apphosting?: Array<Record<string, unknown>>;
  };
  assert.equal(config.apphosting?.length, 1);
  assert.deepEqual(config.apphosting?.[0], {
    backendId: "tars-frontend",
    rootDir: "frontend",
    alwaysDeployFromSource: true,
    ignore: [
      "node_modules",
      ".git",
      "firebase-debug.log",
      "firebase-debug.*.log",
      "functions",
    ],
  });
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { formatSourceHealth } from "./companionHealth.ts";

test("formats reconnecting state for microphone with exact label, icon, and amber color", () => {
  const result = formatSourceHealth("mic", "reconnecting");
  assert.deepEqual(result, {
    label: "Microfone: Reconectando…",
    badgeBg: "rgba(255, 149, 0, 0.12)",
    badgeColor: "#c97000",
    icon: "↻",
  });
});

test("formats reconnecting state for system audio with exact label, icon, and amber color", () => {
  const result = formatSourceHealth("system", "reconnecting");
  assert.deepEqual(result, {
    label: "Áudio do Sistema: Reconectando…",
    badgeBg: "rgba(255, 149, 0, 0.12)",
    badgeColor: "#c97000",
    icon: "↻",
  });
});

test("preserves healthy and unknown states byte-for-byte", () => {
  assert.deepEqual(formatSourceHealth("mic", "healthy"), {
    label: "Microfone: Ativo",
    badgeBg: "rgba(52, 199, 89, 0.12)",
    badgeColor: "#248a3d",
    icon: "●",
  });
  assert.deepEqual(formatSourceHealth("system", "unknown"), {
    label: "Áudio do Sistema: Aguardando companion",
    badgeBg: "rgba(142, 142, 147, 0.12)",
    badgeColor: "#636366",
    icon: "○",
  });
});

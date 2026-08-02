import assert from "node:assert/strict";
import { test } from "node:test";

import { tokens } from "./tokens.ts";

test("every colour role is a valid hex or rgba value", () => {
  const flat = Object.values(tokens.color).flatMap((group) =>
    typeof group === "string" ? [group] : Object.values(group),
  );
  assert.ok(flat.length > 0);
  for (const value of flat) {
    assert.match(
      value,
      /^(#[0-9a-f]{6}|rgba\(\d+, ?\d+, ?\d+, ?[\d.]+\))$/i,
      `bad colour value: ${value}`,
    );
  }
});

test("spacing scale is strictly ascending", () => {
  const steps = Object.values(tokens.space);
  for (let i = 1; i < steps.length; i += 1) {
    assert.ok(
      steps[i] > steps[i - 1],
      `spacing not ascending at index ${i}: ${steps[i - 1]} -> ${steps[i]}`,
    );
  }
});

test("type scale is strictly ascending", () => {
  const steps = Object.values(tokens.text);
  for (let i = 1; i < steps.length; i += 1) {
    assert.ok(steps[i] > steps[i - 1], `type scale not ascending at index ${i}`);
  }
});

test("required colour roles exist", () => {
  for (const role of ["primary", "secondary", "tertiary"] as const) {
    assert.equal(typeof tokens.color.text[role], "string");
  }
  for (const role of ["base", "raised", "sunken"] as const) {
    assert.equal(typeof tokens.color.surface[role], "string");
  }
});

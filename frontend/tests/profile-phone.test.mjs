import { test } from "node:test";
import assert from "node:assert/strict";
import { canonicalProfilePhone, initialProfilePhone } from "../src/lib/profile-phone.ts";

test("editing existing Malaysian profiles converts local and old country-code formats", () => {
  for (const value of ["0123456789", "60123456789", "+60123456789", " +60 12-345 6789 "]) {
    assert.equal(initialProfilePhone(value), "+60123456789");
  }
  assert.equal(initialProfilePhone(null), undefined);
  assert.equal(initialProfilePhone(""), undefined);
  assert.equal(initialProfilePhone("not a phone"), undefined);
});

test("valid numbers from different countries are saved in international format", () => {
  for (const [value, expected] of [["+60 12-345 6789", "+60123456789"], ["+65 8123 4567", "+6581234567"],
    ["+44 20 8366 1177", "+442083661177"], ["+1 213 373 4253", "+12133734253"]]) {
    assert.equal(canonicalProfilePhone(value), expected);
  }
});

test("a country prefix alone or an invalid or ambiguous number cannot be saved", () => {
  for (const value of [undefined, "", "+60", "+60123", "0123456789", "60123456789", "+99912345678", "+60123456789 ext 12"]) {
    assert.equal(canonicalProfilePhone(value), null);
  }
});

import { isValidPhoneNumber, parsePhoneNumber } from "react-phone-number-input/max";

// Existing local-format profiles default to Malaysia when opened for editing.
// The API accepts only international numbers for all new saves.
export function initialProfilePhone(value: string | null) {
  if (!value?.trim()) return undefined;
  return parsePhoneNumber(value.trim(), "MY")?.number;
}

export function canonicalProfilePhone(value: string | undefined) {
  if (!value || !/^\+[0-9 ()-]+$/.test(value) || !isValidPhoneNumber(value)) return null;
  return parsePhoneNumber(value)?.number ?? null;
}

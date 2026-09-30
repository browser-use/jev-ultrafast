/** Small JSON helpers with Python-compatible semantics. */

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => item === undefined ? null : canonical(item));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      const item = (value as Record<string, unknown>)[key];
      if (item !== undefined) out[key] = canonical(item);
    }
    return out;
  }
  return value;
}

/** Compact JSON with sorted keys. Undefined object keys are omitted; undefined array items become null. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonical(value)) ?? "null";
}

/** Deep JSON equality, independent of key order. */
export function jsonEqual(a: unknown, b: unknown): boolean {
  return canonicalJson(a) === canonicalJson(b);
}

/** Python truthiness: null, undefined, false, 0, NaN, "", [] and {} are false. */
export function isTruthy(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (value !== null && typeof value === "object") return Object.keys(value).length > 0;
  return Boolean(value);
}

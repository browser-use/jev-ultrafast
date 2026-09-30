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

function pythonString(value: string): string {
  let out = '"';
  for (let i = 0; i < value.length; i++) {
    const c = value[i];
    const code = value.charCodeAt(i);
    if (c === '"') out += '\\"';
    else if (c === "\\") out += "\\\\";
    else if (c === "\n") out += "\\n";
    else if (c === "\r") out += "\\r";
    else if (c === "\t") out += "\\t";
    else if (c === "\b") out += "\\b";
    else if (c === "\f") out += "\\f";
    else if (code < 0x20 || code > 0x7e) out += "\\u" + code.toString(16).padStart(4, "0");
    else out += c;
  }
  return out + '"';
}

/**
 * Python `json.dumps(value)` for JSON values: `", "` and `": "` separators, insertion key order, and
 * `ensure_ascii` escaping (astral characters as UTF-16 surrogate pairs). Undefined object keys are omitted.
 */
export function pythonJsonDumps(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return pythonString(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (Number.isNaN(value)) return "NaN";
    if (!Number.isFinite(value)) return value > 0 ? "Infinity" : "-Infinity";
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return "[" + value.map(pythonJsonDumps).join(", ") + "]";
  if (typeof value === "object") {
    const items = Object.entries(value).filter(([, item]) => item !== undefined);
    return "{" + items.map(([key, item]) => pythonString(key) + ": " + pythonJsonDumps(item)).join(", ") + "}";
  }
  throw new TypeError(`Object of type ${typeof value} is not JSON serializable`);
}

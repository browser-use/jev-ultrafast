/**
 * Render the actual Google Flights screencast at 1×, including every loading wait.
 *
 * Each video frame is an SVG rasterised by resvg; ffmpeg encodes the PNG sequence.
 * Timing comes only from the recording's original browser timestamps.
 */

import { parseArgs } from "@std/cli/parse-args";
import { encodeBase64 } from "@std/encoding/base64";
import { basename, fromFileUrl, join, resolve } from "@std/path";

export const ROOT: string = fromFileUrl(new URL("../", import.meta.url));

// ---------------------------------------------------------------- fonts

/** A loaded font face: the family name resvg matches plus its ascent as a fraction of the em. */
export interface Face {
  path: string;
  family: string;
  weight: 400 | 700;
  ascent: number;
}

const SANS = [
  "/System/Library/Fonts/Supplemental/Arial.ttf",
  "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
  "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
];
const SANS_BOLD = [
  "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
  "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
];
const MONO = [
  "/System/Library/Fonts/Menlo.ttc",
  "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
  "/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf",
];

function firstExisting(candidates: string[], flag: string): string {
  for (const path of candidates) {
    try {
      if (Deno.statSync(path).isFile) return path;
    } catch {
      // Try the next platform default.
    }
  }
  throw new Error(`No font found for --${flag}; tried ${candidates.join(", ")}`);
}

/** Read the family name and hhea ascent from a TrueType/OpenType file (first face of a collection). */
export function fontInfo(bytes: Uint8Array): { family: string; ascent: number | null } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (at: number) => String.fromCharCode(...bytes.subarray(at, at + 4));
  const base = tag(0) === "ttcf" ? view.getUint32(12) : 0;
  const tables = new Map<string, number>();
  for (let i = 0; i < view.getUint16(base + 4); i++) {
    const record = base + 12 + i * 16;
    tables.set(tag(record), view.getUint32(record + 8));
  }
  let ascent: number | null = null;
  const head = tables.get("head");
  const hhea = tables.get("hhea");
  if (head !== undefined && hhea !== undefined) ascent = view.getInt16(hhea + 4) / view.getUint16(head + 18);
  const name = tables.get("name");
  if (name === undefined) throw new Error("Font has no name table");
  const count = view.getUint16(name + 2);
  const strings = name + view.getUint16(name + 4);
  // fontdb prefers the typographic family (16) over the legacy family (1); Windows names over Mac names.
  let best: { rank: number; value: string } | null = null;
  for (let i = 0; i < count; i++) {
    const record = name + 6 + i * 12;
    const platform = view.getUint16(record);
    const nameId = view.getUint16(record + 6);
    if (nameId !== 1 && nameId !== 16) continue;
    if (platform !== 0 && platform !== 1 && platform !== 3) continue;
    const length = view.getUint16(record + 8);
    const raw = bytes.subarray(strings + view.getUint16(record + 10), strings + view.getUint16(record + 10) + length);
    let value = "";
    if (platform === 1) value = String.fromCharCode(...raw);
    else for (let j = 0; j + 1 < raw.length; j += 2) value += String.fromCharCode((raw[j] << 8) | raw[j + 1]);
    const rank = (nameId === 16 ? 0 : 10) + (platform === 3 ? 0 : platform === 0 ? 1 : 2);
    if (value && (best === null || rank < best.rank)) best = { rank, value };
  }
  if (best === null) throw new Error("Font has no family name");
  return { family: best.value, ascent };
}

function loadFace(path: string, weight: 400 | 700, fallbackAscent: number): Face {
  const info = fontInfo(Deno.readFileSync(path));
  return { path, family: info.family, weight, ascent: info.ascent ?? fallbackAscent };
}

export interface Fonts {
  sans: Face;
  bold: Face;
  mono: Face;
}

/** Resolve --sans, --sans-bold and --mono, defaulting to macOS Arial/Menlo, then Liberation Sans / DejaVu Sans Mono. */
export function loadFonts(flags: { sans?: string; "sans-bold"?: string; mono?: string }): Fonts {
  return {
    sans: loadFace(flags.sans ?? firstExisting(SANS, "sans"), 400, 0.905),
    bold: loadFace(flags["sans-bold"] ?? firstExisting(SANS_BOLD, "sans-bold"), 700, 0.905),
    mono: loadFace(flags.mono ?? firstExisting(MONO, "mono"), 400, 0.928),
  };
}

// ---------------------------------------------------------------- SVG helpers
// Coordinates follow Pillow's conventions so the layout matches the original renderer:
// boxes are inclusive (x0, y0, x1, y1), and text is anchored at the top of the ascender.

export function escapeXml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function text(x: number, y: number, value: string, face: Face, size: number, fill: string): string {
  const baseline = y + pyRound(size * face.ascent);
  return `<text x="${x}" y="${baseline}" font-family="${escapeXml(face.family)}" font-weight="${face.weight}" ` +
    `font-size="${size}" fill="${fill}">${escapeXml(value)}</text>`;
}

export function box(x0: number, y0: number, x1: number, y1: number, fill: string, radius = 0): string {
  const r = radius ? ` rx="${radius}" ry="${radius}"` : "";
  return `<rect x="${x0}" y="${y0}" width="${x1 - x0 + 1}" height="${y1 - y0 + 1}"${r} fill="${fill}"/>`;
}

export function ellipse(x0: number, y0: number, x1: number, y1: number, fill: string): string {
  const rx = (x1 - x0 + 1) / 2;
  const ry = (y1 - y0 + 1) / 2;
  return `<ellipse cx="${x0 + rx}" cy="${y0 + ry}" rx="${rx}" ry="${ry}" fill="${fill}"/>`;
}

export function line(points: [number, number][], stroke: string, width: number): string {
  // Odd widths are centred on a pixel, as Pillow draws them.
  const offset = width % 2 ? 0.5 : 0;
  const coords = points.map(([x, y]) => `${x + offset},${y + offset}`).join(" ");
  return `<polyline points="${coords}" fill="none" stroke="${stroke}" stroke-width="${width}"/>`;
}

/** Natural width/height of a baseline or progressive JPEG, read from its SOF marker. */
export function jpegSize(bytes: Uint8Array): { width: number; height: number } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let at = 2;
  while (at + 9 < bytes.length) {
    if (bytes[at] !== 0xff) {
      at++;
      continue;
    }
    const marker = bytes[at + 1];
    if (marker === 0xff) {
      at++;
      continue;
    }
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      at += 2;
      continue;
    }
    const isSof = marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc;
    if (isSof) return { height: view.getUint16(at + 5), width: view.getUint16(at + 7) };
    at += 2 + view.getUint16(at + 2);
  }
  throw new Error("JPEG has no SOF marker");
}

export interface Jpeg {
  href: string;
  width: number;
  height: number;
}

export function readJpeg(path: string): Jpeg {
  const bytes = Deno.readFileSync(path);
  return { href: `data:image/jpeg;base64,${encodeBase64(bytes)}`, ...jpegSize(bytes) };
}

/** Place `image` at (x, y); with a crop box, only that region is shown, and pixels outside the image are black. */
export function image(img: Jpeg, x: number, y: number, crop?: [number, number, number, number]): string {
  const tag = `<image href="${img.href}" width="${img.width}" height="${img.height}" preserveAspectRatio="none"/>`;
  if (!crop) return `<g transform="translate(${x} ${y})">${tag}</g>`;
  const [cx0, cy0, cx1, cy1] = crop;
  const w = cx1 - cx0;
  const h = cy1 - cy0;
  return `<svg x="${x}" y="${y}" width="${w}" height="${h}" viewBox="${cx0} ${cy0} ${w} ${h}">` +
    `<rect x="${cx0}" y="${cy0}" width="${w}" height="${h}" fill="#000"/>${tag}</svg>`;
}

export function svgDocument(width: number, height: number, background: string, body: string[]): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" width="${width}" height="${height}" ` +
    `viewBox="0 0 ${width} ${height}"><rect width="${width}" height="${height}" fill="${background}"/>` +
    `${body.join("")}</svg>`;
}

// ---------------------------------------------------------------- rasterising and encoding

export type Rasterise = (svg: string) => Uint8Array;

/** SVG → PNG with resvg, restricted to the chosen font files so frames do not depend on system font lookup. */
export async function rasteriser(fonts: Fonts): Promise<Rasterise> {
  const { Resvg } = await import("@resvg/resvg-js");
  const fontFiles = [...new Set([fonts.sans.path, fonts.bold.path, fonts.mono.path])];
  const options = { font: { fontFiles, loadSystemFonts: false, defaultFontFamily: fonts.sans.family } };
  return (svg) => new Resvg(svg, options).render().asPng();
}

export async function ffmpeg(args: string[]): Promise<void> {
  const { code } = await new Deno.Command("ffmpeg", { args: ["-y", "-loglevel", "error", ...args] }).spawn().status;
  if (code !== 0) throw new Error(`ffmpeg exited with status ${code}`);
}

export function h264(frames: string, output: string): Promise<void> {
  return ffmpeg([
    "-framerate",
    "30",
    "-i",
    frames,
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-crf",
    "18",
    "-movflags",
    "+faststart",
    output,
  ]);
}

// ---------------------------------------------------------------- numbers formatted like Python

/** Python's `f"{x:.{digits}f}"`: the exact binary value, rounded half-to-even. */
export function pyFixed(x: number, digits: number): string {
  const negative = x < 0 || Object.is(x, -0);
  const [whole, fraction] = Math.abs(x).toFixed(100).split(".");
  const kept = BigInt(whole + fraction.slice(0, digits));
  const rest = fraction.slice(digits);
  const half = "5".padEnd(rest.length, "0");
  const rounded = rest > half || (rest === half && kept % 2n === 1n) ? kept + 1n : kept;
  let out = rounded.toString().padStart(digits + 1, "0");
  if (digits) out = `${out.slice(0, -digits)}.${out.slice(-digits)}`;
  return negative && /[1-9]/.test(out) ? `-${out}` : out;
}

/** Python's `round(x)`: exact halves go to the even integer. */
export function pyRound(x: number): number {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff === 0.5) return floor % 2 === 0 ? floor : floor + 1;
  return diff < 0.5 ? floor : floor + 1;
}

/** Python's `statistics.median`. */
export function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// ---------------------------------------------------------------- the Flights video

interface RecordedState {
  elapsed_ms: number;
  verification: { passed: boolean };
  recording_errors: unknown[];
  history: { action: string; executed_ms: number }[];
  decisions: { latency_ms: number; elapsed_ms: number }[];
  text_calls: { model: string }[];
}

const INK = "#172a20";
const MUTED = "#6a766c";
const GREEN = "#2a743f";
const STEPS: [string, string][] = [
  ["One way", "One way"],
  ["Zürich", "Zürich, Switzerland"],
  ["London", "London, United Kingdom"],
  ["20 September", "Done. Search"],
  ["Search flights", "Search"],
];

function frameSvg(state: RecordedState, t: number, shot: Jpeg, fonts: Fonts): string {
  const { sans, bold, mono } = fonts;
  const end = state.elapsed_ms;
  const sansOf = (done: boolean) => done ? bold : sans;
  const out: string[] = [];
  out.push(text(36, 26, "browser use", bold, 23, INK));
  out.push(text(186, 27, "×  TypeSafe", sans, 22, MUTED));
  out.push(box(1287, 24, 1499, 59, "#dfebd9", 17));
  out.push(text(1310, 32, "REAL WEB  ·  1× SPEED", bold, 14, GREEN));
  out.push(text(36, 80, `Zürich → London. In ${pyFixed(end / 1000, 1)} seconds.`, bold, 43, INK));
  out.push(text(38, 139, "One goal. Dynamic elements. LLM-generated text.", sans, 20, MUTED));
  out.push(box(35, 191, 1157, 943, "#202124", 14));
  ["#de8278", "#d6bd6e", "#8dbd8a"].forEach((c, j) => out.push(ellipse(54 + j * 19, 205, 63 + j * 19, 214, c)));
  out.push(text(145, 201, "google.com/travel/flights", mono, 13, "#d4d6d5"));
  // Omit Google account controls in every frame. No content from the task area is redrawn.
  out.push(image(shot, 36, 226, [0, 64, 1120, 780]));
  out.push(text(1192, 206, "JEV ULTRAFAST", bold, 16, GREEN));
  out.push(text(1189, 242, pyFixed(t / 1000, 2).padStart(5, "0"), mono, 52, INK));
  out.push(text(1193, 307, "SECONDS ELAPSED", bold, 13, MUTED));
  const history = state.history.filter((h) => h.executed_ms <= t);
  STEPS.forEach(([label, key], j) => {
    const done = history.some((h) => h.action === key || (key === "Done. Search" && h.action.startsWith(key)));
    const y = 370 + j * 54;
    out.push(ellipse(1194, y, 1218, y + 24, done ? GREEN : "#e0e4d9"));
    if (done) out.push(line([[1200, y + 12], [1204, y + 16], [1212, y + 8]], "white", 2));
    out.push(text(1236, y - 1, label, sansOf(done), 21, done ? INK : MUTED));
  });
  const search = state.history.find((h) => h.action === "Search");
  if (!search) throw new Error("Recording has no executed Search action");
  const waiting = t >= search.executed_ms && t < end;
  const final = t >= end;
  out.push(box(1189, 670, 1499, 789, final ? "#dfeeda" : "#e7e9df", 14));
  const title = final ? "Flights found" : waiting ? "Waiting for Google…" : "Choose. Act. Repeat.";
  out.push(text(1209, 691, title, bold, 22, final ? GREEN : INK));
  const subtitle = final
    ? "Route + date verified"
    : waiting
    ? "Loading stays in the video"
    : `${history.length} actions executed`;
  out.push(text(1209, 734, subtitle, sans, 16, MUTED));
  const latencies = state.decisions.filter((x) => x.elapsed_ms <= t).map((x) => x.latency_ms);
  const latency = latencies.length ? `${pyFixed(median(latencies), 0)} ms` : "—";
  out.push(text(1194, 835, latency, mono, 30, INK));
  out.push(text(1194, 878, "median decision latency", sans, 16, MUTED));
  out.push(line([[37, 960], [1498, 960]], "#d3d9cc", 2));
  out.push(line([[37, 960], [37 + (1498 - 37) * t / end, 960]], GREEN, 3));
  const textModel = state.text_calls[0].model.split("/").at(-1);
  out.push(
    text(37, 973, `Operation + index by Jev. Text by ${textModel}. Original timing; waits included.`, sans, 14, MUTED),
  );
  out.push(text(1194, 973, "github.com/syncretic-cc/jev-ultrafast-typescript", sans, 12, MUTED));
  return svgDocument(1536, 1000, "#f3f4ec", out);
}

async function main(): Promise<void> {
  const args = parseArgs(Deno.args, { string: ["sans", "sans-bold", "mono"] });
  if (args._.length !== 1) {
    console.error("usage: deno task render <recording-folder> [--sans F] [--sans-bold F] [--mono F]");
    Deno.exit(2);
  }
  const source = resolve(String(args._[0]));
  const state: RecordedState = JSON.parse(await Deno.readTextFile(join(source, "state.json")));
  if (!state.verification?.passed || state.recording_errors?.length) {
    throw new Error("Only a verified recording without screencast errors can be rendered");
  }
  const framePaths: [number, string][] = [[0, join(source, "frames/000000.jpg")]];
  const screencast: [number, string][] = [];
  for await (const entry of Deno.readDir(join(source, "screencast"))) {
    if (entry.isFile && entry.name.endsWith(".jpg")) {
      screencast.push([parseInt(basename(entry.name, ".jpg"), 10), join(source, "screencast", entry.name)]);
    }
  }
  screencast.sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : 1));
  framePaths.push(...screencast);
  const end = state.elapsed_ms;
  const folder = join(source, "video-frames");
  await Deno.mkdir(folder); // Fails if it already exists; never mix frames from two renders.
  const fonts = loadFonts(args);
  const render = await rasteriser(fonts);
  const cache = new Map<string, Jpeg>();
  let png: Uint8Array | null = null;
  const count = pyRound((end + 500) * 30 / 1000);
  for (let i = 0; i < count; i++) {
    const t = Math.min(end, pyRound(i * 1000 / 30));
    const found = framePaths.findLast(([ts]) => ts <= t);
    if (!found) throw new Error(`No recorded frame at ${t} ms`);
    let shot = cache.get(found[1]);
    if (!shot) cache.set(found[1], shot = readJpeg(found[1]));
    png = render(frameSvg(state, t, shot, fonts));
    await Deno.writeFile(join(folder, `${String(i).padStart(4, "0")}.png`), png);
  }
  if (png) await Deno.writeFile(join(ROOT, "docs/flights-result.png"), png);
  await h264(join(folder, "%04d.png"), join(ROOT, "docs/demo.mp4"));
  await ffmpeg([
    "-i",
    join(ROOT, "docs/demo.mp4"),
    "-vf",
    "fps=12,scale=1152:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
    "-loop",
    "0",
    join(ROOT, "docs/demo.gif"),
  ]);
  console.log("Rendered", framePaths.length, "source frames at original timing:", end, "ms, plus a 500ms end hold.");
}

if (import.meta.main) await main();

/** Render recorded fixture frames at their original timestamps. No acceleration. */

import { parseArgs } from "@std/cli/parse-args";
import { basename, join, resolve } from "@std/path";
import {
  ffmpeg,
  h264,
  image,
  type Jpeg,
  loadFonts,
  pyFixed,
  pyRound,
  rasteriser,
  readJpeg,
  ROOT,
  svgDocument,
  text,
} from "./render_demo.ts";

interface FixtureState {
  elapsed_ms: number;
  history: { elapsed_ms: number }[];
}

async function main(): Promise<void> {
  const args = parseArgs(Deno.args, {
    string: ["source", "sans", "sans-bold", "mono"],
    default: { source: join(ROOT, "artifacts/final") },
  });
  const source = resolve(args.source);
  const state: FixtureState = JSON.parse(await Deno.readTextFile(join(source, "travel-1.json")));
  const framePaths: [number, string][] = [];
  for await (const entry of Deno.readDir(join(source, "frames"))) {
    if (entry.isFile && entry.name.endsWith(".jpg")) {
      framePaths.push([parseInt(basename(entry.name, ".jpg"), 10), join(source, "frames", entry.name)]);
    }
  }
  framePaths.sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : 1));
  const endMs = state.elapsed_ms;
  const folder = join(ROOT, "artifacts/video-frames");
  await Deno.mkdir(folder, { recursive: true });
  const fonts = loadFonts(args);
  const { mono } = fonts;
  const render = await rasteriser(fonts);
  const cache = new Map<string, Jpeg>();
  // 750 ms lead-in and 1,250 ms endpoint hold; action time itself is unmodified.
  const count = pyRound((endMs + 2000) * 30 / 1000);
  for (let index = 0; index < count; index++) {
    const t = Math.min(endMs, Math.max(0, pyRound(index * 1000 / 30) - 750));
    const found = framePaths.findLast(([timestamp]) => timestamp <= t);
    if (!found) throw new Error(`No recorded frame at ${t} ms`);
    let frame = cache.get(found[1]);
    if (!frame) cache.set(found[1], frame = readJpeg(found[1]));
    const step = state.history.filter((h) => h.elapsed_ms <= t).length;
    const svg = svgDocument(1240, 960, "#f5f5ed", [
      image(frame, 60, 105),
      text(60, 34, "BROWSER USE × TYPESAFE", mono, 23, "#283c2c"),
      text(945, 38, `${pyFixed(t / 1000, 2)}s / 1×`, mono, 17, "#487645"),
      text(60, 909, `Jev Ultrafast     ${step}/5 browser actions     Live API calls`, mono, 17, "#64745c"),
    ]);
    await Deno.writeFile(join(folder, `${String(index).padStart(4, "0")}.png`), render(svg));
  }
  const frames = join(folder, "%04d.png");
  await ffmpeg([
    "-framerate",
    "30",
    "-i",
    frames,
    "-vf",
    "scale=930:720:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
    "-loop",
    "0",
    join(ROOT, "docs/fixture-demo.gif"),
  ]);
  await h264(frames, join(ROOT, "docs/fixture-demo.mp4"));
  console.log("Rendered 1× footage from", framePaths.length, "recorded observations; action time:", endMs, "ms");
}

if (import.meta.main) await main();

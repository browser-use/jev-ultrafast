/** Loopback-only inspector for the Jev browser agent. */

import { encodeBase64Url } from "@std/encoding/base64url";
import { join } from "@std/path";
import { Agent } from "./agent.ts";
import { acquireCdp, type CdpLease } from "./cdp.ts";
import { loadEnvironment } from "./env.ts";
import { JevError } from "./errors.ts";
import { MAX_STEPS } from "./questions.ts";

const STATIC: Record<string, [string, string]> = {
  "/": ["index.html", "text/html"],
  "/app.js": ["app.js", "text/javascript"],
  "/style.css": ["style.css", "text/css"],
  "/fixture.html": ["fixture.html", "text/html"],
};

const SCENARIOS = new Set(["travel", "research", "flights"]);

/** Run the inspector on 127.0.0.1 until SIGINT/SIGTERM. */
export async function main(): Promise<void> {
  loadEnvironment();
  const port = Number(Deno.env.get("TYPESAFE_DEMO_PORT") ?? "8766");
  const host = `127.0.0.1:${port}`;
  const origin = `http://${host}`;
  const token = encodeBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  let agent: Agent | null = null;
  let lease: CdpLease | null = null;
  let busy: Promise<void> | null = null;
  let stopping = false;

  const send = (status: number, content: string | Uint8Array, mime = "application/json"): Response =>
    new Response(content as BodyInit, {
      status,
      headers: { "Content-Type": mime, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
    });

  const responseState = () => {
    const state = agent ? agent.snapshot() : { page: null, status: "idle", history: [], decision: null };
    return { ...state, text_model: Deno.env.get("TEXT_MODEL") ?? "deepseek-chat", max_steps: MAX_STEPS };
  };

  const closeBrowser = async () => {
    const current = agent;
    agent = null;
    if (current) await current.close();
  };

  const command = async (name: string, body: Record<string, unknown>) => {
    if (name === "reset") {
      const scenario = body.scenario ?? "flights";
      if (typeof scenario !== "string" || !SCENARIOS.has(scenario)) throw new JevError("Unknown demo scenario");
      const goal = typeof body.goal === "string" ? body.goal.trim() : body.goal === undefined ? "" : null;
      if (!goal || [...goal].length > 2000) throw new JevError("Enter 1–2,000 characters");
      // Best effort: after a Chrome restart the old connection is dead and Browser.close() fails.
      await closeBrowser().catch(() => {});
      if (!lease || lease.cdp.closed) {
        await lease?.release();
        lease = null;
        lease = await acquireCdp();
      }
      const created: Agent = await Agent.create(
        scenario === "flights"
          ? "https://www.google.com/travel/flights?hl=en"
          : `${origin}/fixture.html?scenario=${scenario}`,
        goal,
        {
          cdp: lease.cdp,
          screenshots: true,
          recordDir: body.record ? join(Deno.cwd(), "artifacts", "frames") : null,
        },
      );
      agent = created;
      created.state.scenario = scenario;
    } else {
      if (agent === null) throw new JevError("Start a demo first");
      await agent.command(name, body);
    }
    return responseState();
  };

  const get = async (path: string): Promise<Response> => {
    if (path === "/api/state") {
      while (busy) await busy;
      return send(200, JSON.stringify(responseState()));
    }
    if (path === "/demo.mp4" && import.meta.url.startsWith("file:")) {
      try {
        return send(200, await Deno.readFile(new URL("../docs/demo.mp4", import.meta.url)), "video/mp4");
      } catch {
        // Missing or unreadable video: fall through to 404.
      }
    }
    const file = STATIC[path];
    if (!file) return send(404, "Not found", "text/plain");
    const [name, mime] = file;
    const content = await (await fetch(new URL(`./static/${name}`, import.meta.url))).text();
    return send(200, content.replaceAll("__TOKEN__", token), mime + "; charset=utf-8");
  };

  const post = async (request: Request, path: string): Promise<Response> => {
    const requestOrigin = request.headers.get("Origin");
    if (request.headers.get("X-Demo-Token") !== token || (requestOrigin !== null && requestOrigin !== origin)) {
      return send(403, JSON.stringify({ error: "Local demo requests only" }));
    }
    if (busy) return send(409, JSON.stringify({ error: "A browser step is already running" }));
    let done!: () => void;
    busy = new Promise((resolve) => done = resolve);
    try {
      const declared = Number(request.headers.get("Content-Length") ?? "0");
      if (!(declared > 0 && declared < 8192)) throw new JevError("Invalid request size");
      const bytes = new Uint8Array(await request.arrayBuffer());
      if (!(bytes.length > 0 && bytes.length < 8192)) throw new JevError("Invalid request size");
      let body: unknown;
      try {
        body = JSON.parse(new TextDecoder().decode(bytes));
      } catch {
        throw new JevError("Request body must be JSON");
      }
      if (body === null || typeof body !== "object" || Array.isArray(body)) {
        throw new JevError("Request body must be a JSON object");
      }
      const name = path.startsWith("/api/") ? path.slice("/api/".length) : path;
      return send(200, JSON.stringify(await command(name, body as Record<string, unknown>)));
    } catch (error) {
      if (error instanceof JevError) return send(400, JSON.stringify({ error: error.message }));
      console.error(error);
      return send(500, JSON.stringify({ error: "Local demo failed; no automatic retry. Reset to recover." }));
    } finally {
      busy = null;
      done();
    }
  };

  const server = Deno.serve({ hostname: "127.0.0.1", port, onListen: () => {} }, async (request) => {
    if (request.headers.get("Host") !== host) return send(403, "Forbidden", "text/plain");
    if (stopping) return send(503, JSON.stringify({ error: "Shutting down" }));
    const path = new URL(request.url).pathname;
    try {
      if (request.method === "GET") return await get(path);
      if (request.method === "POST") return await post(request, path);
      return send(405, "Method not allowed", "text/plain");
    } catch (error) {
      console.error(error);
      return send(500, "Internal error", "text/plain");
    }
  });
  console.log(`Jev Ultrafast: ${origin}`);

  const stop = async () => {
    if (stopping) return;
    stopping = true;
    try {
      await closeBrowser();
    } catch (error) {
      console.error(error);
    }
    await lease?.release();
    lease = null;
    await server.shutdown();
  };
  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    try {
      Deno.addSignalListener(signal, () => void stop());
    } catch {
      // SIGTERM is unavailable on Windows.
    }
  }
  await server.finished;
  Deno.exit(0);
}

if (import.meta.main) await main();

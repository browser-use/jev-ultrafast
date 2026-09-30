// Offline contracts for Chrome discovery and connection sharing. No socket is opened and no port is probed.
import { assert, assertEquals, assertRejects, assertStrictEquals } from "@std/assert";
import { acquireCdp, type CdpConnection, CdpError, resolveWsUrl } from "../src/mod.ts";
import { _setSharedConnect, profileDirs } from "../src/cdp.ts";

const ENV = { HOME: "/home/u", USERPROFILE: "C:\\Users\\u", LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" };
const slashes = (dirs: string[]) => dirs.map((d) => d.replaceAll("\\", "/"));

Deno.test("profileDirs lists the Linux Chrome-family profiles", () => {
  const linux = slashes(profileDirs("linux", ENV));
  assertEquals(linux.length, 10);
  assert(linux[0].endsWith("/home/u/.config/google-chrome"), linux[0]);
  assert(linux.at(-1)!.endsWith(".var/app/com.microsoft.Edge/config/microsoft-edge"));
});

Deno.test("profileDirs lists the macOS Chrome-family profiles", () => {
  const mac = slashes(profileDirs("darwin", ENV));
  assertEquals(mac.length, 11);
  assert(mac[0].endsWith("/home/u/Library/Application Support/Google/Chrome"), mac[0]);
  assert(mac.at(-1)!.endsWith("Library/Application Support/BraveSoftware/Brave-Origin"));
});

Deno.test("profileDirs lists the Windows profiles under LOCALAPPDATA", () => {
  const windows = slashes(profileDirs("windows", ENV));
  assertEquals(windows.length, 10);
  assert(windows[0].endsWith("AppData/Local/Google/Chrome/User Data"), windows[0]);
  assert(windows[1].endsWith("Google/Chrome SxS/User Data"));
  const fallback = slashes(profileDirs("windows", { HOME: "/home/u" }));
  assert(fallback[0].endsWith("/home/u/AppData/Local/Google/Chrome/User Data"), fallback[0]);
});

Deno.test("resolveWsUrl honours BU_CDP_WS before any discovery", async () => {
  const ws = "ws://127.0.0.1:9333/devtools/browser/abc";
  // BU_CDP_URL points nowhere; reaching it would need --allow-net and fail the test.
  assertEquals(await resolveWsUrl({ BU_CDP_WS: ws, BU_CDP_URL: "http://127.0.0.1:1" }), ws);
});

/** A stand-in for CdpConnection that only tracks closing. */
function fakeConnection() {
  let markClosed!: () => void;
  const whenClosed = new Promise<void>((resolve) => markClosed = resolve);
  const connection = {
    closes: 0,
    whenClosed,
    close() {
      connection.closes++;
      markClosed();
      return Promise.resolve();
    },
  };
  return connection;
}

Deno.test("acquireCdp counts a waiter before a concurrent last release can close the socket", async () => {
  const connections: ReturnType<typeof fakeConnection>[] = [];
  const restore = _setSharedConnect(() => {
    const connection = fakeConnection();
    connections.push(connection);
    return Promise.resolve(connection as unknown as CdpConnection);
  });
  try {
    const first = await acquireCdp();
    const pending = acquireCdp(); // suspended on the already-resolved connection
    await first.release(); // not the last reference: the waiter already counts
    const second = await pending;
    assertStrictEquals(second.cdp, first.cdp);
    assertEquals(connections.length, 1);
    assertEquals(connections[0].closes, 0);
    await second.release();
    assertEquals(connections[0].closes, 1);
  } finally {
    restore();
  }
});

Deno.test("acquireCdp clears the shared slot after a failed connect", async () => {
  let attempts = 0;
  const connection = fakeConnection();
  const restore = _setSharedConnect(() =>
    ++attempts === 1
      ? Promise.reject(new CdpError("Could not connect"))
      : Promise.resolve(connection as unknown as CdpConnection)
  );
  try {
    await assertRejects(() => acquireCdp(), CdpError, "Could not connect");
    const lease = await acquireCdp();
    assertEquals(attempts, 2);
    await lease.release();
    assertEquals(connection.closes, 1);
  } finally {
    restore();
  }
});

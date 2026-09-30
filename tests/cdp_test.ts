// Offline contracts for Chrome discovery. No socket is opened and no port is probed.
import { assert, assertEquals } from "@std/assert";
import { resolveWsUrl } from "../src/mod.ts";
import { profileDirs } from "../src/cdp.ts";

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

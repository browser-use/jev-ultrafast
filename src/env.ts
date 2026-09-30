/** Minimal .env loader: KEY=value lines, first "=" splits, no trimming, existing variables win. */

import { join } from "@std/path";

/** Load `path` (default `./.env`) into the environment without overriding variables that are already set. */
export function loadEnvironment(path: string = join(Deno.cwd(), ".env")): void {
  let content: string;
  try {
    content = Deno.readTextFileSync(path);
  } catch (error) {
    if (error instanceof Deno.errors.NotFound) return;
    throw error;
  }
  for (const line of content.split(/\r\n|\r|\n/)) {
    if (!line.includes("=") || line.startsWith("#")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index);
    if (Deno.env.get(key) === undefined) Deno.env.set(key, line.slice(index + 1));
  }
}

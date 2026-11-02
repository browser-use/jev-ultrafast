# Browser automation skill

Use the `browser_task` tool for anything that needs interacting with a real website:
clicking, typing, selecting, scrolling, navigating, filling forms, or reading what
is currently visible on a page.

Rules:

- One `browser_task` call = one clear, self-contained browser mission, e.g.
  `{"goal": "Open the Wikipedia article about the Eiffel Tower", "url": "https://en.wikipedia.org"}`.
  The browser executor plans and runs the steps; you get back the actions taken,
  the final URL, and a text excerpt of the resulting page.
- Pass a starting `url` when the mission names a site; omit it to continue on the
  tab the agent already controls.
- Do not try to click or type by other means. There are no DOM tools here; the
  browser tool is the only way to interact with pages.
- Sites with aggressive bot protection may fail. Report that plainly instead of
  retrying the same mission more than twice.

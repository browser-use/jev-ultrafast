# Getting started without a console (GUI-only guide)

> 🇪🇸 **¿Español o primera vez absoluta?** Lee primero **[EMPEZAR-AQUI.md](../EMPEZAR-AQUI.md)** — el manual paso a paso más detallado.

You can use, install, and even re-publish this project **without opening a terminal**. This guide covers download, first run, the Firefox extension, and how to put the project on your own GitHub using only the web interface.

## What you need

- **Firefox** (109 or newer).
- **Python 3.12+** from [python.org/downloads](https://www.python.org/downloads/) — on Windows, tick *"Add python.exe to PATH"* during install.
- **One API key**: a Groq key (free, [console.groq.com](https://console.groq.com)) for the fast executor, and/or an NVIDIA NIM key ([build.nvidia.com](https://build.nvidia.com)) for the planner and text helper.

## 1. Get the code

Two ways, both point-and-click:

- **From the repository page**: the green **Code** button → **Download ZIP** → unzip it wherever you like.
- **From Releases**: ready-made artifacts — the source ZIP and the extension as a single `.xpi` file you can hand to Firefox directly.

## 2. Start the host (double-click)

The agent runs on your machine as a small local process ("the host"). Unzip the project and double-click the starter for your system:

| System | Double-click | What happens |
| --- | --- | --- |
| Windows | `start-host.bat` | First run: creates a private Python environment (about a minute, internet needed), then opens the `.env` settings file in Notepad. |
| macOS | `start-host.command` | Same. If macOS blocks it: right-click → **Open**. |
| Linux | `start-host.sh` | Same (run it from your file manager). |

**First run flow:** the starter creates the `.env` file and opens it. Add your keys, save, close the editor, and double-click the starter again. Example `.env`:

```ini
POLICY_PROVIDER=groq
GROQ_API_KEY=gsk_...your key...
POLICY_MODEL=openai/gpt-oss-20b

PLANNER_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...your key...
PLANNER_MODEL=z-ai/glm-5.3
```

Keep the black host window open while you use the agent. Every option is described in [providers.md](providers.md).

## 3. Install the extension in Firefox

1. Open Firefox and type `about:debugging` in the address bar.
2. Click **This Firefox** → **Load Temporary Add-on…**
3. Pick `extension/manifest.json` inside the project folder (or the `.xpi` from the Release page — the file picker filters by type; choose *All files* if needed).
4. Open the sidebar with the Jev toolbar button (or menu → View → Sidebar → **Jev Agent**).

The dot in the sidebar header turns **green** when it reaches the host. If it stays red, start the host first (step 2).

> The add-on is unsigned, so Firefox treats it as *temporary*: it disappears when Firefox restarts. Load it again after a restart — or sign it via [addons.mozilla.org](https://addons.mozilla.org/developers/) for a permanent install.

## 4. Run your first task

1. Navigate to any normal website in the current tab.
2. Type a goal in the sidebar, e.g. *“Find one-way flights from Zurich to London on September 20, 2026”*.
3. Press **Run** and watch the checklist fill with ✓ as steps complete. **Stop** halts after the current action.

## 5. Put it on your own GitHub (no terminal)

Four point-and-click options, best first:

| Method | How | Keeps history |
| --- | --- | --- |
| **Fork** (simplest) | Click **Fork** at the top of the repository page. You get your own copy instantly. | Yes |
| **Import repository** | Go to [github.com/new/import](https://github.com/new/import), paste the repository URL, choose a name, click **Begin import**. | Yes |
| **Web upload** | Create a new empty repository → **"uploading an existing file"** → drag the *contents* of the unzipped folder into the browser (Chrome/Edge; folder drag works). This project is ~60 files, under GitHub's 100-file-per-upload limit — one drag is enough. | No |
| **GitHub Desktop** | Install [desktop.github.com](https://desktop.github.com), clone the original, then *Repository → Add remote* / push to your new empty repository. GUI app, no terminal. | Yes |

> When uploading manually, **never upload your `.env`** — it holds your API keys. The `.gitignore` already excludes it for git-based methods.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Python 3.12 or newer is required` | Install Python from python.org and run the starter again. |
| Starter window closes instantly | Open it from a terminal once to read the error, or reinstall Python with *Add to PATH*. |
| Sidebar dot stays red | The host is not running — double-click the starter and wait for `bridge: ws://127.0.0.1:8767`. |
| `Model provider returned HTTP 401` | A key in `.env` is missing or wrong (see [providers.md](providers.md)). |
| Sidebar says *offline* right after Firefox restarts | Temporary add-ons are removed on restart — load it again via `about:debugging`. |
| Agent refuses to act on a page | Privileged pages (`about:*`, add-ons manager) cannot be scripted; use a normal website. |

Next: the full architecture and security model live in [firefox-extension.md](firefox-extension.md).

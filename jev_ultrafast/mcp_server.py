"""Goal-level MCP server. The client supplies a goal; Jev chooses every element.

No tool here accepts an element index, a CSS selector, a coordinate, or a script.
A client asks for an outcome and Jev's policy decides which observed control to use,
so the guarantee the library makes -- model output never becomes a selector -- still
holds across the MCP boundary. Browser Harness ships a separate low-level MCP server
for clients that do want to drive the browser directly.

Run:
    uv run jev-mcp

The server speaks MCP over stdio and returns JSON text from every tool.
"""

import atexit
import functools
import json
import os
import sys
import threading
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version

try:
    from mcp.server import MCPServer
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "jev-mcp needs the optional MCP dependency. Install it with: uv sync --extra mcp"
    ) from None

from .agent import Agent
from .demo import load_environment
from .model import action_space

try:
    VERSION = version("jev-ultrafast")
except PackageNotFoundError:  # Running from a source tree that was never installed.
    VERSION = "0"

SERVER = MCPServer("jev-ultrafast", version=VERSION)
LOCK = threading.Lock()
AGENT = None

# A client that forgets to pass a budget still gets a run that ends.
DEFAULT_BUDGET_MS = 120_000
TEXT_LIMIT = 2000


@contextmanager
def quiet_stdout():
    """Send stray prints to stderr. Anything else on stdout corrupts the JSON-RPC stream."""
    saved = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = saved


def dump(value):
    """Serialize a tool result to JSON text without raising on odd values."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def close_agent():
    """Close the open run, if any. Safe to call twice."""
    global AGENT
    if AGENT is not None:
        try:
            AGENT.close()
        finally:
            AGENT = None


def require_agent():
    """Return the open run, or explain that there is none."""
    if AGENT is None:
        raise ValueError("No run is open. Call jev_start or jev_run first.")
    return AGENT


def require_credentials():
    """Fail before opening a browser tab, not several seconds into a run."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise ValueError(
            "TYPESAFE_API_KEY is unset. Put it in a .env file in the server's working "
            "directory, or in the env block of the MCP client's server entry."
        )


def warnings():
    """Flag a missing credential that only some goals need."""
    if os.environ.get("TEXT_MODEL_API_KEY"):
        return []
    return ["TEXT_MODEL_API_KEY is unset; this run will stop if the goal requires typing text."]


def budget(timeout_ms):
    """Apply the default only when a client omitted the timeout.

    `timeout_ms or DEFAULT` would turn an explicit 0 into two minutes; passing it through
    lets Agent reject it, which is what a client that sent 0 needs to hear.
    """
    return DEFAULT_BUDGET_MS if timeout_ms is None else timeout_ms


def page_view(state):
    """The indexed element table Jev itself observed, plus visible text."""
    page = state["page"]
    elements, _targets, _controls = action_space(page["actions"])
    return {
        "url": page["url"],
        "title": page["title"],
        "text": page["text"][:TEXT_LIMIT],
        "elements": elements,
        "omitted_elements": page.get("omitted_actions", 0),
    }


def tool(fn):
    """Expose a function as an MCP tool returning JSON text.

    One browser serves one run, so a second concurrent call is refused rather than
    queued: a client must not be able to stack browser mutations behind each other.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not LOCK.acquire(blocking=False):
            return dump({"error": "A browser step is already running."})
        try:
            with quiet_stdout():
                return dump(fn(*args, **kwargs))
        except (ValueError, RuntimeError, TimeoutError) as error:
            return dump({"error": str(error)})
        except Exception as error:  # noqa: BLE001 -- a tool must serialize any browser failure.
            return dump({"error": f"{type(error).__name__}: {error}"})
        finally:
            LOCK.release()

    return SERVER.tool(name=fn.__name__, description=fn.__doc__ or "")(wrapper)


@tool
def jev_run(url: str, goal: str, max_actions: int | None = None, timeout_ms: int | None = None):
    """Pursue one natural-language goal in a fresh tab until Jev stops, then report what happened.

    Jev selects every operation and every element. Name the outcome you want, not the
    controls to use. `max_actions` caps browser actions for this run; `timeout_ms` caps
    wall-clock time and defaults to 120000. That clock starts at the first decision, so
    opening the tab and reading the page once sit outside it, as they do in the project's
    published timings; browser startup is separately bounded at roughly 15 seconds.

    `status` is the run's own stopping condition, never proof that the goal succeeded:
    `verified` is always null because only an independent check of the returned page can
    decide that. Read `page` and judge for yourself before trusting a `done` status.
    """
    global AGENT
    require_credentials()
    close_agent()
    AGENT = Agent(url, goal, max_actions=max_actions, budget_ms=budget(timeout_ms))
    error = None
    try:
        for _ in AGENT.run():
            pass
    except (ValueError, RuntimeError) as failure:
        # Keep the partial run: the steps already executed are still what the page saw.
        error = str(failure)
    return {**AGENT.summary(), "page": page_view(AGENT.state), "warnings": warnings(), "error": error}


@tool
def jev_start(url: str, goal: str, max_actions: int | None = None, timeout_ms: int | None = None):
    """Open a tab on `url` for `goal` and observe it, without deciding anything yet.

    Use this when you want to watch the run: call jev_step to advance one decision at a
    time. Closes any run already open. Budgets match jev_run, including the exclusion of
    tab creation and the first observation from `timeout_ms`.
    """
    global AGENT
    require_credentials()
    close_agent()
    AGENT = Agent(url, goal, max_actions=max_actions, budget_ms=budget(timeout_ms))
    return {**AGENT.summary(), "page": page_view(AGENT.state), "warnings": warnings()}


@tool
def jev_step():
    """Advance the open run by one Jev decision cycle: observe, choose an operation and a
    target, execute it, observe the result.

    Returns the steps executed by this call in `executed`, plus the resulting page. The
    choice is Jev's; this tool takes no arguments precisely so a caller cannot make it.
    """
    agent = require_agent()
    if agent.state["status"] in {"done", "blocked"}:
        return {
            **agent.summary(),
            "executed": [],
            "page": page_view(agent.state),
            "note": "This run has stopped. Start another with jev_start.",
        }
    before = len(agent.state["history"])
    error = None
    try:
        agent.command("tick")
    except (ValueError, RuntimeError) as failure:
        # A budget stop is an outcome, not a lost call: report it the way jev_run does.
        error = str(failure)
    result = agent.summary()
    return {
        **result,
        "executed": result["steps"][before:],
        "page": page_view(agent.state),
        "error": error,
    }


@tool
def jev_observe():
    """Re-read the open tab and return its current element table and visible text.

    Read-only: no decision, no model call, no browser mutation, no budget spent.
    """
    agent = require_agent()
    agent.state["page"] = agent.browser.observe(screenshot=agent.screenshots)
    return {**agent.summary(), "page": page_view(agent.state)}


@tool
def jev_close():
    """Close the open tab and end the run. Returns that run's final summary."""
    if AGENT is None:
        return {"closed": False, "note": "No run is open."}
    result = AGENT.summary()
    close_agent()
    return {"closed": True, **result}


def main():
    """Run the Jev Ultrafast MCP server over stdio."""
    load_environment()
    atexit.register(close_agent)
    SERVER.run()


if __name__ == "__main__":
    main()

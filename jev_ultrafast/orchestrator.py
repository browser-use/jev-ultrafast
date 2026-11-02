"""The task orchestrator: routes goals and runs a JSON-protocol tool loop.

Two entry paths per the product spec: plain browser missions go straight to
the fast JEV loop (TaskRunner), while tasks that need search, files, documents,
or the terminal run here. The orchestrator model replies with one JSON object
per turn — {"tool": ..., "args": ...} to act or {"final": ...} to finish — so
every chat model works, with or without native tool calling.
"""

import time

from . import providers
from .providers import extract_json
from .skills import select_skills, skill_instructions

ORCHESTRATED_KEYWORDS = (
    # documents
    "pdf", "docx", "xlsx", "excel", "spreadsheet", "word document",
    # files and projects
    "project", "script", "create a file", "write a file", "save", "download",
    "crea un", "crea el", "escribe", "descarga", "archivo",
    # terminal
    "run the test", "run tests", "install", "command", "terminal", "bash",
    "npm ", "pip ", "git init", "ejecuta", "instala",
    # research over multiple sources
    "research", "investigate", "find information about", "compare",
    "investiga", "busca información",
)
MAX_ORCHESTRATOR_STEPS = 25
TOOL_RESULT_BUDGET = 4000

ORCHESTRATOR_SYSTEM = """You are the orchestrator of a task agent running on the user's own computer.
Complete the user's mission with the tools below. You plan, delegate, verify, and answer.

Reply with ONLY one JSON object per turn — no markdown, no extra text:
{"tool": "<tool name>", "args": {...}}   to use a tool
{"final": "<your answer>"}               when the mission is complete

Rules:
- One tool call per reply. Wait for its result before deciding the next step.
- Use only the listed tools with the listed argument names; never invent tools.
- Cite sources as `Title — URL` in the final answer when you used web results.
- Files you create live in the task workspace; list them in the final answer.
- If a tool fails twice the same way, change approach or report the blocker.
- Never claim a command, test, or download succeeded without reading its actual output.
- The user's language: reply in the language of the mission."""


def route_task(goal):
    """'browser' for pure browsing missions; 'orchestrated' when tools are needed.

    Laya (the open System-1 decision engine) routes first when installed — it
    works in any language, not just the keyword lists below — and the keywords
    stay as the always-available fallback.
    """
    from . import laya_local

    decided = laya_local.route_mission(goal)
    if decided is not None:
        return decided
    lowered = goal.lower()
    if any(keyword in lowered for keyword in ORCHESTRATED_KEYWORDS):
        return "orchestrated"
    return "browser"


def _tool_section(toolbox):
    available = toolbox.tool_descriptions()
    return f"AVAILABLE TOOLS:\n{available}" if available else "AVAILABLE TOOLS: none"


def _validate_call(answer, toolbox):
    if not isinstance(answer, dict):
        raise ValueError("not an object")
    if "final" in answer:
        final = answer["final"]
        if not isinstance(final, str) or not final.strip():
            raise ValueError("empty final")
        return None, final.strip()
    tool = answer.get("tool")
    if tool not in toolbox.registry:
        raise ValueError(f"unknown tool {tool!r}")
    args = answer.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    return (tool, args), None


def run_orchestration(goal, toolbox, on_step=None, max_steps=MAX_ORCHESTRATOR_STEPS, on_delta=None):
    """Run the tool loop; returns {'final', 'steps', 'usage'}.

    The provider is the planner role (the deep model); every step is reported
    through on_step so the sidebar can show the activity live, and raw model
    output is streamed through on_delta chunk by chunk while it is generated.
    """
    provider = providers.resolve("planner")
    selected = select_skills(goal, available_tools=toolbox.registry)
    instructions = skill_instructions(selected)
    system = "\n\n".join(
        part for part in (ORCHESTRATOR_SYSTEM, _tool_section(toolbox), instructions) if part
    )
    conversation = [
        {"role": "user", "content": f"MISSION: {goal}\n\nBegin. Reply with the JSON for your first step."}
    ]
    steps = []
    usage_total = {}
    started = time.perf_counter()

    def report(step):
        steps.append(step)
        if on_step:
            on_step(step)

    for step_number in range(1, max_steps + 1):
        content, meta = providers.chat(
            provider, system, _render(conversation), max_tokens=2048, on_delta=on_delta
        )
        usage_total = _add_usage(usage_total, meta.get("usage") or {})
        try:
            answer = extract_json(content)
            call, final = _validate_call(answer, toolbox)
        except ValueError as error:
            twice = _last_was_invalid(conversation) and step_number >= 2
            if twice:
                return _invalid_loop_final(error, steps, usage_total, started, report, step_number)
            conversation.append({"role": "assistant", "content": content[:1000]})
            conversation.append(
                {"role": "user", "content": f"Invalid reply ({error}). Respond with ONE JSON object: "
                 '{"tool": ..., "args": {...}} or {"final": ...}.'}
            )
            continue
        if final is not None:
            report({"step": step_number, "tool": None, "final": final})
            return _result(final, steps, usage_total, started)
        tool, args = call
        report({"step": step_number, "tool": tool, "args": args})
        try:
            result = toolbox.call(tool, args)
        except Exception as error:  # noqa: BLE001 - tool failures are part of the loop
            result = f"TOOL ERROR: {error}"
            steps[-1]["error"] = str(error)[:300]
        steps[-1]["result"] = str(result)[:600]
        conversation.append({"role": "assistant", "content": _compact_json(answer)})
        result_text = str(result)[:TOOL_RESULT_BUDGET]
        conversation.append({
            "role": "user",
            "content": f"TOOL RESULT ({tool}):\n{result_text}\n\n"
            'Continue with the next JSON step, or finish with {"final": ...}.',
        })
    forced = "Stopped at the maximum number of steps. Partial results are in the workspace and the log above."
    report({"step": max_steps, "tool": None, "final": forced})
    return _result(forced, steps, usage_total, started)


def _invalid_loop_final(error, steps, usage_total, started, report, step_number):
    message = "The orchestrator could not produce valid tool calls. Try rephrasing the mission."
    report({"step": step_number, "tool": None, "error": f"Invalid JSON twice in a row ({error}); stopped.",
            "final": message})
    return _result(message, steps, usage_total, started)


def _last_was_invalid(conversation):
    return bool(conversation) and conversation[-1]["role"] == "user" and "Invalid reply" in conversation[-1]["content"]


def _render(conversation):
    return "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in conversation[-12:])


def _compact_json(answer):
    import json

    return json.dumps(answer, ensure_ascii=False)


def _add_usage(total, usage):
    for key, value in usage.items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
    return total


def _result(final, steps, usage, started):
    return {
        "final": final,
        "steps": steps,
        "usage": usage,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }

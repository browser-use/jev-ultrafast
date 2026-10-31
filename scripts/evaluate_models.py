import json
import os
import subprocess
import sys
import time
from pathlib import Path
from jev_ultrafast.demo import load_environment

load_environment()

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9222
USER_DATA = Path(os.environ.get("TEMP", ".")) / "chrome-automation-eval"
FIXTURE_URL = "http://127.0.0.1:8766/fixture.html?scenario=travel"
GOAL = "Use the destination search and filters to find Lisbon stays with free cancellation, then open Casa Flora."

CANDIDATES = [
    ("meta-llama/llama-3.2-3b-instruct", "none"),
    ("mistralai/mistral-small-24b-instruct-2501", "low"),
    ("mistralai/mistral-small-24b-instruct-2501", "none"),
    ("meta-llama/llama-3.1-8b-instruct", "none"),
    ("deepseek/deepseek-chat", "none"),
    ("nvidia/nemotron-3.5-lightning:free", "none"),
]

def run_evaluation_for_model(model_name, reasoning_setting):
    os.environ["TEXT_MODEL"] = model_name
    os.environ["TEXT_MODEL_REASONING"] = reasoning_setting
    # Ensure TYPESAFE_API_KEY is unset so fallback is always triggered
    if "TYPESAFE_API_KEY" in os.environ:
        del os.environ["TYPESAFE_API_KEY"]

    from jev_ultrafast import Agent

    print(f"\n=======================================================", flush=True)
    print(f"EVALUATING: {model_name} (reasoning={reasoning_setting})", flush=True)
    print(f"=======================================================", flush=True)

    run_start = time.perf_counter()
    step_records = []
    error = None
    verified = False
    verification_text = ""

    try:
        with Agent(FIXTURE_URL, GOAL) as agent:
            max_steps = 10
            for i in range(max_steps):
                if agent.state["status"] in {"done", "blocked"}:
                    break
                # Predict
                t_pred = time.perf_counter()
                agent.command("predict", {})
                pred_ms = round((time.perf_counter() - t_pred) * 1000)
                dec = agent.state["decision"]

                # Act
                t_act = time.perf_counter()
                fp = agent.state["page"]["fingerprint"]
                agent.command("act", {"fingerprint": fp})
                act_ms = round((time.perf_counter() - t_act) * 1000)

                hist = agent.state["history"][-1] if agent.state["history"] else {}
                step_records.append({
                    "step": i + 1,
                    "operation": dec.get("operation"),
                    "target": dec.get("target"),
                    "choice": dec.get("choice"),
                    "decision_latency_ms": dec.get("latency_ms", pred_ms),
                    "text_value": hist.get("text"),
                    "text_latency_ms": hist.get("text_latency_ms", 0),
                    "action_name": hist.get("action"),
                    "status": agent.state["status"]
                })
                print(
                    f"  Step {i+1}: Op={dec.get('operation')} Target={dec.get('target')} "
                    f"Action='{hist.get('action')}' Text='{hist.get('text')}' "
                    f"DecLat={dec.get('latency_ms')}ms TextLat={hist.get('text_latency_ms',0)}ms Status={agent.state['status']}",
                    flush=True
                )

            # Verification
            verification_text = agent.browser.evaluate("document.body.innerText") or ""
            current_url = agent.state["page"]["url"]
            verified = (
                agent.state["status"] == "done"
                and current_url.endswith("#casa-flora")
                and "Free cancellation included" in verification_text
                and "Lisbon" in verification_text
            )
    except Exception as e:
        error = str(e)
        print(f"  FAILED with error: {e}", flush=True)

    total_time = round((time.perf_counter() - run_start) * 1000)
    decision_lats = [s["decision_latency_ms"] for s in step_records if s["decision_latency_ms"]]
    text_lats = [s["text_latency_ms"] for s in step_records if s["text_latency_ms"]]

    result = {
        "model": model_name,
        "reasoning": reasoning_setting,
        "verified": verified,
        "total_ms": total_time,
        "steps_count": len(step_records),
        "avg_decision_latency_ms": round(sum(decision_lats) / len(decision_lats), 1) if decision_lats else None,
        "min_decision_latency_ms": min(decision_lats) if decision_lats else None,
        "max_decision_latency_ms": max(decision_lats) if decision_lats else None,
        "avg_text_latency_ms": round(sum(text_lats) / len(text_lats), 1) if text_lats else None,
        "steps": step_records,
        "error": error
    }
    print(f"RESULT: Verified={verified}, Total={total_time}ms, Steps={len(step_records)}, AvgDecLat={result['avg_decision_latency_ms']}ms", flush=True)
    return result

def main():
    # Kill any existing chrome on port 9222
    subprocess.run(
        ["powershell", "-Command", f"Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | Where-Object {{ $_.CommandLine -like '*{PORT}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1)

    chrome_proc = subprocess.Popen(
        [
            CHROME_PATH,
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={USER_DATA}",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    os.environ["BU_CDP_URL"] = f"http://127.0.0.1:{PORT}"

    results = []
    try:
        for model_name, reasoning in CANDIDATES:
            res = run_evaluation_for_model(model_name, reasoning)
            results.append(res)
            time.sleep(1)
    finally:
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            chrome_proc.kill()

    out_file = Path("artifacts/openrouter_benchmark_results.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\nALL BENCHMARKS SAVED TO:", out_file.resolve(), flush=True)

if __name__ == "__main__":
    main()

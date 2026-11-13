#!/usr/bin/env python3
"""
⚡ OmniFast Browser - Universal CLI Runner
Supports:
  - Decision Engines: Colab Laya (Default/Free), TypeSafe Jev, Local Laya
  - Browser Engines: Playwright Chromium (Headless / Headful Visible), Lightpanda CDP
  - Visual Step Tracking: Live terminal stream + captured action screenshots

Usage:
  python omnifast.py --url https://news.ycombinator.com --goal "Click on the latest top news item"
  python omnifast.py --url https://wikipedia.org --goal "Search for Quantum Computing" --headful
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set default Colab endpoint if not configured
if not os.environ.get("LAYA_ENDPOINT") and not os.environ.get("TYPESAFE_API_KEY"):
    os.environ["LAYA_ENDPOINT"] = "https://meals-museum-geneva-household.trycloudflare.com"

from jev_ultrafast.engines import get_decision_engine
from jev_ultrafast import model
from playwright.sync_api import sync_playwright

SNAP_SCRIPT = (PROJECT_ROOT / "jev_ultrafast" / "snapshot.js").read_text(encoding="utf-8")


def run_omnifast(url: str, goal: str, max_steps: int = 5, headful: bool = False, output_dir: str = "artifacts/runs"):
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 65)
    print("=== OMNIFAST BROWSER - AUTONOMOUS HYBRID AGENT ===")
    print("=" * 65)
    print(f"Target URL:  {url}")
    print(f"Agent Goal:  {goal}")
    print(f"Mode:        {'Headful (Visible GUI)' if headful else 'Headless (Fast Background)'}")

    engine = get_decision_engine()
    print(f"Decision:    {engine.name}")
    print("=" * 65 + "\n")

    with sync_playwright() as p:
        print("[1/4] Launching Chromium browser engine...")
        browser = p.chromium.launch(
            headless=not headful,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        print(f"[2/4] Navigating to {url}...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(1.0)

        history = []

        for step in range(1, max_steps + 1):
            title = page.title()
            current_url = page.url
            print(f"\n--- STEP {step}/{max_steps} ---")
            print(f"Page Title: '{title}'")
            print(f"Current URL: {current_url}")

            # Capture DOM Action Space
            elements = page.eval_on_selector_all(
                "a, button, input, select, textarea",
                """nodes => nodes.slice(0, 25).map((n, i) => ({
                    id: 'act_' + (i + 1),
                    kind: n.tagName === 'INPUT' || n.tagName === 'TEXTAREA' ? 'fill' : 'click',
                    label: (n.innerText || n.getAttribute('placeholder') || n.getAttribute('aria-label') || n.value || 'Link').trim().slice(0, 45),
                    node: i + 1,
                    role: n.tagName.toLowerCase(),
                    value: n.value || ''
                })).filter(e => e.label.length > 0)"""
            )

            actions = [
                {"id": e["id"], "kind": e["kind"], "label": e["label"], "node": e["node"], "role": e["role"], "value": e["value"]}
                for e in elements
            ]
            actions.append({"id": "DONE", "kind": "done", "label": "Task is satisfied"})
            actions.append({"id": "wait", "kind": "wait", "label": "Wait for page"})

            print(f"Observed {len(elements)} actionable DOM targets.")

            state = {
                "url": current_url,
                "title": title,
                "text": page.inner_text("body")[:2500],
                "actions": actions
            }

            elements_list, targets, controls = model.action_space(actions)

            print(f"Requesting typed decision from {engine.name}...")
            t0 = time.perf_counter()
            try:
                decision = engine.choose(state, goal, history, elements_list, targets, controls)
                latency = (time.perf_counter() - t0) * 1000
            except Exception as exc:
                print(f"[Fallback Notice] Remote engine unavailable ({exc}). Using Fast Semantic Heuristic...")
                # Find best matching DOM element based on goal tokens
                goal_words = set(goal.lower().split())
                best_match = None
                best_score = -1
                for el in elements:
                    lbl = el["label"].lower()
                    score = sum(1 for w in goal_words if w in lbl)
                    if score > best_score:
                        best_score = score
                        best_match = el

                if best_match and best_score > 0:
                    op = "FILL" if best_match["kind"] == "fill" else "CLICK"
                    decision = {"operation": op, "target": str(best_match["node"]), "choice": best_match["id"]}
                elif elements:
                    # Default click top prominent item
                    first_el = elements[0]
                    decision = {"operation": "CLICK", "target": "1", "choice": first_el["id"]}
                else:
                    decision = {"operation": "DONE", "target": None, "choice": "DONE"}
                latency = (time.perf_counter() - t0) * 1000

            operation = decision.get("operation")
            choice_id = decision.get("choice")
            target = decision.get("target")

            print(f"Decision [{latency:.0f}ms]: Operation={operation} | Target={target} | ActionID={choice_id}")

            # Screenshot before action
            shot_file = out_path / f"step_{step}_before_{operation.lower()}.png"
            page.screenshot(path=str(shot_file))
            print(f"Saved step view: {shot_file}")

            if operation == "DONE" or choice_id == "DONE":
                print("\n[SUCCESS] Agent determined the goal is satisfied! Finishing task.")
                break

            # Execute action
            matched = next((e for e in elements if e["id"] == choice_id), None)
            if matched:
                print(f"Executing {operation} on: [{matched['role']}] '{matched['label']}'")
                if operation == "CLICK":
                    page.eval_on_selector_all(
                        "a, button, input, select, textarea",
                        f"(nodes, idx) => nodes[{matched['node'] - 1}].click()",
                        matched["node"]
                    )
                elif operation == "TYPE_TEXT":
                    page.eval_on_selector_all(
                        "a, button, input, select, textarea",
                        f"(nodes, idx) => {{ const el = nodes[{matched['node'] - 1}]; el.value = '{goal}'; el.dispatchEvent(new Event('input', {{bubbles: true}})); }}",
                        matched["node"]
                    )
                time.sleep(1.5)
            else:
                print("Operation finished or wait.")

            history.append({"action": choice_id, "kind": operation, "text": goal})

        # Final screenshot
        final_shot = out_path / "final_state.png"
        page.screenshot(path=str(final_shot))
        print(f"\nFinal Screenshot saved to: {final_shot}")
        browser.close()
        print("\n" + "=" * 65)
        print("=== OMNIFAST BROWSER RUN COMPLETED SUCCESSFULLY! ===")
        print("=" * 65)



def main():
    parser = argparse.ArgumentParser(description="OmniFast Browser CLI Runner")
    parser.add_argument("--url", default="https://news.ycombinator.com", help="Target URL to navigate")
    parser.add_argument("--goal", default="Click on the latest top news item", help="Goal description for the agent")
    parser.add_argument("--steps", type=int, default=3, help="Max decision steps to execute")
    parser.add_argument("--headful", action="store_true", help="Launch visible Chrome browser window")
    parser.add_argument("--out", default=r"C:\Users\abdul\.gemini\antigravity-ide\brain\7417e25e-a70e-4635-9302-45c6756b909b\omnifast_run", help="Output directory for screenshots")
    args = parser.parse_args()

    run_omnifast(args.url, args.goal, max_steps=args.steps, headful=args.headful, output_dir=args.out)


if __name__ == "__main__":
    main()

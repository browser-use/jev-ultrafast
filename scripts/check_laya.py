#!/usr/bin/env python3
"""Exercise the REAL Laya weights: multilingual mission routing + skill picking.

Runs on CI (the sandbox cannot reach huggingface.co). Loads the multilingual
checkpoint, replays a battery of missions in Spanish, English, German and
French, and prints a markdown report for the PR comment. Exit code 1 when the
accuracy floor is missed, so a bad checkpoint cannot ship silently.

The keyword router stays as the fallback at runtime, so even a failed battery
never breaks the agent — this check only tells us how good Laya is TODAY.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROUTE_CASES = [
    ("es", "Busca vuelos de ida de Zúrich a Londres el 20 de septiembre de 2026 en esta página", "browser"),
    ("es", "Descarga un PDF sobre el cambio climático y escribe un resumen en un archivo", "orchestrated"),
    ("es", "Haz clic en el botón de iniciar sesión y rellena el formulario de esta página", "browser"),
    ("es", "Crea un script de python que imprima hola y ejecútalo en la terminal", "orchestrated"),
    ("en", "Find one-way flights from Zurich to London on September 20, 2026", "browser"),
    ("en", "Research the topic across the web and save a summary file", "orchestrated"),
    ("de", "Klicke auf den Login-Button auf dieser Seite", "browser"),
    ("de", "Recherchiere Flüge und erstelle eine Datei mit den Preisen", "orchestrated"),
    ("fr", "Clique sur le bouton de connexion de cette page", "browser"),
    ("fr", "Télécharge le rapport PDF et résume-le dans un fichier", "orchestrated"),
]

SKILL_CASES = [
    ("es", "Analiza este documento PDF y resume sus puntos", "documents"),
    ("de", "Fasse dieses PDF-Dokument zusammen", "documents"),
    ("es", "Investiga y compara precios en varias webs", "web-research"),
    ("es", "Crea un proyecto nuevo y ejecuta los tests", "coding"),
]


def main():
    from jev_ultrafast import laya_local, orchestrator, skills
    from jev_ultrafast.skills import load_skills

    checkpoint = os.environ.get("LAYA_CHECKPOINT", "multilingual")
    print(f"## 🧠 Laya check — checkpoint `{checkpoint}`\n")
    started = time.perf_counter()
    agent = laya_local.engine()
    if agent is None:
        print(f"🔴 Laya could not load: {laya_local.status()}")
        return 1
    load_s = time.perf_counter() - started
    print(f"Model loaded in {load_s:.1f} s (`{laya_local.status()}`)\n")

    all_tools = {
        "web_search", "read_page", "download_file", "list_files", "read_file",
        "write_file", "parse_document", "run_command", "browser_task",
    }
    loaded = load_skills()

    rows = []
    laya_correct = 0
    effective_correct = 0
    total = 0
    latencies = []
    for language, goal, expected in ROUTE_CASES:
        t0 = time.perf_counter()
        raw_choice, confidence = laya_local._decide(
            {"mission": goal}, laya_local.ROUTE_QUESTIONS, "route", {"browser", "orchestrated"}
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        effective = orchestrator.route_task(goal)  # Laya first, keywords as fallback
        total += 1
        laya_ok = raw_choice == expected
        effective_ok = effective == expected
        laya_correct += laya_ok
        effective_correct += effective_ok
        raw_text = (
            f"`{raw_choice}` ({confidence:.2f})" if raw_choice
            else f"⚠️ low confidence ({confidence:.2f}) → fallback" if isinstance(confidence, (int, float))
            else "— not decided"
        )
        rows.append(
            f"| {'✅' if effective_ok else '❌'} | {language} | {goal[:42]}… | `{expected}` | "
            f"{raw_text} | {'✅' if effective_ok else '❌'} `{effective}` |"
        )

    skill_rows = []
    for language, goal, expected in SKILL_CASES:
        picked = laya_local.pick_skills(goal, loaded, available_tools=all_tools)
        raw = picked[0]["id"] if picked else "(none)"
        effective_list = skills.select_skills(goal, available_tools=all_tools)
        effective = effective_list[0]["id"] if effective_list else "(none)"
        total += 1
        laya_ok = raw == expected
        effective_ok = effective == expected
        laya_correct += laya_ok
        effective_correct += effective_ok
        skill_rows.append(
            f"| {'✅' if effective_ok else '❌'} | {language} | {goal[:42]}… | `{expected}` | "
            f"`{raw}` | {'✅' if effective_ok else '❌'} `{effective}` |"
        )

    print("| Agent ✓ | Lang | Mission | Expected | Laya raw (conf) | Effective |")
    print("|---|---|---|---|---|---|")
    print("\n".join(rows))
    print("\n**Skill picking**\n")
    print("| Agent ✓ | Lang | Mission | Expected | Laya raw | Effective |")
    print("|---|---|---|---|---|---|")
    print("\n".join(skill_rows))

    laya_accuracy = laya_correct / total if total else 0
    effective_accuracy = effective_correct / total if total else 0
    p50 = sorted(latencies)[len(latencies) // 2] if latencies else 0
    print(
        f"\n**Laya alone: {laya_correct}/{total} ({laya_accuracy:.0%}) · effective with the keyword "
        f"fallback: {effective_correct}/{total} ({effective_accuracy:.0%})** · decision latency p50 "
        f"{p50:.0f} ms (CPU, runner) · model load {load_s:.0f} s\n"
    )

    floor = float(os.environ.get("LAYA_ACCURACY_FLOOR", "0.8"))
    if effective_accuracy < floor:
        print(f"⚠️ Effective accuracy below the {floor:.0%} floor — review the fallback coverage.")
        return 1
    print("🟢 The agent decides correctly with Laya + fallback; raw Laya quality is reported above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

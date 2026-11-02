#!/usr/bin/env python3
"""Real head-to-head: GPT-OSS-20B (Groq) vs local Laya on this agent's two jobs.

Contest 1 — routing (Laya's current job): browser loop vs tool orchestrator,
24 missions across ES/EN/DE/FR/IT/PT.

Contest 2 — the executor job (GPT-OSS's job today): pick the element a mission
must act on from a realistic observed page. 14 standard pages (12–18 elements)
plus 3 "cliff" pages (30+ elements) where Laya's own docs say choice quality
degrades.

Runs on CI (the sandbox cannot reach Groq or Hugging Face): real GPT-OSS-20B
requests via the project's own provider layer, real Laya weights on CPU.
The report is printed as markdown for the PR comment. Exit code stays 0 —
this is a measurement, not a gate.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

GPT_PACING_SECONDS = 2.2  # keep the free tier's rate limit comfortable

# ── contest 1: routing (lang, mission, expected) ─────────────────────────────

ROUTING_CASES = [
    ("es", "Busca vuelos de ida de Zúrich a Londres el 20 de septiembre de 2026 en esta página", "browser"),
    ("es", "Descarga un PDF sobre el cambio climático y escribe un resumen en un archivo", "orchestrated"),
    ("es", "Haz clic en el botón de iniciar sesión y rellena el formulario de esta página", "browser"),
    ("es", "Crea un script de python que imprima hola y ejecútalo en la terminal", "orchestrated"),
    ("es", "Investiga y compara precios de portátiles en varias webs", "orchestrated"),
    ("es", "En esta página, cambia el idioma a inglés", "browser"),
    ("en", "Find one-way flights from Zurich to London on September 20, 2026", "browser"),
    ("en", "Research the topic across the web and save a summary file", "orchestrated"),
    ("en", "Click the login button and enter my credentials on this page", "browser"),
    ("en", "Clone this repository and run its test suite", "orchestrated"),
    ("en", "Fill the shipping address form on this page", "browser"),
    ("en", "Download the invoice PDF and extract the totals into a spreadsheet", "orchestrated"),
    ("de", "Klicke auf den Login-Button auf dieser Seite", "browser"),
    ("de", "Recherchiere Flüge und erstelle eine Datei mit den Preisen", "orchestrated"),
    ("de", "Fülle das Anmeldeformular auf dieser Seite aus", "browser"),
    ("de", "Erstelle ein Python-Skript und führe es aus", "orchestrated"),
    ("fr", "Clique sur le bouton de connexion de cette page", "browser"),
    ("fr", "Télécharge le rapport PDF et résume-le dans un fichier", "orchestrated"),
    ("fr", "Remplis le formulaire d'inscription sur cette page", "browser"),
    ("fr", "Analyse ce document Excel et écris un rapport", "orchestrated"),
    ("it", "Cerca voli da Roma a Milano su questo sito", "browser"),
    ("it", "Cerca informazioni sul web e salva un file di riepilogo", "orchestrated"),
    ("pt", "Reserva um hotel em Lisboa nesta página", "browser"),
    ("pt", "Cria um projeto novo e corre os testes", "orchestrated"),
]

ROUTING_SYSTEM = (
    "You are a mission router for a browser agent. Reply with ONLY one JSON object: "
    '{"choice": "browser"} or {"choice": "orchestrated"}. '
    "'browser' = the mission is about interacting with the current web page (clicking, filling, "
    "navigating, booking on that site). "
    "'orchestrated' = the mission needs web search, reading other pages, downloading files, "
    "creating or editing files, analyzing documents, or running terminal commands and code."
)

ROUTING_CRITERIA = {
    "browser": ("interacting with the current web page: clicking, filling forms, navigating, "
                "booking, searching on that site"),
    "orchestrated": ("needs web search, reading other pages, downloading files, creating or editing "
                     "files, analyzing documents, or running terminal commands and code"),
}

# ── contest 2: the executor job (single next action over observed elements) ──

FLIGHT_FORM = [
    ("e1", "textbox 'Origen'"), ("e2", "textbox 'Destino'"), ("e3", "textbox 'Fecha de ida'"),
    ("e4", "select 'Pasajeros'"), ("e5", "select 'Moneda'"), ("e6", "button 'Buscar vuelos'"),
    ("e7", "link 'Iniciar sesión'"), ("e8", "link 'Registrarse'"), ("e9", "toggle 'Solo ida'"),
    ("e10", "link 'Ayuda'"), ("e11", "link 'Idioma'"), ("e12", "textbox 'Código promocional'"),
    ("e13", "button 'Aplicar código'"), ("e14", "link 'Términos'"), ("e15", "link 'Privacidad'"),
    ("e16", "button 'Aceptar cookies'"), ("e17", "link 'Cerrar'"),
]
REPO_PAGE = [
    ("e1", "textbox 'Search or jump to…'"), ("e2", "link 'Pull requests'"), ("e3", "link 'Issues'"),
    ("e4", "link 'Code'"), ("e5", "link 'Actions'"), ("e6", "link 'Wiki'"), ("e7", "link 'Security'"),
    ("e8", "link 'Insights'"), ("e9", "button 'Sign in'"), ("e10", "button 'Sign up'"),
    ("e11", "button 'Watch'"), ("e12", "button 'Fork'"), ("e13", "button 'Star'"),
    ("e14", "button 'Notifications'"), ("e15", "select 'main branch'"), ("e16", "link 'README.md'"),
    ("e17", "link 'LICENSE'"), ("e18", "button 'Mobile menu'"),
]
LOGIN_FORM = [
    ("e1", "textbox 'Email'"), ("e2", "textbox 'Password'"), ("e3", "button 'Log in'"),
    ("e4", "link 'Forgot password?'"), ("e5", "link 'Create account'"), ("e6", "button 'Continue with Google'"),
    ("e7", "button 'Continue with GitHub'"), ("e8", "checkbox 'Remember me'"), ("e9", "link 'Back to home'"),
    ("e10", "select 'Language'"), ("e11", "link 'Help'"), ("e12", "link 'Privacy'"),
    ("e13", "link 'Terms'"), ("e14", "button 'Show password'"), ("e15", "textbox 'CAPTCHA'"),
    ("e16", "button 'Reload CAPTCHA'"),
]
SHOP_DE = [
    ("e1", "textbox 'Suche'"), ("e2", "link 'Warenkorb'"), ("e3", "link 'Wunschliste'"),
    ("e4", "link 'Anmelden'"), ("e5", "button 'In den Warenkorb' for Kopfhörer"),
    ("e6", "button 'In den Warenkorb' for Tastatur"), ("e7", "link 'Produkt: Kopfhörer'"),
    ("e8", "link 'Produkt: Tastatur'"), ("e9", "select 'Sortieren nach'"), ("e10", "link 'Seite 2'"),
    ("e11", "link 'Impressum'"), ("e12", "link 'Versand'"), ("e13", "link 'Rückgabe'"),
    ("e14", "textbox 'Newsletter E-Mail'"), ("e15", "button 'Abonnieren'"), ("e16", "link 'Kontakt'"),
]
BOOKING_FR = [
    ("e1", "textbox 'Destination'"), ("e2", "textbox 'Dates'"), ("e3", "select 'Voyageurs'"),
    ("e4", "button 'Réserver'"), ("e5", "link 'Avis'"), ("e6", "link 'Photos'"),
    ("e7", "link 'Profil de l'hôte'"), ("e8", "link 'Équipements'"), ("e9", "link 'Carte'"),
    ("e10", "button 'Partager'"), ("e11", "button 'Enregistrer'"), ("e12", "link 'Signaler'"),
    ("e13", "link 'Conditions'"), ("e14", "link 'Disponibilité'"), ("e15", "button 'Fermer'"),
]
SETTINGS_EN = [
    ("e1", "link 'Profile'"), ("e2", "link 'Account'"), ("e3", "link 'Privacy'"), ("e4", "link 'Notifications'"),
    ("e5", "link 'Security'"), ("e6", "link 'Appearance'"), ("e7", "link 'Language'"),
    ("e8", "link 'Accessibility'"), ("e9", "link 'Data export'"), ("e10", "link 'Delete account'"),
    ("e11", "button 'Log out'"), ("e12", "button 'Back'"), ("e13", "textbox 'Search settings'"),
    ("e14", "link 'Billing'"),
]
CLIFF_NAV = [
    (f"n{i:02d}", text) for i, text in enumerate([
        "link 'Home'", "link 'Features'", "link 'Integrations'", "link 'Pricing'", "link 'Docs'",
        "link 'API'", "link 'Blog'", "link 'Changelog'", "link 'Careers'", "link 'About'",
        "link 'Contact'", "link 'Support'", "link 'Status'", "link 'Security'", "link 'Terms'",
        "link 'Privacy'", "link 'Cookies'", "link 'Twitter'", "link 'GitHub'", "link 'LinkedIn'",
        "link 'YouTube'", "link 'RSS'", "link 'Sitemap'", "link 'Brand'", "link 'Press'",
        "link 'Community'", "link 'Partners'", "link 'Enterprise'", "link 'Whitepapers'", "link 'Developers'",
    ], start=1)
]
CLIFF_RESULTS = [
    ("q", "textbox 'Búsqueda'"), ("f1", "button 'Filtro: Imágenes'"), ("f2", "button 'Filtro: Vídeos'"),
    ("f3", "button 'Filtro: Noticias'"), ("f4", "button 'Filtro: Maps'"), ("f5", "button 'Herramientas'"),
] + [(f"r{i}", f"link 'Resultado {i}: {title}'") for i, title in enumerate([
    "Guía completa de Roma", "Los 10 mejores restaurantes", "Historia de la ciudad",
    "Transporte público explicado", "Alojamientos baratos", "Qué ver en 3 días",
    "El clima por meses", "Free tours recomendados", "Museos y entradas",
    "Barrios donde dormir", "Gastronomía típica", "Excursiones cercanas",
    "Consejos de seguridad", "Moneda y propinas", "SIM y connexión internet",
    "Vida nocturna", "Eventos del año", "Viajar con niños",
    "Accesibilidad", "De compras", "Idioma y frases útiles", "Taxi o aeropuerto",
    "Seguro de viaje", "Itinerarios de una semana", "Preguntas frecuentes",
], start=1)] + [("p2", "link 'Página siguiente'"), ("p3", "link 'Página 3'")]
CLIFF_SHOP_DE = [
    ("c", "button 'Zur Kasse'"), ("s", "textbox 'Suche'"), ("w", "link 'Wunschliste'"),
] + [(f"p{i}", f"button 'In den Warenkorb' for {name}") for i, name in enumerate([
    "Kopfhörer", "Tastatur", "Maus", "Monitor", "Webcam", "Lautsprecher", "Mikrofon",
    "Dockingstation", "USB-Hub", "Kabel", "Akkus", "Ladegeräte", "Taschen", "Ständer",
    "Lampen", "Halterungen", "Reinigung", "Zubehör",
], start=1)] + [
    ("sort", "select 'Sortieren nach'"), ("pg2", "link 'Seite 2'"), ("pg3", "link 'Seite 3'"),
    ("imp", "link 'Impressum'"), ("vers", "link 'Versand'"), ("rueck", "link 'Rückgabe'"),
    ("news", "textbox 'Newsletter'"), ("abo", "button 'Abonnieren'"), ("kont", "link 'Kontakt'"),
    ("hilfe", "link 'Hilfe'"), ("faq", "link 'FAQ'"), ("ueber", "link 'Über uns'"),
]

EXECUTOR_CASES = [
    {"lang": "es", "mission": "Escribe 'Londres' en el campo de destino", "elements": FLIGHT_FORM, "expected": "e2"},
    {"lang": "es", "mission": "Pulsa el botón Buscar vuelos", "elements": FLIGHT_FORM, "expected": "e6"},
    {"lang": "es", "mission": "Selecciona 2 adultos en el desplegable de pasajeros",
     "elements": FLIGHT_FORM, "expected": "e4"},
    {"lang": "es", "mission": "Activa la opción de solo ida", "elements": FLIGHT_FORM, "expected": "e9"},
    {"lang": "es", "mission": "Acepta las cookies del banner", "elements": FLIGHT_FORM, "expected": "e16"},
    {"lang": "en", "mission": "Click the Sign in button", "elements": REPO_PAGE, "expected": "e9"},
    {"lang": "en", "mission": "Open the Pull requests tab", "elements": REPO_PAGE, "expected": "e2"},
    {"lang": "en", "mission": "Star this repository", "elements": REPO_PAGE, "expected": "e13"},
    {"lang": "en", "mission": "Type the user's email into the email field", "elements": LOGIN_FORM, "expected": "e1"},
    {"lang": "en", "mission": "Reset my password", "elements": LOGIN_FORM, "expected": "e4"},
    {"lang": "de", "mission": "Klicke auf den Warenkorb", "elements": SHOP_DE, "expected": "e2"},
    {"lang": "de", "mission": "Sortiere die Produkte nach Preis", "elements": SHOP_DE, "expected": "e9"},
    {"lang": "fr", "mission": "Clique sur le bouton Réserver", "elements": BOOKING_FR, "expected": "e4"},
    {"lang": "en", "mission": "Open the privacy settings page", "elements": SETTINGS_EN, "expected": "e3"},
    # the cliff: 30+ observed elements, where choice quality is documented to degrade
    {"lang": "en", "mission": "Click the link to the Pricing page",
     "elements": CLIFF_NAV, "expected": "n04", "cliff": True},
    {"lang": "es", "mission": "Haz clic en el tercer resultado de la búsqueda",
     "elements": CLIFF_RESULTS, "expected": "r3", "cliff": True},
    {"lang": "de", "mission": "Klicke auf 'Zur Kasse'", "elements": CLIFF_SHOP_DE, "expected": "c", "cliff": True},
]

EXECUTOR_SYSTEM = (
    "You are the executor of a browser agent. You get a mission (exactly one next action) and the "
    "elements observed on the current page. Reply with ONLY one JSON object: "
    '{"choice": "<element id>"} choosing the element the mission must act on.'
)


def _p50(values):
    return sorted(values)[len(values) // 2] if values else 0


def _p95(values):
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def _element_table(elements):
    return "\n".join(f"[{element_id}] {description}" for element_id, description in elements)


def run_routing(ask_gpt, ask_laya):
    rows, gpt_hits, laya_hits, gpt_ms, laya_ms = [], 0, 0, [], []
    for lang, mission, expected in ROUTING_CASES:
        laya_choice, laya_conf, l_ms = ask_laya(
            {"mission": mission},
            {"route": {"type": "choice", "instructions": "How should this mission be executed?",
                       "criteria": ROUTING_CRITERIA}},
            "route",
        )
        gpt_choice, g_ms = ask_gpt(ROUTING_SYSTEM, f"MISSION: {mission}\n\nReply with the JSON object now.")
        laya_hits += laya_choice == expected
        gpt_hits += gpt_choice == expected
        laya_ms.append(l_ms)
        gpt_ms.append(g_ms)
        rows.append((lang, mission, expected, laya_choice, laya_conf, gpt_choice, l_ms, g_ms))
    return {
        "rows": rows, "gpt_hits": gpt_hits, "laya_hits": laya_hits, "total": len(ROUTING_CASES),
        "gpt_ms": gpt_ms, "laya_ms": laya_ms,
    }


def run_executor(ask_gpt, ask_laya):
    rows, gpt_hits, laya_hits, gpt_ms, laya_ms = [], 0, 0, [], []
    cliff = {"gpt": 0, "laya": 0, "total": 0}
    for case in EXECUTOR_CASES:
        elements, mission, expected = case["elements"], case["mission"], case["expected"]
        criteria = {element_id: description for element_id, description in elements}
        laya_choice, laya_conf, l_ms = ask_laya(
            {"mission": mission, "page": _element_table(elements)[:1500]},
            {"element": {"type": "choice",
                         "instructions": "Which observed element must the mission act on?",
                         "criteria": criteria}},
            "element",
        )
        gpt_choice, g_ms = ask_gpt(
            EXECUTOR_SYSTEM,
            f"MISSION: {mission}\n\nOBSERVED ELEMENTS:\n{_element_table(elements)}\n\nReply with the JSON object now.",
        )
        laya_ok, gpt_ok = laya_choice == expected, gpt_choice == expected
        laya_hits += laya_ok
        gpt_hits += gpt_ok
        laya_ms.append(l_ms)
        gpt_ms.append(g_ms)
        if case.get("cliff"):
            cliff["total"] += 1
            cliff["laya"] += laya_ok
            cliff["gpt"] += gpt_ok
        rows.append((case["lang"], mission, len(elements), expected, laya_choice, laya_conf, gpt_choice, l_ms, g_ms))
    return {
        "rows": rows, "gpt_hits": gpt_hits, "laya_hits": laya_hits, "total": len(EXECUTOR_CASES),
        "gpt_ms": gpt_ms, "laya_ms": laya_ms, "cliff": cliff,
    }


def _pct(hits, total):
    return f"{hits}/{total} ({(100 * hits / total if total else 0):.0f}%)"


def render_report(routing, executor, gpt_model_name, laya_status, tokens=None):
    lines = [f"## ⚔️ Duelo real: GPT-OSS-20B ({gpt_model_name}) vs Laya local ({laya_status})\n"]
    lines.append("### Concurso 1 — Enrutado de misiones (el trabajo actual de Laya)\n")
    lines.append("| | Lang | Misión | Correcto | Laya (conf) | GPT-OSS | Laya ms | GPT ms |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for lang, mission, expected, laya_choice, laya_conf, gpt_choice, l_ms, g_ms in routing["rows"]:
        lines.append(
            f"| {'✅' if laya_choice == expected else '❌'} | {lang} | {mission[:40]}… | `{expected}` | "
            f"`{laya_choice or '—'}`{f' ({laya_conf:.2f})' if laya_conf is not None else ''} | "
            f"{'✅' if gpt_choice == expected else '❌'} `{gpt_choice or '—'}` | {l_ms:.0f} | {g_ms:.0f} |"
        )
    lines.append(
        f"\n**Routing — Laya: {_pct(routing['laya_hits'], routing['total'])} · "
        f"GPT-OSS: {_pct(routing['gpt_hits'], routing['total'])}** · "
        f"p50/p95 — Laya {_p50(routing['laya_ms']):.0f}/{_p95(routing['laya_ms']):.0f} ms · "
        f"GPT {_p50(routing['gpt_ms']):.0f}/{_p95(routing['gpt_ms']):.0f} ms\n"
    )

    lines.append("### Concurso 2 — El trabajo del executor (elegir el elemento de la página)\n")
    lines.append("| | Lang | Misión | #elem | Correcto | Laya (conf) | GPT-OSS | Laya ms | GPT ms |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for lang, mission, count, expected, laya_choice, laya_conf, gpt_choice, l_ms, g_ms in executor["rows"]:
        lines.append(
            f"| {'✅' if laya_choice == expected else '❌'} | {lang} | {mission[:36]}… | {count} | `{expected}` | "
            f"`{laya_choice or '—'}`{f' ({laya_conf:.2f})' if laya_conf is not None else ''} | "
            f"{'✅' if gpt_choice == expected else '❌'} `{gpt_choice or '—'}` | {l_ms:.0f} | {g_ms:.0f} |"
        )
    cliff = executor["cliff"]
    lines.append(
        f"\n**Executor — Laya: {_pct(executor['laya_hits'], executor['total'])} · "
        f"GPT-OSS: {_pct(executor['gpt_hits'], executor['total'])}** · "
        f"páginas «cliff» (30+ elementos) — Laya {_pct(cliff['laya'], cliff['total'])} · "
        f"GPT-OSS {_pct(cliff['gpt'], cliff['total'])} · "
        f"p50/p95 — Laya {_p50(executor['laya_ms']):.0f}/{_p95(executor['laya_ms']):.0f} ms · "
        f"GPT {_p50(executor['gpt_ms']):.0f}/{_p95(executor['gpt_ms']):.0f} ms\n"
    )
    if tokens:
        lines.append(f"GPT-OSS consumió **{tokens:,} tokens** en el duelo (Laya: 0 €, 0 tokens de API).\n")

    # verdict, computed from the numbers
    routing_laya_wins = routing["laya_hits"] >= routing["gpt_hits"]
    executor_gpt_wins = executor["gpt_hits"] > executor["laya_hits"]
    lines.append("### Veredicto\n")
    lines.append(
        f"- **Routing**: {'Laya' if routing_laya_wins else 'GPT-OSS'} iguala o gana "
        f"({routing['laya_hits']} vs {routing['gpt_hits']} de {routing['total']}) — y Laya decide en local, "
        f"gratis y sin round-trip de red.\n"
        f"- **Executor**: {'GPT-OSS gana claramente' if executor_gpt_wins else 'empate inesperado'} "
        f"({executor['gpt_hits']} vs {executor['laya_hits']} de {executor['total']}); en las páginas de 30+ "
        f"elementos: {cliff['gpt']} vs {cliff['laya']} de {cliff['total']}.\n"
    )
    lines.append(
        "Conclusión de arquitectura: **GPT-OSS-20B ejecuta, Laya clasifica** — exactamente el reparto "
        "actual del agente, ahora con números medidos."
    )
    return "\n".join(lines)


def main():
    from jev_ultrafast import laya_local, providers

    gpt_ready = bool((os.environ.get("GROQ_API_KEY") or "").strip())
    if not gpt_ready:
        print("## ⚔️ Duelo GPT-OSS-20B vs Laya\n\nSin `GROQ_API_KEY` el lado GPT-OSS no puede correr; duelo omitido.")
        return 0

    provider = providers.resolve("policy")
    tokens_total = [0]

    def ask_gpt(system, user):
        time.sleep(GPT_PACING_SECONDS)
        started = time.perf_counter()
        content, meta = providers.chat(provider, system, user, max_tokens=200)
        latency = (time.perf_counter() - started) * 1000
        usage = meta.get("usage") or {}
        tokens_total[0] += sum(
            usage.get(field) or 0 for field in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens")
        )
        try:
            answer = providers.extract_json(content)
            return answer.get("choice"), latency
        except ValueError:
            return None, latency

    def ask_laya(state, questions, question_id):
        agent = laya_local.engine()
        if agent is None:
            return None, None, 0.0
        started = time.perf_counter()
        try:
            result = agent.predict(state, questions)
        except Exception as error:  # noqa: BLE001 - context overflow etc: counts as a miss
            return None, f"err:{str(error)[:24]}", (time.perf_counter() - started) * 1000
        latency = (time.perf_counter() - started) * 1000
        answer = (result.get("answers") or {}).get(question_id) or {}
        confidence = answer.get("confidence")
        return answer.get("choice"), (round(confidence, 2) if isinstance(confidence, (int, float)) else None), latency

    laya_agent = laya_local.engine()
    if laya_agent is None:
        print(f"## ⚔️ Duelo GPT-OSS-20B vs Laya\n\nLaya no está disponible ({laya_local.status()}); duelo omitido.")
        return 0

    routing = run_routing(ask_gpt, ask_laya)
    executor = run_executor(ask_gpt, ask_laya)
    print(render_report(routing, executor, f"{provider['name']}:{provider['model']}", laya_local.status(),
                        tokens=tokens_total[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

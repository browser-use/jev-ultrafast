"""The duel batteries must be well-formed before they run on CI with real engines."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "duel_gptoss_vs_laya", Path(__file__).parent.parent / "scripts" / "duel_gptoss_vs_laya.py"
)
duel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(duel)


def test_routing_battery_covers_languages_and_valid_answers():
    assert len(duel.ROUTING_CASES) >= 20
    languages = {lang for lang, _mission, _expected in duel.ROUTING_CASES}
    assert {"es", "en", "de", "fr"} <= languages
    for _lang, _mission, expected in duel.ROUTING_CASES:
        assert expected in {"browser", "orchestrated"}


def test_executor_battery_is_realistic_and_has_the_cliff():
    standard = [case for case in duel.EXECUTOR_CASES if not case.get("cliff")]
    cliff = [case for case in duel.EXECUTOR_CASES if case.get("cliff")]
    assert len(standard) >= 12
    assert len(cliff) >= 3
    for case in duel.EXECUTOR_CASES:
        ids = [element_id for element_id, _description in case["elements"]]
        assert len(ids) == len(set(ids)), "duplicate element ids"
        assert case["expected"] in ids, f"expected {case['expected']} not among the options"
        if case.get("cliff"):
            assert len(ids) >= 25, "cliff pages need 25+ elements"
        else:
            assert 10 <= len(ids) <= 20, "standard pages hold 10-20 elements (Laya's documented comfort zone)"
        assert case["mission"].strip()


def test_report_renders_from_fake_results():
    def fake_gpt_route(system, user):
        browser_words = ("flights", "vuelos", "volo", "voos", "klicke", "clique", "cambia",
                         "click", "fill", "fülle", "remplis", "reserva", "credentials")
        return ("browser" if any(word in user.lower() for word in browser_words) else "orchestrated", 300.0)

    routing = duel.run_routing(
        ask_gpt=fake_gpt_route,
        ask_laya=lambda state, questions, qid: ("browser", 0.8, 25.0) if "login" in state["mission"].lower()
        else ("orchestrated", 0.8, 25.0),
    )
    def fake_gpt_execute(system, user):
        for marker, choice in (("Sign in", "e9"), ("Pull requests", "e2"), ("Star", "e13"),
                               ("email field", "e1"), ("password", "e4"), ("Pricing", "n04"),
                               ("tercer", "r3"), ("Zur Kasse", "c"), ("Réserver", "e4"),
                               ("Warenkorb", "e2"), ("Sortiere", "e9"), ("privacy", "e3"),
                               ("solo ida", "e9"), ("cookies", "e16"), ("Destino", "e2"),
                               ("Buscar vuelos", "e6"), ("pasajeros", "e4")):
            if marker in user:
                return (choice, 300.0)
        return ("e1", 300.0)

    executor = duel.run_executor(
        ask_gpt=fake_gpt_execute,
        ask_laya=lambda state, questions, qid: (list(questions[qid]["criteria"])[0], 0.7, 120.0),
    )
    report = duel.render_report(routing, executor, "groq:openai/gpt-oss-20b", "ready (multilingual)", tokens=12345)
    assert "Concurso 1" in report and "Concurso 2" in report and "Veredicto" in report
    assert "groq:openai/gpt-oss-20b" in report
    assert "12,345 tokens" in report
    assert "GPT-OSS-20B ejecuta, Laya clasifica" in report
    # honest numbers, not hardcoded
    assert f"{executor['gpt_hits']}/{executor['total']}" in report

import os
import json
from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json

load_environment()
typesafe_key = os.environ.get("TYPESAFE_API_KEY")

body = {
    "model": "jev-latest",
    "state": {
        "page": {
            "url": "https://www.chess.com/play/computer",
            "title": "Play Chess Online vs the Computer - Chess.com",
            "text": "Current position: White to move after 1. e4 e5."
        },
        "elements": [
            {"index": "1", "label": "Play Qh5 (Scholar's Mate threat on f7 and e5)", "operations": ["CLICK"]},
            {"index": "2", "label": "Play Bc4 (Scholar's Mate setup targeting f7)", "operations": ["CLICK"]},
            {"index": "3", "label": "Play Nf3 (Develop Knight and attack e5)", "operations": ["CLICK"]}
        ],
        "recent_actions": [
            {"action": "Move e2 to e4", "kind": "click", "text": None, "page_changed": True}
        ]
    },
    "questions": {
        "operation": {
            "type": "choice",
            "criteria": {"CLICK": "Make a chess move"},
            "instructions": {
                "goal": "Win the chess game against the bot by playing the most aggressive and winning move.",
                "rules": "Pick CLICK to make the best move."
            }
        },
        "click_target": {
            "type": "choice",
            "criteria": {
                "1": {"element": "[1] Play Qh5 (Scholar's Mate threat on f7 and e5)"},
                "2": {"element": "[2] Play Bc4 (Scholar's Mate setup targeting f7)"},
                "3": {"element": "[3] Play Nf3 (Develop Knight and attack e5)"}
            },
            "instructions": {
                "goal": "Win the chess game against the bot by playing the most aggressive and winning move.",
                "operation": "CLICK",
                "rules": ["Pick the target index that delivers the strongest attack to win."]
            }
        }
    }
}

resp = post_json("https://api.typesafe.ai/v1/systemone", typesafe_key, body)
print(json.dumps(resp, indent=2))

"""
Standalone Laya Decision Server.
Can be run on Google Colab, GPU cloud instances, or local server.
Exposes a sub-20ms HTTP API matching the Jev-Ultrafast action decision schema.

Usage:
    python scripts/serve_laya.py --port 8000
    # Or in Google Colab:
    python scripts/serve_laya.py --port 8000 --tunnel ngrok
"""

import argparse
import json
import logging
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("laya-server")

_ROUTER = None


def get_router():
    global _ROUTER
    if _ROUTER is None:
        logger.info("Initializing Laya Router (ModernBERT)...")
        from laya import Router
        _ROUTER = Router()
        logger.info("Laya Router ready for inference!")
    return _ROUTER


class LayaRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: Dict[str, Any]):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/health", "/v1/health"):
            self._send_json(200, {"status": "ok", "service": "laya-decision-engine", "uptime": time.time()})
        elif self.path in ("/info", "/v1/info"):
            self._send_json(200, {
                "engine": "laya-opensource",
                "model": "convaiinnovations/laya",
                "architecture": "ModernBERT-large non-autoregressive",
                "author": "NandhaKishorM",
                "capabilities": ["choice", "multilingual", "sub-40ms"],
            })
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        if self.path not in ("/predict", "/v1/predict", "/v1/systemone"):
            self._send_json(404, {"error": f"Unknown endpoint {self.path}"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "Empty body"})
            return

        try:
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return

        router = get_router()
        context = body.get("context", "")
        questions = body.get("questions", {})

        # If sent in TypeSafe schema format:
        if not context and "state" in body:
            state = body["state"].get("page", {})
            context = f"Page URL: {state.get('url', '')}\nPage Title: {state.get('title', '')}\nContent: {state.get('text', '')[:2000]}"

        start_time = time.perf_counter()
        try:
            prediction = router.predict(context, questions)
            latency_ms = (time.perf_counter() - start_time) * 1000
            prediction["latency_ms"] = round(latency_ms, 2)
            prediction["model"] = "laya-multilingual"
            self._send_json(200, prediction)
        except Exception as err:
            logger.error(f"Inference error: {err}", exc_info=True)
            self._send_json(500, {"error": str(err)})

    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - {format % args}")


def main():
    parser = argparse.ArgumentParser(description="Serve Laya Decision Engine over HTTP")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default 8000)")
    parser.add_argument("--tunnel", choices=["none", "ngrok"], default="none", help="Expose via ngrok tunnel")
    args = parser.parse_args()

    # Pre-warm router
    get_router()

    if args.tunnel == "ngrok":
        try:
            from pyngrok import ngrok
            public_url = ngrok.connect(args.port).public_url
            logger.info("=" * 60)
            logger.info(f"PUBLIC LAYA ENDPOINT: {public_url}")
            logger.info(f"Set this in your local jev-ultrafast .env:")
            logger.info(f"export LAYA_ENDPOINT={public_url}")
            logger.info("=" * 60)
        except ImportError:
            logger.warning("pyngrok not installed. Install with 'pip install pyngrok' to use --tunnel ngrok")

    server = HTTPServer((args.host, args.port), LayaRequestHandler)
    logger.info(f"Serving Laya Decision API on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.server_close()


if __name__ == "__main__":
    main()

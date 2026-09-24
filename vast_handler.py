"""Vast serverless adapter for the existing ClipWeave ComfyUI render handler.

The inherited /start.sh starts ComfyUI and executes /handler.py. This module
serves a local model API and starts Vast's PyWorker proxy in the same process.
RunPod's /handler.py remains unchanged in its own image.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

from vastai import BenchmarkConfig, HandlerConfig, LogActionConfig, Worker, WorkerConfig

from clipweave_runpod_handler import handler as render_handler


PORT = 18289
LOG_FILE = Path("/tmp/clipweave-vast-model.log")
MAX_BODY_BYTES = 32 * 1024 * 1024


class LocalModelHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._respond(200, {"ready": True})

    def do_POST(self):
        if self.path != "/generate/sync":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            self._respond(413, {"error": "Invalid request size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or not isinstance(payload.get("input"), dict):
                raise ValueError("Expected an input object")
            if payload["input"].get("benchmark") is True:
                # A fixed, cheap benchmark avoids rendering a customer video
                # or requiring customer storage credentials during warm-up.
                time.sleep(2)
                result = {"benchmark": True}
            else:
                result = render_handler({"input": payload["input"]})
            self._respond(200, result)
        except (ValueError, json.JSONDecodeError) as error:
            self._respond(400, {"error": str(error)})
        except Exception as error:
            self._respond(500, {"error": str(error)})

    def _respond(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def wait_for_comfyui():
    for _ in range(180):
        try:
            with urlopen("http://127.0.0.1:8188/system_stats", timeout=2) as response:
                if response.status == 200:
                    LOG_FILE.write_text("CLIPWEAVE_READY\n", encoding="utf-8")
                    return
        except (OSError, URLError):
            pass
        time.sleep(5)
    LOG_FILE.write_text("CLIPWEAVE_STARTUP_FAILED\n", encoding="utf-8")


if __name__ == "__main__":
    LOG_FILE.touch()
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", PORT), LocalModelHandler).serve_forever, daemon=True).start()
    threading.Thread(target=wait_for_comfyui, daemon=True).start()
    Worker(WorkerConfig(
        model_server_url="http://127.0.0.1",
        model_server_port=PORT,
        model_log_file=str(LOG_FILE),
        model_healthcheck_url="/health",
        handlers=[HandlerConfig(
            route="/generate/sync",
            allow_parallel_requests=False,
            max_queue_time=30.0,
            benchmark_config=BenchmarkConfig(generator=lambda: {"input": {"benchmark": True}}),
        )],
        log_action_config=LogActionConfig(
            on_load=["CLIPWEAVE_READY"],
            on_error=["CLIPWEAVE_STARTUP_FAILED"],
            on_info=[],
        ),
    )).run()

"""Vast serverless adapter for the Qwen VoiceDesign worker.

Serves the same request contract as the RunPod handler (`handler.py`) from a
local model API, and runs Vast's PyWorker proxy in front of it in the same
process. RunPod keeps using `handler.py` directly.
"""

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from vastai import BenchmarkConfig, HandlerConfig, LogActionConfig, Worker, WorkerConfig

from handler import handler as voice_handler, model


PORT = 18289
LOG_FILE = Path("/tmp/clipweave-qwen-vast-model.log")
MAX_BODY_BYTES = 1024 * 1024
# One GPU generates one voice at a time.
_GENERATE_LOCK = threading.Lock()


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
                # Warm-up needs no real generation; the model is already loaded.
                time.sleep(1)
                result = {"benchmark": True}
            else:
                with _GENERATE_LOCK:
                    result = voice_handler({"id": f"vast-voice-{uuid.uuid4()}", "input": payload["input"]})
            # Validation errors come back as {"error": ...}, as on RunPod.
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


def load_model():
    # Downloads the weights on a fresh worker, then keeps them on the GPU.
    try:
        model()
        LOG_FILE.write_text("CLIPWEAVE_READY\n", encoding="utf-8")
    except Exception as error:
        print(f"[ClipWeave] Qwen model failed to load: {error}", flush=True)
        LOG_FILE.write_text("CLIPWEAVE_STARTUP_FAILED\n", encoding="utf-8")


if __name__ == "__main__":
    LOG_FILE.touch()
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", PORT), LocalModelHandler).serve_forever, daemon=True).start()
    threading.Thread(target=load_model, daemon=True).start()
    Worker(WorkerConfig(
        model_server_url="http://127.0.0.1",
        model_server_port=PORT,
        model_log_file=str(LOG_FILE),
        model_healthcheck_url="/health",
        handlers=[
            HandlerConfig(
                route="/generate/sync",
                allow_parallel_requests=False,
                max_queue_time=60.0,
                benchmark_config=BenchmarkConfig(generator=lambda: {"input": {"benchmark": True}}),
            ),
        ],
        log_action_config=LogActionConfig(
            on_load=["CLIPWEAVE_READY"],
            on_error=["CLIPWEAVE_STARTUP_FAILED"],
            on_info=[],
        ),
    )).run()

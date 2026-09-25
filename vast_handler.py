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
import uuid
from urllib.error import URLError
from urllib.request import urlopen

import requests

from vastai import BenchmarkConfig, HandlerConfig, LogActionConfig, Worker, WorkerConfig

from clipweave_runpod_handler import handler as render_handler


PORT = 18289
LOG_FILE = Path("/tmp/clipweave-vast-model.log")
MAX_BODY_BYTES = 32 * 1024 * 1024
_ACTIVE_JOBS = set()
_ACTIVE_LOCK = threading.Lock()


def _finish_session(session):
    if not isinstance(session, dict):
        return
    session_id = session.get("id")
    auth_data = session.get("auth_data")
    if not session_id or not isinstance(auth_data, dict):
        return
    port = int(os.environ.get("WORKER_HTTP_PORT", "3001"))
    try:
        requests.post(
            f"http://127.0.0.1:{port}/session/end",
            json={"session_id": session_id, "session_auth": auth_data},
            timeout=10,
        ).raise_for_status()
    except requests.RequestException as error:
        print(f"[ClipWeave] Could not close Vast session: {error}", flush=True)


def _report_failure(delivery, message):
    if not isinstance(delivery, dict) or not str(delivery.get("failure_url", "")).startswith("https://"):
        return
    try:
        requests.post(delivery["failure_url"], json={
            "jobId": delivery["job_id"],
            "path": delivery["storage_path"],
            "expiresAt": delivery["expires_at"],
            "token": delivery["token"],
            "error": str(message)[:1000],
        }, timeout=30).raise_for_status()
    except requests.RequestException as error:
        print(f"[ClipWeave] Could not report Vast render failure: {error}", flush=True)


def _render_in_background(job_input, session):
    delivery = job_input.get("delivery")
    job_id = delivery.get("job_id")
    try:
        # RunPod's base handler reads job["id"], which RunPod always supplies.
        result = render_handler({"id": job_id, "input": job_input})
        if not isinstance(result, dict) or result.get("error") or not result.get("delivered"):
            # A completed render may have missed its delivery callback. The
            # shared handler can retry delivery from the chain's local cache.
            if isinstance(result, dict) and not result.get("error"):
                result = render_handler({"id": job_id, "input": {
                    "fetch": {"cache_namespace": job_input.get("cache_namespace")},
                    "delivery": delivery,
                }})
            if not isinstance(result, dict) or result.get("error") or not result.get("delivered"):
                raise RuntimeError(str(result.get("error") if isinstance(result, dict) else "Render did not deliver a video"))
    except Exception as error:
        print(f"[ClipWeave] Vast render failed for {job_id}: {error}", flush=True)
        _report_failure(delivery, error)
    finally:
        _finish_session(session)
        with _ACTIVE_LOCK:
            _ACTIVE_JOBS.discard(job_id)


class LocalModelHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._respond(200, {"ready": True})

    def do_POST(self):
        if self.path not in ("/generate/sync", "/generate/async"):
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
            if self.path == "/generate/async":
                delivery = payload["input"].get("delivery")
                session = payload.get("session")
                if not isinstance(delivery, dict) or not delivery.get("job_id") or not isinstance(session, dict) or not session.get("id"):
                    raise ValueError("A delivery target and Vast session are required")
                job_id = delivery["job_id"]
                with _ACTIVE_LOCK:
                    if job_id in _ACTIVE_JOBS:
                        self._respond(200, {"accepted": True, "job_id": job_id})
                        return
                    _ACTIVE_JOBS.add(job_id)
                threading.Thread(target=_render_in_background, args=(payload["input"], session), daemon=True).start()
                self._respond(202, {"accepted": True, "job_id": job_id})
                return
            if payload["input"].get("benchmark") is True:
                # A fixed, cheap benchmark avoids rendering a customer video
                # or requiring customer storage credentials during warm-up.
                time.sleep(2)
                result = {"benchmark": True}
            else:
                result = render_handler({"id": f"vast-sync-{uuid.uuid4()}", "input": payload["input"]})
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
        handlers=[
            HandlerConfig(
                route="/generate/sync",
                allow_parallel_requests=False,
                max_queue_time=30.0,
                benchmark_config=BenchmarkConfig(generator=lambda: {"input": {"benchmark": True}}),
            ),
            HandlerConfig(route="/generate/async", allow_parallel_requests=False, max_queue_time=30.0),
        ],
        log_action_config=LogActionConfig(
            on_load=["CLIPWEAVE_READY"],
            on_error=["CLIPWEAVE_STARTUP_FAILED"],
            on_info=[],
        ),
    )).run()

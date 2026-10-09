import http.client
import ast
import json
import sys
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipweave_direct import make_handler


class DirectAuthTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def submit(payload):
            self.calls.append(payload)
            return 202, {"accepted": True, "job_id": "test-job"}
        self.secret = "test-only-secret-" * 3
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            self.secret, lambda: {"ready": True, "busy": False}, submit))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, token=None, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        values = headers or {}
        if token is not None:
            values["Authorization"] = "Bearer " + token
        connection.request(method, path, body, values)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_missing_or_wrong_secret_cannot_submit_or_inspect(self):
        for token in [None, "wrong"]:
            self.assertEqual(self.request("GET", "/clipweave/health", token)[0], 401)
            self.assertEqual(self.request("POST", "/clipweave/generate", token, '{"input":{}}')[0], 401)
        self.assertEqual(self.calls, [])

    def test_authorized_submission_and_health(self):
        self.assertEqual(self.request("GET", "/clipweave/health", self.secret)[1]["ready"], True)
        self.assertEqual(self.request("POST", "/clipweave/generate", self.secret, '{"input":{"test":true}}')[0], 202)
        self.assertEqual(self.calls, [{"input": {"test": True}}])

    def test_malformed_and_oversized_requests_do_not_execute(self):
        self.assertEqual(self.request("POST", "/clipweave/generate", self.secret, "broken")[0], 400)
        self.assertEqual(self.request("POST", "/clipweave/generate", self.secret, "{}", {"Content-Length": str(33*1024*1024)})[0], 413)
        self.assertEqual(self.calls, [])

    def test_short_secret_fails_closed(self):
        with self.assertRaises(ValueError):
            make_handler("short", lambda: {}, lambda payload: {})

    def test_duplicate_delivery_and_busy_worker_do_not_render_twice(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "vast_handler.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_direct_submit")
        scope = {"_validate_render_profile": lambda value: None, "_ACTIVE_LOCK": threading.Lock(),
                 "_ACTIVE_JOBS": set(), "_DIRECT_JOBS": {"already-accepted"}}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "vast_handler.py", "exec"), scope)
        self.assertEqual(scope["_direct_submit"]({"input": {"delivery": {"job_id": "already-accepted"}}})[0], 200)
        scope["_ACTIVE_JOBS"].add("other-job")
        self.assertEqual(scope["_direct_submit"]({"input": {"delivery": {"job_id": "new-job"}}})[0], 409)
        self.assertEqual(scope["_ACTIVE_JOBS"], {"other-job"})

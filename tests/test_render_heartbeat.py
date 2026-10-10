import ast
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest

class HeartbeatTests(unittest.TestCase):
    def test_idle_stage_sends_heartbeat_without_repeating_progress(self):
        source = Path(__file__).resolve().parents[1] / "runpod_handler.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ProgressReporter")
        calls = []
        class Event:
            def wait(self, timeout=None):
                self.timeout = timeout
            def clear(self): pass
            def set(self): pass
        def post(url, json, timeout):
            calls.append(json)
            if len(calls) == 2: reporter.closed = True
            return SimpleNamespace(raise_for_status=lambda: None)
        scope = {"threading": threading, "json": json, "requests": SimpleNamespace(post=post, RequestException=Exception)}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), "exec"), scope)
        reporter = scope["_ProgressReporter"]({}, {})
        reporter.url = "https://example.test/progress"
        reporter.state = {"stage": "finishing"}
        reporter.wake = Event()
        reporter._send_loop()
        self.assertEqual([c["heartbeat"] for c in calls], [False, True])
        self.assertEqual([c["stage"] for c in calls], ["finishing", "finishing"])
        self.assertEqual(reporter.wake.timeout, 30)

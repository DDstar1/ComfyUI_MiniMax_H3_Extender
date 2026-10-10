import contextlib
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeBar:
    def __init__(self, *args, **kwargs):
        self.n = kwargs.get("initial", 0)
        self.total = kwargs.get("total")
    def update(self, amount):
        self.n += amount


class ModelProgressTests(unittest.TestCase):
    def test_download_reports_actual_bytes_and_completion(self):
        def fake_download(**kwargs):
            bar = kwargs["tqdm_class"](total=100, initial=20)
            bar.update(10)
        modules = {"huggingface_hub": types.SimpleNamespace(hf_hub_download=fake_download),
                   "tqdm.auto": types.SimpleNamespace(tqdm=FakeBar)}
        spec = importlib.util.spec_from_file_location("provision", Path(__file__).parents[1] / "vast_provision_models.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.dict("os.environ", {"H3_RENDER_PROFILE": "standard"}):
            spec.loader.exec_module(module)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.download(module.FILES[0])
        events = [json.loads(line.split("[ClipWeave startup] ")[1]) for line in output.getvalue().splitlines() if line.startswith("[ClipWeave startup]")]
        self.assertEqual(events[0]["model_total"], 5)
        self.assertNotIn("percent", events[0])
        self.assertEqual(events[1]["percent"], 30)
        self.assertEqual(events[-1]["percent"], 100)


if __name__ == "__main__":
    unittest.main()

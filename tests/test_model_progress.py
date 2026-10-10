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
    def run_download(self, legacy=False):
        context = types.SimpleNamespace(_get_progress_bar_context=lambda **kwargs: FakeBar(**kwargs))
        def modern_download(*, tqdm_class=None, **kwargs):
            bar = tqdm_class(total=100, initial=20)
            bar.update(10)
        def legacy_download(*, repo_id, filename, local_dir, token):
            bar = context._get_progress_bar_context(total=100, initial=20, log_level=20, name="download")
            bar.update(10)
        modules = {"huggingface_hub": types.SimpleNamespace(hf_hub_download=legacy_download if legacy else modern_download, file_download=context),
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
        self.assertIsNone(module.PROGRESS_CONTEXT.bar)

    def test_modern_hub_reports_actual_bytes(self):
        self.run_download()

    def test_legacy_hub_reports_actual_bytes_without_unsupported_keyword(self):
        self.run_download(legacy=True)


if __name__ == "__main__":
    unittest.main()

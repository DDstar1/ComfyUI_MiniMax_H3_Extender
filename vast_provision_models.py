"""Download only the MiniMax H3 weights used by ClipWeave to Vast's local disk.

All missing files download at once to shorten a fresh boot. Hugging Face has
rate-limited bursts before (HTTP 429 on the old RunPod setup), so any file that
fails is retried one at a time afterwards. `huggingface_hub` itself waits out a
429 using the server's RateLimit header. Set HF_TOKEN (a read-only token) as a
Vast account environment variable for account-level rather than per-IP limits.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys
import time
import json
import inspect
import threading
from tqdm.auto import tqdm

from huggingface_hub import hf_hub_download, file_download


PROGRESS_CONTEXT = threading.local()
# Older ComfyUI dependencies use Hub 0.x: its public download call has no
# tqdm_class argument, but both HTTP and Xet use this progress context.
_ORIGINAL_PROGRESS_CONTEXT = getattr(file_download, "_get_progress_bar_context", None)


def progress_context(**kwargs):
    cls = getattr(PROGRESS_CONTEXT, "bar", None)
    if cls is not None and kwargs.get("_tqdm_bar") is None:
        options = {key: value for key, value in kwargs.items()
                   if key not in ("log_level", "name", "_tqdm_bar", "tqdm_class")}
        return cls(**options)
    return _ORIGINAL_PROGRESS_CONTEXT(**kwargs)


if "tqdm_class" not in inspect.signature(hf_hub_download).parameters:
    if _ORIGINAL_PROGRESS_CONTEXT is None:
        raise RuntimeError("Installed Hugging Face Hub lacks a supported progress context")
    file_download._get_progress_bar_context = progress_context

ROOT = Path("/runpod-volume/runpod-slim/ComfyUI/models")
REPO = "Comfy-Org/MiniMax-H3"
COMMON_FILES = (
    "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "vae/minimax_h3_video_vae_fp16.safetensors",
    "vae/minimax_h3_audio_vae_fp32.safetensors",
    "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
)
PROFILE_MODELS = {
    "standard": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "enhanced": "diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors",
}
RENDER_PROFILE = os.getenv("H3_RENDER_PROFILE", "standard").strip().lower()
if RENDER_PROFILE not in PROFILE_MODELS:
    raise SystemExit("H3_RENDER_PROFILE must be 'standard' or 'enhanced'.")
# Enhanced workers can also serve Standard jobs without changing their model.
# Standard workers never download BF16 and cannot accept Enhanced generation.
FILES = ((*PROFILE_MODELS.values(), *COMMON_FILES) if RENDER_PROFILE == "enhanced"
         else (PROFILE_MODELS["standard"], *COMMON_FILES))
TOKEN = os.getenv("HF_TOKEN") or None


def report(stage, **fields):
    print("[ClipWeave startup] " + json.dumps({"stage": stage, **fields}), flush=True)


def download(filename):
    started = time.monotonic()
    print(f"[ClipWeave] Downloading model: {filename}", flush=True)
    index = FILES.index(filename) + 1
    report("downloading", model_index=index, model_total=len(FILES))

    class ModelProgress(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = False
            self.last_report = 0
            super().__init__(*args, **kwargs)

        def update(self, amount=1):
            result = super().update(amount)
            now = time.monotonic()
            if self.total and (now - self.last_report >= 10 or self.n >= self.total):
                report("downloading", model_index=index, model_total=len(FILES),
                       percent=round(min(100, 100 * self.n / self.total), 1))
                self.last_report = now
            return result

    PROGRESS_CONTEXT.bar = ModelProgress
    try:
        options = {"tqdm_class": ModelProgress} if "tqdm_class" in inspect.signature(hf_hub_download).parameters else {}
        hf_hub_download(repo_id=REPO, filename=filename, local_dir=ROOT, token=TOKEN, **options)
    finally:
        PROGRESS_CONTEXT.bar = None
    report("downloading", model_index=index, model_total=len(FILES), percent=100)
    print(f"[ClipWeave] Downloaded model in {time.monotonic() - started:.0f}s: {filename}", flush=True)


def main():
    print(f"[ClipWeave] Render profile: {RENDER_PROFILE} ({PROFILE_MODELS[RENDER_PROFILE]})", flush=True)
    missing = []
    for filename in FILES:
        target = ROOT / filename
        if target.is_file() and target.stat().st_size > 0:
            print(f"[ClipWeave] Model cached: {filename}", flush=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            missing.append(filename)
    if not missing:
        report("booting")
        return

    started = time.monotonic()
    print(f"[ClipWeave] Downloading {len(missing)} models at once "
          f"({'with' if TOKEN else 'without'} HF_TOKEN)", flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=len(missing)) as pool:
        futures = {pool.submit(download, filename): filename for filename in missing}
        for future, filename in futures.items():
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - every failure gets the sequential retry
                print(f"[ClipWeave] Parallel download failed, will retry one by one: {filename}: {error}", flush=True)
                failed.append(filename)

    # hf_hub_download resumes partial files, so the retry continues where the
    # parallel attempt stopped.
    for filename in failed:
        download(filename)
    report("booting")
    print(f"[ClipWeave] Models ready in {time.monotonic() - started:.0f}s "
          f"({len(failed)} retried one by one)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"[ClipWeave] Model download failed: {error}", file=sys.stderr, flush=True)
        raise

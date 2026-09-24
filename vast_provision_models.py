"""Download only the MiniMax H3 weights used by ClipWeave to Vast's local disk."""

from pathlib import Path
import os

from huggingface_hub import hf_hub_download


ROOT = Path("/runpod-volume/runpod-slim/ComfyUI/models")
FILES = (
    "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "vae/minimax_h3_video_vae_fp16.safetensors",
    "vae/minimax_h3_audio_vae_fp32.safetensors",
    "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
)


for filename in FILES:
    target = ROOT / filename
    if target.is_file() and target.stat().st_size > 0:
        print(f"[ClipWeave] Model cached: {filename}", flush=True)
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[ClipWeave] Downloading model: {filename}", flush=True)
    hf_hub_download(
        repo_id="Comfy-Org/MiniMax-H3",
        filename=filename,
        local_dir=ROOT,
        token=os.getenv("HF_TOKEN") or None,
    )

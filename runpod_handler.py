"""RunPod's official ComfyUI handler with isolated H3 caches and video output."""

import hashlib
import os
from pathlib import Path
import threading

import runpod
import runpod_base_handler as base


_VIDEO_KEYS = {"videos", "gifs", "h3_outputs"}
_VIDEO_EXTENSIONS = {".avi", ".gif", ".mkv", ".mov", ".mp4", ".webm"}
_CACHE_BASE_ROOT = Path(
    os.environ.get("H3_CACHE_ROOT", "/runpod-volume/comfytr-cache")
).expanduser().resolve()
_CACHE_ROOT_FILE = Path(
    os.environ.get("H3_CACHE_ROOT_FILE", "/tmp/comfytr-h3-cache-root")
).expanduser().resolve()
_JOB_LOCK = threading.Lock()
_original_get_history = base.get_history


def _history_with_video_outputs(prompt_id):
    """Expose ComfyUI video UI entries to the base handler's file collector."""
    history = _original_get_history(prompt_id)
    prompt_history = history.get(prompt_id, {})
    for node_output in prompt_history.get("outputs", {}).values():
        media = list(node_output.get("images", []))
        for key in _VIDEO_KEYS:
            values = node_output.get(key, [])
            if isinstance(values, list):
                media.extend(item for item in values if isinstance(item, dict))
        if media:
            node_output["images"] = media
    return history


base.get_history = _history_with_video_outputs


def _select_project_cache(job):
    """Atomically select a stable, non-identifying cache directory for this job."""
    job_input = job.get("input") if isinstance(job, dict) else None
    namespace = job_input.get("cache_namespace") if isinstance(job_input, dict) else None
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("Missing 'cache_namespace'; derive it server-side from user and project IDs")
    namespace = namespace.strip()
    if len(namespace) > 512:
        raise ValueError("'cache_namespace' must be 512 characters or fewer")

    digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()
    cache_root = _CACHE_BASE_ROOT / digest[:2] / digest
    cache_root.mkdir(parents=True, exist_ok=True)
    _CACHE_ROOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _CACHE_ROOT_FILE.with_name(
        f"{_CACHE_ROOT_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(str(cache_root), encoding="utf-8")
    os.replace(temporary, _CACHE_ROOT_FILE)
    return cache_root


def handler(job):
    """Run the official handler and group video files separately for clients."""
    # ComfyUI and the handler are separate processes. The atomic control file is
    # how the already-running ComfyUI process learns this job's cache directory.
    # Serializing the selection and execution prevents concurrent jobs in one
    # worker from switching each other's cache root.
    with _JOB_LOCK:
        try:
            _select_project_cache(job)
        except (OSError, ValueError) as error:
            return {"error": str(error)}
        result = base.handler(job)
    if not isinstance(result, dict) or "images" not in result:
        return result

    images = []
    videos = []
    for artifact in result.get("images", []):
        extension = os.path.splitext(str(artifact.get("filename", "")))[1].lower()
        (videos if extension in _VIDEO_EXTENSIONS else images).append(artifact)

    result["images"] = images
    if videos:
        result["videos"] = videos
    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

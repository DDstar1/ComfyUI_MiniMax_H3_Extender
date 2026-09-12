"""RunPod's official ComfyUI handler with isolated H3 caches and video output."""

import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import urllib.request

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


def _namespace_digest(namespace):
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("Each merge chain needs a 'cache_namespace'")
    namespace = namespace.strip()
    if len(namespace) > 512:
        raise ValueError("'cache_namespace' must be 512 characters or fewer")
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()


def _volume_chain_video(namespace):
    """Locate a chain's assembled video in its cache directory on the volume.

    Returns None when the chain is not on this volume, which happens whenever a
    cache has been truncated by an edit or evicted. The caller then falls back
    to the copy the application stored, so the merge never depends on a cache
    surviving.
    """
    digest = _namespace_digest(namespace)
    directory = _CACHE_BASE_ROOT / digest[:2] / digest
    if not directory.is_dir():
        return None
    previews = [
        path
        for path in directory.glob("chain_*.preview.mp4")
        if path.is_file() and path.stat().st_size > 0
    ]
    if not previews:
        return None
    return max(previews, key=lambda path: path.stat().st_mtime)


def _download(url, destination):
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise ValueError("Merge fallback URLs must be https")
    with urllib.request.urlopen(url, timeout=600) as response:
        with open(destination, "wb") as handle:
            shutil.copyfileobj(response, handle)
    if destination.stat().st_size <= 0:
        raise ValueError("Merge fallback download was empty")
    return destination


def _find_ffmpeg():
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return str(exe)
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise RuntimeError("Merge: no ffmpeg binary is available in this image")


def _concat(ffmpeg, sources, destination):
    """Join finished chains. Stream copy first, re-encode only if that fails.

    Every chain is produced by the same workflow, so a stream copy is normally
    valid and costs seconds instead of a full re-encode. The fallback covers a
    chain that was rendered with different encode settings.
    """
    listing = destination.with_suffix(".txt")
    listing.write_text(
        "".join(f"file '{Path(src).as_posix()}'\n" for src in sources),
        encoding="utf-8",
    )
    common = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    copied = subprocess.run(
        [*common, "-c", "copy", str(destination)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if copied.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
        return "stream-copy"
    encoded = subprocess.run(
        [
            *common,
            "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(destination),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if encoded.returncode != 0:
        detail = encoded.stderr.decode("utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"Merge concat failed.\n{detail}")
    return "re-encode"


def _merge_chains(request):
    """Join each chain's finished video, in order, into one file.

    Chains are read from the Network Volume where they were rendered, so nothing
    is transferred in the normal case. A chain missing from the volume is pulled
    from the signed URL the trusted backend supplied for it.
    """
    chains = request.get("chains")
    if not isinstance(chains, list) or not chains:
        return {"error": "Merge requires a non-empty 'chains' list"}

    filename = str(request.get("filename") or "clipweave-merged.mp4")
    if "/" in filename or "\\" in filename or not filename.lower().endswith(".mp4"):
        return {"error": "Merge 'filename' must be a plain .mp4 name"}

    ffmpeg = _find_ffmpeg()
    sources = []
    origins = []
    with tempfile.TemporaryDirectory(prefix="comfytr-merge-") as workspace:
        root = Path(workspace)
        for position, chain in enumerate(chains):
            if not isinstance(chain, dict):
                return {"error": f"Merge chain {position} is not an object"}
            try:
                local = _volume_chain_video(chain.get("cache_namespace"))
            except ValueError as error:
                return {"error": str(error)}
            if local is not None:
                sources.append(local)
                origins.append("volume")
                continue
            fallback = chain.get("fallback_url")
            if not fallback:
                return {
                    "error": (
                        f"Merge chain {position} is not on this volume and no "
                        "'fallback_url' was supplied"
                    )
                }
            try:
                sources.append(_download(fallback, root / f"chain_{position:03d}.mp4"))
            except Exception as error:
                return {"error": f"Merge chain {position} download failed: {error}"}
            origins.append("fallback")

        destination = root / filename
        method = _concat(ffmpeg, sources, destination)
        payload = base64.b64encode(destination.read_bytes()).decode("ascii")
        size = destination.stat().st_size

    return {
        "images": [],
        "videos": [{"filename": filename, "type": "base64", "data": payload}],
        "merge": {"chains": len(sources), "sources": origins, "method": method, "bytes": size},
    }


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


def _fetch_chain(request):
    """Recover an already-finished chain video that a job's own /status expired.

    A generation job's result (including its base64 video) is only queryable
    through Runpod's /status for a limited window after completion -- roughly
    30 minutes, observed live. The video itself survives on the Network Volume
    indefinitely, because the Extender's disk cache writes an assembled preview
    there as part of normal operation, independent of any job's own retention.
    This does not create new persistence; it exposes what already exists.

    The returned file matches a render's normal output.videos exactly, since
    every render in this application uses neutral color_adjustment values --
    if that ever changes, a color-corrected clip and its neutral volume preview
    would differ, and this fallback would return the wrong pixels.
    """
    namespace = request.get("cache_namespace")
    try:
        local = _volume_chain_video(namespace)
    except ValueError as error:
        return {"error": str(error)}
    if local is None:
        return {"error": "No cached video for this cache_namespace on this volume"}
    payload = base64.b64encode(local.read_bytes()).decode("ascii")
    return {
        "images": [],
        "videos": [{"filename": local.name, "type": "base64", "data": payload}],
    }


def handler(job):
    """Run the official handler and group video files separately for clients."""
    job_input = job.get("input") if isinstance(job, dict) else None
    # Merge and fetch both read already-rendered video off the volume and never
    # touch ComfyUI, so both are handled before the cache root is selected for
    # a generation job.
    if isinstance(job_input, dict) and isinstance(job_input.get("merge"), dict):
        try:
            return _merge_chains(job_input["merge"])
        except (OSError, RuntimeError, ValueError) as error:
            return {"error": str(error)}
    if isinstance(job_input, dict) and isinstance(job_input.get("fetch"), dict):
        try:
            return _fetch_chain(job_input["fetch"])
        except OSError as error:
            return {"error": str(error)}

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

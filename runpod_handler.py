"""RunPod's official ComfyUI handler with isolated H3 caches and video output."""

import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

import runpod
import runpod_base_handler as base
import requests


_VIDEO_KEYS = {"videos", "gifs", "h3_outputs"}
_VIDEO_EXTENSIONS = {".avi", ".gif", ".mkv", ".mov", ".mp4", ".webm"}
_VOICE_EXTENSIONS = {".flac", ".m4a", ".mp3", ".ogg", ".wav"}
_CACHE_BASE_ROOT = Path(
    os.environ.get("H3_CACHE_ROOT", "/runpod-volume/comfytr-cache")
).expanduser().resolve()
_CACHE_ROOT_FILE = Path(
    os.environ.get("H3_CACHE_ROOT_FILE", "/tmp/comfytr-h3-cache-root")
).expanduser().resolve()
_JOB_LOCK = threading.Lock()
_original_get_history = base.get_history


def _comfy_process_args():
    """Return the active ComfyUI command line without depending on a fixed PID."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    for candidate in proc.iterdir():
        if not candidate.name.isdigit():
            continue
        try:
            args = (candidate / "cmdline").read_bytes().decode("utf-8", errors="replace").split("\0")
        except OSError:
            continue
        if any("main.py" in arg for arg in args):
            return [arg for arg in args if arg]
    return []


def _attention_metadata():
    """Describe the selected attention mode, rather than inferring it from packages."""
    args = _comfy_process_args()
    try:
        import sageattention

        sage_version = getattr(sageattention, "__version__", "installed")
    except ImportError:
        sage_version = None
    return {
        "sageattention_version": sage_version,
        "sageattention_requested": "--use-sage-attention" in args,
        "comfyui_args": args,
    }


def _runtime_metadata(worker_rate_usd_per_second=None):
    """Capture runtime versions and the active attention selection for a render."""
    metadata = _worker_metadata(worker_rate_usd_per_second)
    try:
        import torch

        metadata.update(
            {
                "torch_version": torch.__version__,
                "torch_cuda_version": torch.version.cuda,
            }
        )
    except ImportError:
        metadata.update({"torch_version": None, "torch_cuda_version": None})
    metadata.update(_attention_metadata())
    return metadata


def _worker_metadata(worker_rate_usd_per_second=None):
    """Return a small, JSON-safe GPU snapshot for render diagnostics."""
    try:
        gpu = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        values = [value.strip() for value in gpu.stdout.splitlines()[0].split(",")] if gpu.returncode == 0 and gpu.stdout.strip() else []
        model = values[0] if values else None
    except OSError:
        values = []
        model = None
    try:
        rate = float(worker_rate_usd_per_second)
        if rate < 0:
            rate = None
    except (TypeError, ValueError):
        rate = None
    return {
        "gpu_model": model,
        "vram_total_mb": int(values[1]) if len(values) > 1 and values[1].isdigit() else None,
        "vram_used_mb": int(values[2]) if len(values) > 2 and values[2].isdigit() else None,
        "gpu_utilization_percent": int(values[3]) if len(values) > 3 and values[3].isdigit() else None,
        "worker_id": os.environ.get("RUNPOD_POD_ID") or os.environ.get("RUNPOD_WORKER_ID"),
        "worker_rate_usd_per_second": rate,
    }



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


def _volume_chain_video(namespace, workspace=None):
    """Locate a chain's assembled video in its cache directory on the volume.

    Returns None when the chain is not on this volume, which happens whenever a
    cache has been truncated by an edit or evicted. The caller then falls back
    to the copy the application stored, so recovery and merge never depend on a
    cache surviving.

    Confirmed by listing the volume directly: this application's workflow never
    populates chain_*.preview.mp4 -- that file belongs to a different, unused
    live-preview feature of the vendored node. What is always present is
    chain_*.final.video/ref2va_NNNN.<ext>, one file per clip position in the
    chain. A single-clip chain therefore has exactly one file, which is already
    the render's own output. A chain with more than one clip needs those
    segments joined; `workspace` is required in that case, since a temporary
    file has to exist somewhere to hold the result.
    """
    digest = _namespace_digest(namespace)
    directory = _CACHE_BASE_ROOT / digest[:2] / digest
    final_dir = directory / "chain_extender_1.final.video"
    if not final_dir.is_dir():
        return None

    def _index(path):
        match = re.match(r"ref2va_(\d+)\.", path.name)
        return int(match.group(1)) if match else -1

    segments = sorted(
        (
            path
            for path in final_dir.glob("ref2va_*.*")
            if path.is_file() and path.stat().st_size > 0
        ),
        key=_index,
    )
    if not segments:
        return None
    if len(segments) == 1:
        return segments[0]
    if workspace is None:
        raise ValueError(
            f"Chain has {len(segments)} clip segments and needs a workspace to join them"
        )
    joined = Path(workspace) / f"{digest[:16]}-joined.mp4"
    _concat(_find_ffmpeg(), segments, joined)
    return joined


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


def _prepare_voice_references(job_input):
    """Upload trusted voice samples to ComfyUI's active input directory.

    The official worker uploads images through ComfyUI's HTTP API instead of
    assuming a container path. Voice references need the same treatment: the
    base image can configure a different input directory from ``/comfyui/input``.
    Uploading through ComfyUI ensures LoadAudio resolves the file it validates.
    """
    if not isinstance(job_input, dict):
        return
    audios = job_input.get("audios", [])
    if audios is None:
        return
    if not isinstance(audios, list) or len(audios) > 3:
        raise ValueError("Voice references must be a list of at most three audio files")
    for item in audios:
        if not isinstance(item, dict):
            raise ValueError("Voice reference is invalid")
        name = str(item.get("name") or "")
        payload = str(item.get("audio") or "")
        if Path(name).name != name or Path(name).suffix.lower() not in _VOICE_EXTENSIONS:
            raise ValueError("Voice reference filename is invalid")
        match = re.fullmatch(r"data:audio/(?:mpeg|wav|x-wav|mp4|x-m4a|ogg|flac);base64,([A-Za-z0-9+/=]+)", payload, re.IGNORECASE)
        if not match:
            raise ValueError("Voice reference must be base64 audio data")
        raw = base64.b64decode(match.group(1), validate=True)
        if not raw or len(raw) > 12 * 1024 * 1024:
            raise ValueError("Voice reference must be between 1 byte and 12 MB")
        mime_type = match.group(0).split(";", 1)[0][5:]
        try:
            response = requests.post(
                f"http://{base.COMFY_HOST}/upload/image",
                files={
                    "image": (name, raw, mime_type),
                    "overwrite": (None, "true"),
                },
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise ValueError(f"Could not upload voice reference '{name}': {error}") from error


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
                local = _volume_chain_video(chain.get("cache_namespace"), workspace=root)
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


def _latest_chain_segment(namespace):
    """Return the most recent clip segment and its volume-relative key."""
    digest = _namespace_digest(namespace)
    directory = _CACHE_BASE_ROOT / digest[:2] / digest / "chain_extender_1.final.video"
    segments = sorted(
        (path for path in directory.glob("ref2va_*.*") if path.is_file() and path.stat().st_size > 0),
        key=lambda path: int(re.search(r"ref2va_(\d+)\.", path.name).group(1)) if re.search(r"ref2va_(\d+)\.", path.name) else -1,
    )
    if not segments:
        return None, None
    segment = segments[-1]
    return segment, segment.relative_to(_CACHE_BASE_ROOT.parent).as_posix()


def _volume_video_result(namespace, worker_rate_usd_per_second=None):
    """Return metadata only. The MP4 remains on the network volume."""
    segment, key = _latest_chain_segment(namespace)
    if segment is None:
        return {"error": "No rendered video was found in this chain's volume cache"}
    return {
        "images": [],
        "videos": [{
            "filename": segment.name,
            "type": "runpod-volume",
            "volume_key": key,
            "bytes": segment.stat().st_size,
        }],
        "worker_metadata": _worker_metadata(worker_rate_usd_per_second),
    }


def _deliver_video_to_supabase(delivery, namespace, execution_ms=None):
    """Upload a finished segment through a one-use signed URL and publish it.

    The trusted application creates both URLs. This worker does not hold any
    Supabase credential and cannot choose a different object's storage path.
    """
    if not isinstance(delivery, dict):
        return False
    required = ("upload_url", "completion_url", "job_id", "storage_path", "expires_at", "token")
    if any(not delivery.get(key) for key in required):
        return False
    segment, _ = _latest_chain_segment(namespace)
    if segment is None:
        raise RuntimeError("No rendered video was found for direct delivery")
    with segment.open("rb") as handle:
        upload = requests.put(
            str(delivery["upload_url"]), data=handle,
            headers={"Content-Type": "video/mp4", "x-upsert": "false"}, timeout=600,
        )
    upload.raise_for_status()
    # Configure the complete callback route once on the endpoint. The per-job
    # URL remains a fallback for older workers and local development.
    configured_callback_url = os.environ.get("CLIPWEAVE_RENDER_COMPLETE_URL", "").strip()
    completion_url = (
        configured_callback_url
        if configured_callback_url.startswith("https://")
        else str(delivery["completion_url"])
    )
    completed = requests.post(
        completion_url,
        json={
            "jobId": delivery["job_id"], "path": delivery["storage_path"],
            "filename": segment.name, "bytes": segment.stat().st_size,
            **({"executionMs": int(execution_ms)} if execution_ms is not None else {}),
            "expiresAt": delivery["expires_at"], "token": delivery["token"],
        }, timeout=30,
    )
    completed.raise_for_status()
    return True

def _fetch_chain(request):
    """Recover an already-finished chain video that a job's own /status expired.

    A generation job's result (including its base64 video) is only queryable
    through Runpod's /status for a limited window after completion -- roughly
    30 minutes, observed live. The video itself survives on the Network Volume
    indefinitely, because the Extender's disk cache writes an assembled preview
    there as part of normal operation, independent of any job's own retention.
    This does not create new persistence; it exposes what already exists.

    A chain with more than one clip position is joined with the same stream-copy
    concat merge uses, since the cache holds one file per clip position, not one
    cumulative file. The returned file matches a render's normal output.videos
    exactly, since every render in this application uses neutral
    color_adjustment values -- if that ever changes, a color-corrected clip and
    its neutral volume cache would differ, and this fallback would return the
    wrong pixels.
    """
    try:
        return _volume_video_result(request.get("cache_namespace"))
    except (OSError, ValueError) as error:
        return {"error": str(error)}


def handler(job):
    """Run the official handler and group video files separately for clients."""
    job_input = job.get("input") if isinstance(job, dict) else None
    worker_rate = job_input.get("worker_rate_usd_per_second") if isinstance(job_input, dict) else None
    try:
        rate_value = float(worker_rate)
    except (TypeError, ValueError):
        rate_value = None
    if rate_value is not None and rate_value >= 0:
        print(f"[ClipWeave] Worker rate: ${rate_value:.6f}/s (${rate_value * 3600:.4f}/hr)", flush=True)
    started_at = time.monotonic()
    print(f"[ClipWeave] Runtime: {_runtime_metadata(rate_value)}", flush=True)
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
            result = _fetch_chain(job_input["fetch"])
            # Recovery jobs use the same one-use upload and completion callback
            # as a normal render, so a browser does not need to poll a second
            # time before the recovered video appears in ClipWeave.
            if "error" not in result and job_input.get("delivery"):
                _deliver_video_to_supabase(
                    job_input["delivery"],
                    job_input["fetch"].get("cache_namespace"),
                )
            return result
        except (OSError, RuntimeError, ValueError, requests.RequestException) as error:
            return {"error": str(error)}

    # ComfyUI and the handler are separate processes. The atomic control file is
    # how the already-running ComfyUI process learns this job's cache directory.
    # Serializing the selection and execution prevents concurrent jobs in one
    # worker from switching each other's cache root.
    with _JOB_LOCK:
        try:
            _select_project_cache(job)
            if not base.check_server(
                f"http://{base.COMFY_HOST}/",
                base.COMFY_API_AVAILABLE_MAX_RETRIES,
                base.COMFY_API_AVAILABLE_INTERVAL_MS,
            ):
                return {
                    "error": (
                        f"ComfyUI server ({base.COMFY_HOST}) was not reachable "
                        "while preparing voice references."
                    )
                }
            _prepare_voice_references(job_input)
        except (OSError, ValueError) as error:
            return {"error": str(error)}
        result = base.handler(job)
    elapsed_seconds = round(time.monotonic() - started_at, 3)
    print(
        f"[ClipWeave] Render finished in {elapsed_seconds}s; "
        f"runtime={_runtime_metadata(rate_value)}",
        flush=True,
    )
    if not isinstance(result, dict) or "images" not in result:
        return result

    images = []
    videos = []
    for artifact in result.get("images", []):
        extension = os.path.splitext(str(artifact.get("filename", "")))[1].lower()
        (videos if extension in _VIDEO_EXTENSIONS else images).append(artifact)

    result["images"] = images
    if videos:
        try:
            if _deliver_video_to_supabase(job_input.get("delivery"), job_input.get("cache_namespace"), elapsed_seconds * 1000):
                print("[ClipWeave] Video delivered to Supabase.", flush=True)
                return {"images": [], "videos": [], "delivered": True,
                        "worker_metadata": _worker_metadata(rate_value)}
        except (OSError, ValueError, requests.RequestException) as error:
            # Return the volume key as the durable fallback. The application
            # can still ingest it when a delivery URL has expired or is down.
            print(f"[ClipWeave] Direct video delivery failed: {error}", flush=True)
        try:
            return _volume_video_result(job_input.get("cache_namespace"), rate_value)
        except (OSError, ValueError) as error:
            return {"error": str(error)}
    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

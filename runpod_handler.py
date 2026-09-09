"""RunPod's official ComfyUI handler with video-output support."""

import os

import runpod
import runpod_base_handler as base


_VIDEO_KEYS = {"videos", "gifs", "h3_outputs"}
_VIDEO_EXTENSIONS = {".avi", ".gif", ".mkv", ".mov", ".mp4", ".webm"}
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


def handler(job):
    """Run the official handler and group video files separately for clients."""
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

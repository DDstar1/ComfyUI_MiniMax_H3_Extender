# RunPod Serverless deployment

This repository builds a RunPod ComfyUI Serverless worker with the MiniMax H3 Extender installed. It keeps RunPod's official ComfyUI handler and accepts normal ComfyUI API-format workflows.

## Current deployment state

GitHub Actions successfully built and published commit `e1c4c07` on 2026-09-09:

```text
ghcr.io/ddstar1/comfyui_minimax_h3_extender:runpod-latest
```

No RunPod Pod, template or endpoint has been created. Building the image used GitHub
Actions, not RunPod compute. The existing Network Volume was inspected but not
modified.

## Network Volume layout

Attach the existing Network Volume to the endpoint. Serverless mounts it at `/runpod-volume`, and the base worker discovers models in these directories:

```text
/runpod-volume/models/unet/
/runpod-volume/models/clip/
/runpod-volume/models/vae/
/runpod-volume/models/loras/
```

For the current Ref2VA workflow, keep these filenames or update the workflow selectors to match the files on the volume:

```text
models/unet/minimax_h3_ref2va_pruned_int8_convrot.safetensors
models/clip/qwen3vl_32b_minimax_h3_int4_convrot.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16(1).safetensors
```

The image sets `H3_CACHE_ROOT=/runpod-volume/comfytr-cache`, which stores the Extender's disk-backed motion context on the volume. Override this environment variable in the RunPod template if a different directory is required.

## Build and publish

Every relevant push to `main` runs **Build RunPod Serverless image** and publishes:

```text
ghcr.io/ddstar1/comfyui_minimax_h3_extender:runpod-latest
```

The workflow also publishes a commit-specific `runpod-sha-...` tag. The image is built for `linux/amd64`, as required by RunPod. If the GHCR package is private, either make the package public or add GitHub Container Registry credentials to RunPod before creating the template.

To build manually:

```bash
docker build --platform linux/amd64 -t ghcr.io/ddstar1/comfyui_minimax_h3_extender:runpod-latest .
docker push ghcr.io/ddstar1/comfyui_minimax_h3_extender:runpod-latest
```

## Create the endpoint

1. Confirm the latest **Build RunPod Serverless image** workflow completed successfully.
2. In RunPod, create a Serverless template using `ghcr.io/ddstar1/comfyui_minimax_h3_extender:runpod-latest`.
3. Create an endpoint from that template in `EU-RO-1`, because the existing Network Volume `my_100gb_volume` (`0oaqjjkos5`) is in that data center.
4. Attach that Network Volume under the endpoint's advanced settings.
5. Choose a GPU with enough VRAM for the selected MiniMax H3 model and set the endpoint's worker limits.

## Request format

Export the workflow with ComfyUI's **Save (API Format)** option. Send that object as `input.workflow`. Reference images can be supplied as base64 data URLs in `input.images`; each `name` must match the filename used by the workflow's image-loading node.

```json
{
  "input": {
    "workflow": {
      "1": {
        "class_type": "LoadImage",
        "inputs": { "image": "character-reference.png" }
      }
    },
    "images": [
      {
        "name": "character-reference.png",
        "image": "data:image/png;base64,..."
      }
    ]
  }
}
```

Submit the payload to the endpoint's `/runsync` route for a synchronous call or `/run` for an asynchronous job. Production workflows should be sent exactly as exported; the abbreviated object above only shows the image contract.

Completed MP4/MKV artifacts are returned in `output.videos`. Each entry contains `filename`, `type`, and `data`, using the same base64 or S3 URL contract as `output.images`:

```json
{
  "output": {
    "images": [],
    "videos": [
      {
        "filename": "MiniMax_H3_cached.mp4",
        "type": "base64",
        "data": "AAAAIGZ0eXBpc29t..."
      }
    ]
  }
}
```

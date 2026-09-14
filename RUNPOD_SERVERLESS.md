# RunPod Serverless deployment

## Cost provenance work — 2026-09-13

The worker is being extended to return non-secret hardware metadata with each completed job: GPU model, VRAM, worker identifier and data centre when available. ClipWeave records that alongside RunPod's `executionTime`. This establishes which hardware served a render, but it is not by itself a billing invoice: RunPod's normal job-status payload does not provide an authoritative hourly charge. The application must snapshot the endpoint's configured hourly rate at submission and calculate `executionTimeMs / 3,600,000 × capturedRate`.

Do not use a later GPU-catalog price to overwrite a historical estimate. A catalog lookup is useful for configuration assistance, but rates can vary by data centre and change after a job has completed.
This repository builds a RunPod ComfyUI Serverless worker with the MiniMax H3 Extender installed. It keeps RunPod's official ComfyUI handler and accepts normal ComfyUI API-format workflows.

## Current deployment state

**Rendering works.** Three clips have completed on this endpoint across two
chains, stored and played back from the application's own bucket. Getting there
took several rounds of fixes, documented in the root README's checkpoint —
a missing worker node, a stale server-side allowlist, a missing required
input, and the model-path issue below.

Queue endpoint `my_extender_endpoint` (`nqpfrj6twlaz5h`, `EU-RO-1`, Network
Volume `0oaqjjkos5`) is pinned to an **image digest**, not the mutable
`runpod-latest` tag:

```text
ghcr.io/ddstar1/comfyui_minimax_h3_extender@sha256:cef2c2ff5f124d7154cebd258bb50836996f37731e69ba6fe7ebd96b02b38106
```

That digest is current as of the merge/fetch fix below; it moves every time the
template is repinned, so check `runpodctl template get uoq6ryaqu6` for the
live value rather than trusting this file. The pin exists because tracking the
mutable tag once left the fleet mixed mid-rollout — old-image workers reported
empty model lists, new-image workers succeeded, and the same job's outcome
depended on which worker picked it up. Rolling out now needs an explicit
`runpodctl template update uoq6ryaqu6 --image <ref>`, and it's worth waiting
for `runpodctl serverless health` to show `ready: 0` afterward so old workers
have actually cycled out before testing.

| Setting | Value |
| --- | --- |
| Endpoint type | Queue |
| Worker range | minimum 0, maximum 3 |
| Idle timeout | 300 seconds |
| Job timeout | 1800000 ms (30 minutes) |
| Scaling | Queue delay, target 4 seconds |
| GPU count | 1 |
| GPU pools | `AMPERE_16`, `AMPERE_24` |
| Minimum CUDA | 12.0 |
| Container disk | 5 GB |
| FlashBoot | Off |

Keep minimum workers at zero unless continuous warm capacity is worth
continuous GPU billing.

## Network Volume layout

Attach the existing Network Volume to the endpoint. Serverless mounts it at
`/runpod-volume`; a Pod mounts the same volume at `/workspace`. The base worker
discovers models under `/runpod-volume/models/`, but **the weights are not
there**. The Pod setup installs ComfyUI onto the volume itself, so they live at:

```text
<volume>/runpod-slim/ComfyUI/models/
```

Volume `0oaqjjkos5` was listed over the S3 API on 2026-09-11. Its root contains
only `comfytr-cache/` (written by this worker) and `runpod-slim/`; there is no
`models/` directory at the root. That is why every render failed validation with
empty model lists (`unet_name: '…' not in []`) while the container itself was
healthy.

Copying the weights to `/runpod-volume/models/` is not an option: the volume is
100 GB with 69.26 GB already used, and the five H3 models alone are ~52 GB. The
image therefore appends
[`extra_model_paths.runpod-volume.yaml`](extra_model_paths.runpod-volume.yaml)
to `/comfyui/extra_model_paths.yaml`, which ComfyUI auto-loads from its base
directory with no CLI flag. The entries are additive, so a volume laid out as
`/runpod-volume/models/` keeps working.

Verified files on the volume for the current Ref2VA workflow:

```text
runpod-slim/ComfyUI/models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors   19.53 GB
runpod-slim/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors        25.28 GB
runpod-slim/ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors                             4.85 GB
runpod-slim/ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors                             0.56 GB
runpod-slim/ComfyUI/models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors      1.82 GB
```

Earlier revisions of this document listed the text encoder as `int4` and the
LoRA with a `(1)` suffix. Neither filename exists on the volume; the frontend's
`render-workflow.ts` constants were corrected to match the list above. ComfyUI
treats `unet`/`diffusion_models` and `clip`/`text_encoders` as aliases for the
same folder lists, so those directory-name differences are harmless.

The image uses `/runpod-volume/comfytr-cache` as its cache base. Every request must
include a stable `cache_namespace`. The handler hashes it and atomically tells the
already-running ComfyUI process to use:

```text
/runpod-volume/comfytr-cache/<hash-prefix>/<sha256-of-cache-namespace>/
```

Derive the namespace in the trusted Next.js backend from the authenticated user ID
and project ID, for example `user.id + ":" + project.id`. Never accept an arbitrary
user ID from the browser. The same project must send the same namespace on every
continuation request. Different projects resolve to different cache directories.

`H3_CACHE_ROOT` changes the base directory and `H3_CACHE_ROOT_FILE` changes the
ephemeral control-file location. The handler serializes jobs inside each worker so
concurrent requests cannot switch one another's cache root.

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

## Endpoint creation and worker lifecycle

The endpoint was created from the Docker image, in `EU-RO-1`, with the existing
Network Volume attached. Next.js does not start a worker directly. It submits a job
to `/run`; RunPod queues the request, obtains an allowed GPU, pulls this image, mounts
the volume and starts the inherited worker entrypoint. The image starts ComfyUI and
`/handler.py`. At the bottom of `runpod_handler.py`, this call registers the queue
handler:

```python
runpod.serverless.start({"handler": handler})
```

For each job, the wrapper selects the project cache, calls the official ComfyUI
handler, and groups MP4/MKV artifacts under `output.videos`. RunPod stops an idle
worker after the configured idle timeout. With minimum workers zero, GPU compute can
scale to zero; the next request then pays the cold-start delay.

The Dockerfile performs these image-time steps:

1. Inherit `runpod/worker-comfyui:5.10.0-base`.
2. Copy this repository into ComfyUI's `custom_nodes` directory.
3. Preserve RunPod's official `/handler.py` as `/runpod_base_handler.py`.
4. Install this repository's wrapper as `/handler.py`.
5. Install node dependencies and run a CPU ComfyUI import check.

GitHub Actions rebuilds and republishes `runpod-latest` when relevant code is pushed.
An existing endpoint using a mutable tag still needs a new endpoint release/worker
rollout before all workers run the newly published image.

## Measured Pod benchmark and resolution

Manual tests used an RTX 2000 Ada 16 GB Pod billed at $0.26/hour:

| Resolution selector | Six-second render | Approx. clip cost | 30 sequential clips |
| --- | ---: | ---: | ---: |
| 0.2 MP | 528.23s and 547.89s; 8m 58s average | $0.039 | 4h 29m / $1.17 |
| 0.4 MP | 1026.30s; 17m 6s | $0.074 | 8h 33m / $2.22 |

The 0.4 MP run almost exactly doubled runtime. During the lower-resolution run,
reported VRAM was 100%, system memory 78% and GPU utilization around 20%, which
indicates that higher resolutions may increase offloading or exhaust a 16 GB card.

The workflow's Resolution Selector width and height outputs are connected to the
MiniMax H3 Extender. All dimensions are aligned to its 32-pixel canvas grid. Changing
resolution invalidates the existing motion-context geometry, so do not change it
mid-sequence: reset the sequence cache and regenerate from Clip 1 at the new setting.
Use lower resolution for drafts and benchmark visual quality before choosing 0.4 MP
for final output.

## Request format

Export the workflow with ComfyUI's **Save (API Format)** option. Send that object as `input.workflow`. Reference images can be supplied as base64 data URLs in `input.images`; each `name` must match the filename used by the workflow's image-loading node.

```json
{
  "input": {
    "cache_namespace": "authenticated-user-id:project-id",
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

For these multi-minute renders, use `/run`, store the returned job ID, and poll the
status URL. Configure the Next.js server with:

```dotenv
RUNPOD_ENDPOINT_API_KEY=YOUR_RUNPOD_API_KEY
RUNPOD_ENDPOINT_ID=nqpfrj6twlaz5h
```

Never prefix the API key with `NEXT_PUBLIC_`. Derive `cache_namespace` on the server
from the authenticated Supabase user and owned project. Enforce one active job per
project across all backend instances; the handler lock only serializes jobs inside a
single worker, while the endpoint can run three workers for different projects.

## Merging finished chains

The Extender chains clips into one continuous take and has no cut primitive, so a
cut is produced by rendering a new chain under a different `cache_namespace`.
Joining those chains into one film is a separate job type. The chain model
(`continuesPrevious`, per-chain namespaces, chain-scoped validation) is built in
the application; see the [design note](../frontend/docs/design/scene-cuts-and-merge.md).

Where a chain's video actually lives on the volume was wrong in an earlier
version of this document. Confirmed by listing the volume directly: this
workflow never writes the vendored node's `chain_*.preview.mp4` file — that
belongs to a different, unused live-preview feature. What is always present is
`chain_*.final.video/ref2va_NNNN.<ext>`, one file per clip position in the
chain, the same per-segment cache the node uses for Full Batch export. A
single-clip chain has exactly one such file, already equal to that render's own
output; a chain with more than one clip has one file per position, joined in
order by the same stream-copy concat merge uses.

Send `input.merge` instead of `input.workflow`. The job never touches ComfyUI:

```json
{
  "input": {
    "merge": {
      "filename": "clipweave-final.mp4",
      "chains": [
        {
          "cache_namespace": "authenticated-user-id:project-id:0",
          "fallback_url": "https://…signed-supabase-url…"
        },
        {
          "cache_namespace": "authenticated-user-id:project-id:1",
          "fallback_url": "https://…signed-supabase-url…"
        }
      ]
    }
  }
}
```

Chains are joined in array order. For each one the worker hashes the namespace
exactly as a render does and looks for the chain's assembled video in its cache
directory on the Network Volume, so nothing is transferred in the normal case.
A chain missing from the volume — the cache is truncated whenever a clip is
edited, and nothing guarantees retention — is downloaded from `fallback_url`
instead, which must be https. Supplying a fallback for every chain is
recommended; a chain absent from both is an error.

Concatenation is attempted as a stream copy first, which is lossless and takes
seconds because every chain comes from the same workflow. It falls back to an
H.264 re-encode if a chain was rendered with different encode settings.

The result uses the normal video contract, plus a `merge` summary reporting
where each chain came from:

```json
{
  "output": {
    "images": [],
    "videos": [{ "filename": "clipweave-final.mp4", "type": "base64", "data": "AAAA…" }],
    "merge": { "chains": 2, "sources": ["volume", "fallback"], "method": "stream-copy", "bytes": 5242880 }
  }
}
```

Two caveats remain. This depends on every render using neutral
`color_adjustment`, which is true today; if per-clip colour correction is ever
wired up, a corrected clip's live output and its cached segment could diverge,
since it is unverified whether the cache reflects colour settings applied after
the clip was originally generated. And the merged film is returned base64
inline like any other video, so a long film will eventually outgrow the
response; storing it from the worker instead is unsolved.

`input.merge` is implemented in `runpod_handler.py` and the chain model exists
in the application. **Never executed against a real multi-clip chain** — every
chain rendered so far has had exactly one clip, so the join path is
syntax-checked and logically fixed but unproven live. The merge button and
gating it on every clip being validated are not built.

### Recovering a render whose own status has expired

A generation job's own `/status` result is only queryable for roughly 30
minutes after completion — verified live: a job that had genuinely finished
(`executionTime` set, a video in its output) later 404'd. The video survives
that window regardless, on the volume, for the same reason merge can read it.

Send `input.fetch` instead of `input.workflow`. Like merge, this never touches
ComfyUI:

```json
{ "input": { "fetch": { "cache_namespace": "authenticated-user-id:project-id:0" } } }
```

It looks up the chain's segments the same way merge does, joining them if there
is more than one, and returns the result in the normal `output.videos` shape —
nothing downstream has to know the video came from here instead of a fresh
render. If no segment exists at that namespace, it fails fast with `{"error":
"No cached video for this cache_namespace on this volume"}` rather than
retrying, since there is nothing more to recover.

**The application only calls this for a multi-clip chain.** For the common
case — a single-clip chain — its own server reads the volume directly over the
S3-compatible API, with no RunPod job and no GPU charge, since a single
existing file needs no joining. See
[`frontend/src/lib/server/runpod-volume.ts`](../frontend/src/lib/server/runpod-volume.ts).
That direct read does not support presigned URLs — verified live: a
correctly-signed query-string GET with no `Authorization` header returns 401
`missing Authorization header` — so every read, from the worker or from the
application server, must carry a full SigV4 header signature; a bare link can
never be handed to a browser.

Verified live for a single-clip chain, after the `.final.video` fix landed and
the endpoint was re-pinned: `input.fetch` completed and returned
`ref2va_0000.mp4`, 1,266,081 bytes, matching the volume listing. Not yet
exercised for a multi-clip chain. The direct server-side S3 read
(`runpod-volume.ts`) was verified separately, against the same file, with a
standalone script mirroring its exact logic.

## Render output contract

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


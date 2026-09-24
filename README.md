# ComfyUI MiniMax H3 Extender

## Current ClipWeave worker behavior — 2026-09-23

The RunPod worker now requires private Cloudflare R2 motion-context storage.
Before a render it restores the chain's cache under the hashed
`cache_namespace`; after a successful ComfyUI job it uploads the updated cache
and removes stale remote segments before reporting success. A missing R2
configuration or failed upload is an error, rather than a silent fallback to a
network-volume-only cache. The local network volume still holds the H3 model
weights and working files. When ComfyUI produces an audio-bearing video, the
handler replaces the video-only chain segment with that muxed output and checks
the copied segment's audio stream. It also reports worker and
timing metadata. See [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md) for required
worker environment variables and the distinction between motion context and
customer-facing video delivery.

ClipWeave currently uses a temporary 0.08 MP, 10-step Draft render profile and
persists selectable full-story runs in its separate frontend repository.
Vast.ai now has a separate serverless image and PyWorker adapter in
`Dockerfile.vast`, `vast_handler.py`, and `vast_provision_models.py`. This does
not change the RunPod image or the application's active RunPod submission path.
The Vast endpoint is provisioned with zero workers while the image, R2 secrets,
and long-running request delivery are validated. Marketplace offers are not
deployed workers; the Vast image is not yet carrying customer renders.

## Vast.ai serverless preparation

GitHub Actions publishes `ghcr.io/ddstar1/comfyui_minimax_h3_extender:vast-latest`
from `Dockerfile.vast`. Vast endpoint `clipweave-minimax-h3` (ID `38350`)
has workergroup `48221` and private template `740056`. The endpoint has
`min_load=0`, `cold_workers=0`, and no active workers, so it is currently a
non-billable deployment scaffold. The workergroup targets one GPU with at least
23 GB VRAM and 120 GB disk.

The image carries code, not model weights. On first boot, the Vast startup
script downloads the five MiniMax H3 files used by ClipWeave (about 56 GB)
from `Comfy-Org/MiniMax-H3` to the worker's local disk. A surviving cold worker
reuses them. The Vast adapter accepts `/generate/sync`, passes `input` to the
same render handler used by RunPod, and therefore preserves R2 motion-cache
restore/upload and signed video delivery. It needs the worker environment
variables `CLOUDFLARE_S3_API_ENDPOINT`, `CLOUDFLARE_ACCESS_KEY_ID`,
`CLOUDFLARE_SECRET_ACCESS_KEY`, `CLOUDFLARE_R2_BUCKET`, and optionally
`HF_TOKEN`. Configure secrets in Vast account environment variables, not in
the public image or repository.

Vast's synchronous request stays open for the full render, unlike RunPod's
submit-and-poll API. The ClipWeave frontend still submits to RunPod until a
durable Vast request dispatcher and end-to-end test are in place. Do not switch
production traffic by only replacing the endpoint URL.

## Latest documentation checkpoint — 2026-09-16

The latest ClipWeave Studio Duration/Render layout refinement is frontend-only:
it does not change the worker request contract, cache namespace, H3 workflow,
or endpoint configuration. The application continues to provide one fixed
quality/frame geometry per project, chain-scoped cache input, durable job
recovery, private Supabase media storage and CPU FFmpeg final exports.


## ClipWeave application integration — 2026-09-13

ClipWeave sends normal H3 workflow requests with a trusted, chain-scoped
`input.cache_namespace`. The frontend saves job state and video assets in
Supabase, recovers accepted jobs without re-submitting GPU work, and uses signed
storage URLs for playback.

The customer-facing final **Merge videos** action now runs in the Next.js server
with CPU FFmpeg. It selects the final cumulative video from every validated chain
and stores the private export in Supabase, avoiding duplicated continuation
footage and a new RunPod job. Keep `input.merge` documented as a worker capability,
but do not assume the web product invokes it. Quality and aspect ratio are fixed
for a project once clips exist so a chain never changes cache geometry mid-run.


A ComfyUI custom node for **MiniMax H3** designed to generate long, continuous video sequences from multiple clips while preserving motion, visual continuity, and audio continuity between generations.

The node combines **Ref2VA conditioning, Motion Context, disk caching, multi-clip generation, image references, audio references, and final video/audio decoding** into a much simpler workflow.

## RunPod Serverless

The published image includes request-scoped project cache isolation using
`input.cache_namespace` and an atomic handler-to-ComfyUI control file.

This fork includes a production container based on RunPod's official ComfyUI worker. It loads models via an `extra_model_paths` entry pointing at where the Pod setup actually installed them on the volume (`<volume>/runpod-slim/ComfyUI/models`, not the base worker's default `/runpod-volume/models`), persists the Extender cache through `H3_CACHE_ROOT`, and publishes a `linux/amd64` image to GitHub Container Registry through GitHub Actions.

The worker keeps RunPod's normal `input.workflow` and `input.images` request format. A thin handler extension collects final MP4/MKV artifacts and returns them in `output.videos`, since the stock handler only collects image outputs. Two further job types skip ComfyUI entirely and read already-rendered video straight off the volume: `input.merge` joins multiple chains into one film, and `input.fetch` recovers a single chain whose own RunPod job result has expired.

Every job must also include a stable `input.cache_namespace`, derived by the trusted
backend from the authenticated user, project and clip **chain** — a cut to a new scene
gets its own namespace, so it renders with no motion context from the scene before it.
The handler hashes that value and selects a separate persistent cache directory before
ComfyUI executes the job. Jobs are serialized within each worker to prevent cache-root
switching during a run.

Queue endpoint `my_extender_endpoint` (`nqpfrj6twlaz5h`) mounts Network Volume
`0oaqjjkos5` in `EU-RO-1` and is pinned to a specific image digest rather than
the mutable `runpod-latest` tag, after tracking the tag once left the fleet
mixed mid-rollout. **Three clips across two chains have rendered, stored and
played back** through the application. The 30-minute job timeout and
300-second idle timeout allow the measured 17m 6s 0.4 MP render and warm reuse
between adjacent clips in the same chain.

See [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md) for the volume layout, image name, endpoint setup, and API request format.

---

### 🆕 Per-Clip Local References

Ref2VA clips can now use their own **local Picture, Video and Audio references** in addition to the existing global references.

Each clip gets a compact **Refs** panel where local references can be added, removed and previewed without cluttering the main card UI.

Local references use the normal H3 slot numbering and share the same limits as global references:

- up to **9 Pictures**
- up to **3 Videos**
- up to **3 Audio references**

Global references remain global exactly as before. Local references simply use the next available slots for that clip.

If a slot is already used locally, the matching global slot is automatically reserved to avoid conflicts. References coming from a Ref Pack are also handled safely without remapping or blocking generation.

Local Pictures can be edited with the same image editor as global references, and local Video/Audio references include compact preview players directly inside the Refs panel.

All local references are fully included in **Project Save/Load**, so they are restored automatically with the project.

---

### 🆕 Full Batch: Interrupt, Save & Resume

Full Batch workflows are now much safer and easier to manage.

You can interrupt a long Full Batch, keep the clips that are already computed, save the project, close ComfyUI, reload it later, and resume from where you stopped without losing completed work.

This also works with project Save/Load, so long generations can now be split across multiple sessions instead of needing to finish in one run.

Available for both **Ref2VA** and **FL2VA**.

---

## 🆕 News — Dynamic FL2VA Guides & Native VIDEO Output

### 🎯 Dynamic Image Guides for FL2VA
FL2VA clips can now use up to **3 independent image guides**, each placed at an exact `frame_idx`.

This is based on ComfyUI's native **MiniMaxH3AddGuide** support introduced in PR [#15439](https://github.com/Comfy-Org/ComfyUI/pull/15439).

You can now build a real visual timeline inside a single generation:

`First → Guide 1 → Guide 2 → Guide 3 → Last`

MiniMax H3 automatically generates the transitions between these visual anchors, opening the door to controlled transformations, pose changes, camera evolution, action staging and much more.

### 🎬 Native VIDEO Output
The **Final Decode / Preview** node now exposes a native ComfyUI `VIDEO` output.

This makes it possible to connect the final result directly to compatible video nodes such as upscalers, post-processing pipelines and video encoders, without having to reload the generated MP4 manually.

---

## 🚀 MiniMax H3 Extender 2.0.0

Version **2.0.0** is now available!

This release introduces **FL2VA support**

It also includes major **speed and memory optimizations**, with reduced RAM/VRAM usage, faster continuity handling, smoother preview behavior, and improved performance on longer projects.

Project save/load, preview restoration, clip continuity, and overall stability have also been improved.

**MiniMax H3 Extender 2.0.0** is now the new base for the project.

Thanks to everyone testing, reporting issues, and helping improve the extension ❤️

---

## 🎛️ New — Per-Clip LoRAs

Each clip card can now use its own LoRA.

- choose a LoRA directly inside the clip card
- set its strength per clip
- add multiple LoRAs to the same clip
- LoRA rows grow automatically as needed
- remove a LoRA and the list compacts automatically
- clips without LoRAs continue to use the incoming model unchanged
- global LoRAs applied before the Extender still work as before
- per-clip LoRA choices are saved with the project

This makes it possible to change style, motion or behavior from one clip to another while keeping the Extender’s normal continuity workflow.

---
## 🎬 New — Video & Audio References for MiniMax H3

The Extender now supports **MiniMax H3 video references** directly inside the workflow.

You can now use external video frames as `<Video N>` references, combine them with image references, and optionally attach the matching video audio for motion, timing, camera behavior and lip-sync guidance.

### What’s new

- up to **3 video references**
- up to **3 matching video-audio references**
- up to **3 standalone audio references**
- `<Video 1>`, `<Video 2>`, `<Video 3>` support in prompts
- stable logical video slots with no automatic remapping
- dynamic AV inputs in the node UI
- optional paired `ref_video_audio_N` inputs
- long standalone audio references are automatically sliced along the clip timeline
- video soundtracks are cropped to the effective reference-video duration
- reference-video inputs are automatically aligned to H3’s required `17k+5` frame structure
- external Prompt Pack and Reference Pack sockets remain at the bottom of the node

### Automatic FPS correction

MiniMax H3 expects reference-video frames at **24 fps**, but real source videos can be 23.976, 25, 30, 45, 50, 60 fps, etc.

The Extender now accepts the original source FPS through:

- `ref_video_fps_1`
- `ref_video_fps_2`
- `ref_video_fps_3`

The reference-video frame batch is then automatically **resampled to 24 fps while preserving the original duration** before it is sent to H3.

This fixes cases where the end of a reference video appeared to be missing because a non-24-fps image batch was previously interpreted as if it were already 24 fps.

Typical workflow:

~~~text
Load Video
   ↓
Get Video Components
   ├── images → ref_video_1
   ├── fps    → ref_video_fps_1
   └── audio  → ref_video_audio_1
~~~

### Character replacement

Video references can be combined with image references for workflows such as:

- character replacement
- motion transfer
- pose and body-performance transfer
- camera-motion transfer
- timing preservation
- lip-sync guidance from the original video audio

For example:

- `<Picture 1>` defines the new character identity
- `<Video 1>` provides the original performance, timing and camera behavior
- `ref_video_audio_1` provides the matching audio reference

This keeps the Extender’s existing internal image-reference system fully intact while adding proper H3 Ref2VA video/audio support.


**Added support for an external prompt pack through the new MiniMax H3 Prompt Pack Bridge node.**

## Reference Pack Bridge

A new node **MiniMax H3 Reference Pack Bridge** has been added.

This allows external ComfyUI `IMAGE` outputs to be injected directly into the Extender’s existing internal reference slots, while keeping the current internal reference system fully intact.

You can now mix both approaches in the same workflow:

- internal references loaded with the Extender
- external references coming from `Load Image`, crop/resize nodes, video frames, Kontext-style preprocessing, or any other `IMAGE` source
- each external input maps directly to its matching `<Picture N>` slot
- empty external slots leave the existing internal reference untouched
- reference indices are never compacted or remapped
- once imported, the external image becomes a normal internal reference with thumbnail, project persistence and Save/Load compatibility
- disconnecting the external input does not remove the imported internal reference
- unchanged external images are not rewritten on every run

The Bridge is optional and does not replace the current reference workflow. It simply adds a flexible external entry point for advanced ComfyUI pipelines.

### 🎬 Save Preview

The **Final Decode / Preview** node now includes a dedicated **Save Preview** button.

It saves the currently assembled Extender preview exactly as shown, including:

- seam handling
- assembled audio
- per-clip color corrections
- ComfyUI workflow metadata
- ComfyUI prompt metadata

The saved MP4 can be dragged back into ComfyUI to restore the workflow.

### 🎨 Real-Time Per-Clip Color Editor

Each generated clip now has its own **color editor**, accessible directly from the clip card with the `🎨` button.

You can adjust:

- Saturation
- Contrast
- Brightness

The editor provides a **live looping preview around the selected clip**, including a short part of the previous and next clips, making it much easier to visually match colors between continuations.

Corrections are:

- stored independently for every clip
- editable again at any time
- non-destructive
- preserved in `.ext` Save/Load projects
- automatically included in Preview, Full Batch and Save Preview

Color correction is applied only to the decoded video and **does not modify Motion Context, latent data or clip validation**.

A small `✓` next to the palette indicates that a clip has an active color correction.

### ⚡ Full Batch Integration

Full Batch correctly preserves and applies all previously configured clip corrections during final assembly.

You can therefore:

1. Generate several clips
2. Color-correct individual clips
3. Continue generating additional clips
4. Run Full Batch
5. Obtain the complete sequence with all previous adjustments preserved

### 💾 Save / Load Project

Per-clip color adjustments are stored inside `.ext` projects.

Loading a project restores the clip settings and color correction state, allowing you to continue exactly where you stopped.

- **Internal reference manager**

  - Add thumb editor.
  - Up to 9 image references can now be loaded directly inside the Extender.
  - No external `Load Image` nodes are required anymore.
  - References are shown as thumbnails directly in the node.
  - Reference slots stay fixed, so prompt numbering remains stable.
  - Double-click a thumbnail to view it larger.

- **Portable `.ext` projects**
  - `Save Project` now embeds the actual reference images inside the project archive.
  - `Load Project` restores prompts, refs, validation state, cache, preview and resolution settings.
  - A saved project can therefore be reopened on another machine without needing the original image files.

- **Named clip cards**
  - Each clip card can now have its own optional name, making long sequences much easier to organize.

- **Improved audio joins** (work in progress)
  - Audio is now rebuilt from PCM and encoded only once at the end instead of concatenating separate AAC streams.
  - Additional gain matching, declicking and smooth entry ramps reduce audible bumps between clips.

---

https://github.com/user-attachments/assets/a18cc6a5-2340-474e-9d3b-b784cd41584a

<img width="987" height="922" alt="Capture d&#39;écran 2026-08-31 010245" src="https://github.com/user-attachments/assets/62cf39ce-5ec4-4a69-b472-3fbafa18cb81" />


<img width="1836" height="945" alt="Capture d&#39;écran 2026-08-29 141350" src="https://github.com/user-attachments/assets/490497ef-93a0-4442-89be-fc5589491c2f" />


<img width="2307" height="1028" alt="image" src="https://github.com/user-attachments/assets/c1126ae8-2d4b-416c-a8c2-b839cd4c6b15" />


<img width="2376" height="1163" alt="Capture d&#39;écran 2026-08-20 062155" src="https://github.com/user-attachments/assets/072885ca-8faa-4d58-b525-4b12d57fe75c" />


<img width="1860" height="691" alt="image" src="https://github.com/user-attachments/assets/9f2354a4-d8b7-485d-904f-76481e8fba15" />


<img width="2048" height="1103" alt="image" src="https://github.com/user-attachments/assets/f67ba34a-5b8a-4d3b-9c18-d6314db2c873" />


<img width="2091" height="948" alt="image" src="https://github.com/user-attachments/assets/905dcd01-09f8-4dfe-9d20-2f949637f938" />


<img width="2557" height="1212" alt="Capture d&#39;écran 2026-08-15 083401" src="https://github.com/user-attachments/assets/99ca1fc4-d8b9-4662-a869-1fa06e5e58e1" />

## Keeping References Consistent Across Clips

When using image references, it is strongly recommended to place a `subject_definitions` block at the **beginning of every clip prompt**.

This helps MiniMax H3 keep the same subjects, identities, clothing, visual roles and even the environment associated with a reference image from one clip to the next.

### Example

> **subject_definitions:**  
> `<Picture 1>` is the reference image defining the exact visual appearance, identity, face, hairstyle, body proportions, clothing, accessories, and overall look of `<Subject 1>`, as well as the established environment and visual context of the scene.  
> `<Picture 2>` is the reference image defining the exact visual appearance, identity, face, hairstyle, body proportions, clothing, accessories, and overall look of `<Subject 2>`.  
> `<Subject 1>` is the exact same woman shown in `<Picture 1>`.  
> `<Subject 2>` is her friend, the exact same woman shown in `<Picture 2>`.  
> `<Subject 3>` is the same street environment and scene context established in `<Picture 1>`.

Place this `subject_definitions` block at the **very beginning of every clip prompt**.

The important point is that a reference image does not have to represent only a character.

A reference can also define the **environment, location or visual context** of the sequence.

In the example above:

- `<Picture 1>` defines the appearance of `<Subject 1>`
- `<Picture 2>` defines the appearance of `<Subject 2>`
- `<Picture 1>` also defines the street environment used as `<Subject 3>`

So the same reference image can be used both to preserve a character **and** to preserve the environment established by that image.

Repeating these definitions at the beginning of each clip prompt helps MiniMax H3 maintain the intended reference roles throughout the entire sequence.

---

## Features

- Multi-clip MiniMax H3 generation
- Continuous Motion Context between clips
- Video and audio latent continuity
- Disk-based latent cache
- Clip-by-clip generation workflow
- Full batch generation mode
- Per-clip prompt
- Per-clip seed
- Seed modes:
  - Randomize
  - Fixed
  - Increment
  - Decrement
- Per-clip duration
- Clip validation system
- Dynamic image reference inputs
- Up to **9 image references**
- Optional **audio reference**
- Shared references automatically applied to all clips
- Native MiniMax H3 Ref2VA conditioning
- Native ComfyUI sampling progress
- Final video preview
- Final video + audio export
- Seam correction between generated clips
- Audio seam correction / declick
- H.264, H.265 / HEVC and FFV1 export
- Persistent disk cache allowing generation to be resumed

---
# Clip Validation

Each clip has a **Validated** checkbox.

The validation system allows you to progressively build a long sequence without regenerating clips that have already been accepted.

When a clip is validated:

- its generated latent remains stored in the disk cache;
- the clip is locked and reused directly;
- it becomes the Motion Context source for the next clip;
- it will not be sampled again while it remains valid.

A typical workflow is:

    Clip 1 → Generate
    Clip 1 → Validate

    Clip 2 → Generate
    Clip 2 → Retry if needed
    Clip 2 → Validate

    Clip 3 → Generate
    Clip 3 → Retry if needed
    Clip 3 → Validate

This makes it possible to build a sequence one clip at a time while preserving all previously accepted generations.

Validation always forms a continuous chain from the beginning of the sequence.

For example:

    Clip 1 ✅
    Clip 2 ✅
    Clip 3 ⬜
    Clip 4 ⬜

If an earlier clip is unvalidated, every clip after it is automatically unvalidated as well, because each continuation depends on the previous generated clip.

Changing a generation parameter also invalidates the sequence when necessary. This includes changes to:

- prompt
- seed
- duration
- model
- sampling settings
- image references
- audio reference
- Motion Context settings

The affected clip and all dependent clips after it must then be regenerated.

In **clip_by_clip** mode, the Extender works on the first unvalidated clip in the sequence.

The intended workflow is therefore:

    Generate → Preview → Retry if needed → Validate → Continue

This allows long MiniMax H3 sequences to be created progressively without repeatedly regenerating clips that are already approved.

---

# Installation

## ComfyUI Manager

Search for:

    MiniMax H3 Extender

and install it directly from ComfyUI Manager.

## Manual Installation

Open a terminal in:

    ComfyUI/custom_nodes/

Then run:

    git clone https://github.com/tritant/ComfyUI_MiniMax_H3_Extender.git

Restart ComfyUI after installation.

### ℹ️ About the old Disk Join nodes

The old low-level **Motion Context Disk Join** workflow is now considered **deprecated** and is no longer actively maintained.

New workflows should use the main **MiniMax H3 Extender** node, which now handles cache management, references, trimming, validation, preview, seam correction, project Save/Load, color correction and final assembly internally.

---

Thanks again to everyone testing the node and reporting edge cases.

The Extender is becoming much more comfortable to use for long H3 sequences.

## ClipWeave integration test — 2026-09-11

The Next.js app has a real Red Signal project with seven account image references
and a four-by-five-second plan (20 seconds maximum). Project/reference persistence
passed; prompt revision exposed a schema failure and the app now requests strict
structured output. Testing is moving to local Next.js for direct server logs;
ComfyUI and GPU rendering still execute on remote RunPod. No worker code was
changed during this frontend test. Playable output, artifact ingestion, validated
prefix locking and sequential cache reuse have not yet passed end to end.

## Reproducing the end-to-end test

See the [Claude Code end-to-end runbook](https://github.com/DDstar1/Comfy__Video_Creator/blob/main/docs/e2e/README.md) for exact setup,
fixture, reference paths, browser steps, acceptance checks and debugging entry points.
Latest checkpoint: local Google sign-in and project loading passed. The connected
local prompt-revision retry still returned invalid model output after the schema
change; parser diagnostics are the next step. No render for this project has yet
been submitted. Preserve the existing four-by-five-second project (20 seconds).

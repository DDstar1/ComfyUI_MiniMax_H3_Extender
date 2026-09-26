# ClipWeave Qwen Voice Worker

Build and deploy this as a separate RunPod Serverless endpoint. Set the returned endpoint ID as `RUNPOD_QWEN_TTS_ENDPOINT_ID` on the Next.js deployment. It accepts `{ text, instruct, language, seed, filename }` and returns `output.audio` as base64 WAV.

The first model load downloads Qwen's VoiceDesign weights unless you configure an image build cache or RunPod model cache. Test this worker before publishing it: Qwen dependencies pin Transformers and must stay isolated from the MiniMax H3 image.

## Vast version

`Dockerfile.vast` builds the same worker for Vast.ai serverless. GitHub Actions
(`.github/workflows/qwen-voice-vast-image.yml`) publishes it as
`ghcr.io/ddstar1/<repo>:qwen-voice-vast-<commit>` and `:qwen-voice-vast-latest`
whenever `qwen-voice-worker/` changes on `main`.

- `vast_handler.py` runs Vast's PyWorker (`vastai==1.8.0`) in front of a local
  model API that calls `handler.py`, so both providers share one request
  contract: POST `/generate/sync` with `{"input": {text, instruct, language,
  seed, filename}}`, returning `{"audio": {...base64 WAV}}` or `{"error": ...}`.
- The worker reports ready only after the model is loaded on the GPU. Weights
  download to `/workspace/huggingface` on first boot.
- Like the H3 template, the Vast template needs `-p 3000:3000 -e WORKER_PORT=3000`.
- Not yet deployed: there is no Vast voice endpoint, and the Next.js app still
  calls the RunPod endpoint (`RUNPOD_QWEN_TTS_ENDPOINT_ID`).

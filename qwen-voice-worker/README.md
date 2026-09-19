# ClipWeave Qwen Voice Worker

Build and deploy this as a separate RunPod Serverless endpoint. Set the returned endpoint ID as `RUNPOD_QWEN_TTS_ENDPOINT_ID` on the Next.js deployment. It accepts `{ text, instruct, language, seed, filename }` and returns `output.audio` as base64 WAV.

The first model load downloads Qwen's VoiceDesign weights unless you configure an image build cache or RunPod model cache. Test this worker before publishing it: Qwen dependencies pin Transformers and must stay isolated from the MiniMax H3 image.

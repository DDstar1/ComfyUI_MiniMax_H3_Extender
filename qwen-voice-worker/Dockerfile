FROM runpod/worker-comfyui:5.10.0-base

# Keep the ComfyUI node available for visual workflow iteration, while the
# serverless handler uses the same official Qwen package for a small, stable
# request/response contract.
RUN git clone --depth 1 https://github.com/DarioFT/ComfyUI-Qwen3-TTS.git /comfyui/custom_nodes/ComfyUI-Qwen3-TTS \
    && uv pip install --no-cache -r /comfyui/custom_nodes/ComfyUI-Qwen3-TTS/requirements.txt
COPY handler.py /handler.py
ENV QWEN_MODEL_ID=Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
CMD ["python", "-u", "/handler.py"]

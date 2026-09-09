# syntax=docker/dockerfile:1

ARG WORKER_COMFYUI_VERSION=5.10.0
FROM runpod/worker-comfyui:${WORKER_COMFYUI_VERSION}-base

COPY . /comfyui/custom_nodes/ComfyUI_MiniMax_H3_Extender

# Keep the official handler implementation and wrap it with support for video
# artifacts emitted by the Extender.
RUN mv /handler.py /runpod_base_handler.py
COPY runpod_handler.py /handler.py

# The upstream worker launches ComfyUI from /opt/venv. Install the node's
# dependencies into that same environment, then fail the build if node imports
# break ComfyUI startup.
RUN uv pip install --no-cache -r /comfyui/custom_nodes/ComfyUI_MiniMax_H3_Extender/requirements.txt \
    && cd /comfyui \
    && timeout 300 python main.py --quick-test-for-ci --cpu

# Cache generated motion context on the attached Network Volume so a warm or
# replacement worker can reuse validated clip state.
ENV H3_CACHE_ROOT=/runpod-volume/comfytr-cache

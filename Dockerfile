# syntax=docker/dockerfile:1

ARG WORKER_COMFYUI_VERSION=5.10.0
FROM runpod/worker-comfyui:${WORKER_COMFYUI_VERSION}-base

# Serverless workers normally expose only the web terminal. Install an SSH
# daemon for direct diagnostics; its authorized key is supplied at runtime by
# the endpoint template, never baked into this public image.
RUN apt-get -o Acquire::Retries=5 update \
    && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=5 install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /root/.ssh \
    && chmod 700 /root/.ssh \
    && printf '%s\n' \
      'PermitRootLogin prohibit-password' \
      'PasswordAuthentication no' \
      'KbdInteractiveAuthentication no' \
      'ChallengeResponseAuthentication no' \
      'UsePAM no' \
      'X11Forwarding no' \
      >> /etc/ssh/sshd_config

COPY start-with-ssh.sh comfyui-web-proxy.py /start-with-ssh.sh /
RUN chmod 755 /start-with-ssh.sh

# RunPod maps these ports from the endpoint template. ComfyUI itself is started
# by the inherited worker entrypoint on 8188; sshd is started by our wrapper.
EXPOSE 8188 8189 22

COPY . /comfyui/custom_nodes/ComfyUI_MiniMax_H3_Extender

# Keep the official handler implementation and wrap it with support for video
# artifacts emitted by the Extender.
RUN mv /handler.py /runpod_base_handler.py
COPY runpod_handler.py /handler.py

# ComfyUI auto-loads extra_model_paths.yaml from its base directory. Append
# rather than replace: the base worker may already configure paths there, and
# overwriting the file would drop the stock /runpod-volume/models layout. The
# leading newline protects against an existing file with no trailing newline.
# This runs before the startup check below so a malformed file fails the build
# instead of reaching a worker.
COPY extra_model_paths.runpod-volume.yaml /tmp/extra_model_paths.runpod-volume.yaml
RUN printf '\n' >> /comfyui/extra_model_paths.yaml \
    && cat /tmp/extra_model_paths.runpod-volume.yaml >> /comfyui/extra_model_paths.yaml \
    && rm /tmp/extra_model_paths.runpod-volume.yaml \
    && echo "--- resulting /comfyui/extra_model_paths.yaml ---" \
    && cat /comfyui/extra_model_paths.yaml

# The upstream worker launches ComfyUI from /opt/venv. Install the node's
# dependencies into that same environment, then fail the build if node imports
# break ComfyUI startup.
RUN uv pip install --no-cache -r /comfyui/custom_nodes/ComfyUI_MiniMax_H3_Extender/requirements.txt \
    && cd /comfyui \
    && timeout 300 python main.py --quick-test-for-ci --cpu

# Cache generated motion context on the attached Network Volume so a warm or
# replacement worker can reuse validated clip state.
ENV H3_CACHE_ROOT=/runpod-volume/comfytr-cache
ENV H3_CACHE_ROOT_FILE=/tmp/comfytr-h3-cache-root

# Preserve the base image's NVIDIA entrypoint while replacing its /start.sh
# command with a small wrapper that starts sshd first.
CMD ["bash", "/start-with-ssh.sh"]

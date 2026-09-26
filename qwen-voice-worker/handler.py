"""Separate RunPod endpoint for admin-only Qwen VoiceDesign jobs."""
import base64
import os
import tempfile
from pathlib import Path

import runpod
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

_model = None
def model():
    global _model
    if _model is None:
        _model = Qwen3TTSModel.from_pretrained(os.environ['QWEN_MODEL_ID'], device_map='cuda:0', dtype='bfloat16')
    return _model

def handler(job):
    data = job.get('input', {})
    text, instruct, language = (str(data.get(k, '')).strip() for k in ('text', 'instruct', 'language'))
    seed = data.get('seed')
    if not text or not instruct or not language or not isinstance(seed, int):
        return {'error': 'text, instruct, language, and integer seed are required'}
    # Qwen forwards unknown generation kwargs to Transformers; seed is set in
    # PyTorch rather than passed to generate_voice_design.
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    wavs, sample_rate = model().generate_voice_design(text=text, instruct=instruct, language=language)
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as output:
        path = Path(output.name)
    try:
        sf.write(path, wavs[0], sample_rate, subtype='PCM_16')
        return {'audio': {'filename': str(data.get('filename') or 'voice.wav'), 'type': 'base64', 'data': base64.b64encode(path.read_bytes()).decode('ascii')}}
    finally:
        path.unlink(missing_ok=True)

# The Vast adapter imports this module, so only start RunPod when run directly.
if __name__ == '__main__':
    runpod.serverless.start({'handler': handler})

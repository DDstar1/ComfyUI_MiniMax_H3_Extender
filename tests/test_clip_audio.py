"""Exercise clip extraction without importing GPU/RunPod dependencies.

Run: python -m unittest discover -s tests
Set FFMPEG_BINARY to run the real two-tone A/V regression as well.
"""
import ast
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest


def handler_functions():
    path = Path(__file__).resolve().parents[1] / "runpod_handler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_segment_preview_offset", "_decoded_clip_audio_offset", "_sync_audio_output_to_chain"}
    module = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
    scope = dict(Path=Path, json=json, re=re, os=os, math=math, subprocess=subprocess)
    exec(compile(module, str(path), "exec"), scope)
    return scope


class ClipAudioTests(unittest.TestCase):
    def test_decoder_timeline_handles_real_h3_frame_counts(self):
        info = {"clip": 2, "color_timeline": [
            {"index": 0, "start": 0, "end": 10.125},
            {"index": 1, "start": 10.125, "end": 464 / 24},
        ]}
        offset = handler_functions()["_decoded_clip_audio_offset"](
            Path("ref2va_0001.mp4"), 19.334, 221 / 24, info)
        self.assertEqual(offset, 10.125)

    def test_restored_first_clip_cannot_be_delivered_as_second_clip(self):
        info = {"clip": 2, "color_timeline": [{"index": 1, "start": 10.125, "end": 464 / 24}]}
        with self.assertRaisesRegex(RuntimeError, "Restored cache contains clip 1"):
            handler_functions()["_decoded_clip_audio_offset"](
                Path("ref2va_0000.mp4"), 19.334, 10.125, info)

    def test_extender_rechecks_external_disk_state_for_identical_inputs(self):
        path = Path(__file__).resolve().parents[1] / "extender.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MiniMaxH3Extender")
        method = next(method for method in node.body if isinstance(method, ast.FunctionDef) and method.name == "IS_CHANGED")
        cls = ast.ClassDef(name="Extender", bases=[], keywords=[], body=[method], decorator_list=[])
        scope = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), "exec"), scope)
        first = scope["Extender"].IS_CHANGED(clips_json="same inputs")
        second = scope["Extender"].IS_CHANGED(clips_json="same inputs")
        self.assertNotEqual(first, second)

    def test_offset_accounts_for_prior_overlap_trims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chain_extender_1.json").write_text(json.dumps({"fps": 24, "segments": [
                {"frames": 240, "trim_frames": 0}, {"frames": 262, "trim_frames": 22}, {"frames": 262, "trim_frames": 22}]}))
            self.assertEqual(handler_functions()["_segment_preview_offset"](root, 2), 20)

    def test_bad_timeline_does_not_overwrite_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ref2va_0001.mp4"
            source = Path(directory) / "assembled.mp4"
            target.write_bytes(b"original-video")
            scope = handler_functions()
            selection = []
            def select_segment(namespace, *, ensure_audio=True):
                selection.append(ensure_audio)
                if ensure_audio:
                    raise AssertionError("Legacy preview mux must not touch a fresh segment")
                return target, None
            scope.update(_has_audio_stream=lambda _: True, _latest_chain_segment=select_segment,
                         _ffprobe_duration=lambda path: 10 if path == target else 30)
            with self.assertRaisesRegex(RuntimeError, "timeline does not match"):
                scope["_sync_audio_output_to_chain"]("test", source)
            self.assertEqual(target.read_bytes(), b"original-video")
            self.assertEqual(selection, [False])

    @unittest.skipUnless(os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg"), "FFmpeg required for media regression")
    def test_cumulative_output_yields_only_latest_video_and_tone(self):
        ffmpeg = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg")

        def run(*args):
            return subprocess.run([ffmpeg, "-v", "error", "-y", *args], capture_output=True, check=True).stdout

        def duration(path):
            probe = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True)
            match = re.search(rb"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
            return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])

        def has_audio(path):
            return b"Audio:" in subprocess.run([ffmpeg, "-i", str(path)], capture_output=True).stderr

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            segment_dir = cache / "chain_extender_1.final.video"
            segment_dir.mkdir(parents=True)
            (cache / "chain_extender_1.json").write_text(json.dumps({"fps": 24, "segments": [
                {"frames": 24, "trim_frames": 0}, {"frames": 46, "trim_frames": 22}]}))
            parts = []
            for index, (color, frequency) in enumerate((("red", 440), ("blue", 880))):
                part = root / f"part{index}.mp4"
                run("-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=24:d=1", "-f", "lavfi", "-i",
                    f"sine=frequency={frequency}:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(part))
                parts.append(part)
            listing = root / "concat.txt"
            listing.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts))
            source = root / "assembled.mp4"
            run("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(source))
            target = segment_dir / "ref2va_0001.mp4"
            run("-i", str(parts[1]), "-an", "-c:v", "copy", str(target))
            expected_pixels = run("-i", str(target), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
            scope = handler_functions()
            scope.update(_has_audio_stream=has_audio, _ffprobe_duration=duration,
                         _latest_chain_segment=lambda _, **kwargs: (target, None), _find_ffmpeg=lambda: ffmpeg)
            self.assertEqual(scope["_sync_audio_output_to_chain"]("test", source), target)
            self.assertAlmostEqual(duration(target), 1, delta=0.1)
            # The current decoder supplies boundaries for this exact output.
            info = {"clip": 2, "color_timeline": [{"index": 1, "start": 1.0, "end": 2.0}]}
            scope["_sync_audio_output_to_chain"]("test", source, info)
            self.assertAlmostEqual(duration(target), 1, delta=0.1)
            self.assertEqual(run("-i", str(target), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"), expected_pixels)
            audio = run("-i", str(target), "-ss", "0.2", "-t", "0.5", "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1")
            samples = struct.unpack(f"<{len(audio)//4}f", audio)
            crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
            self.assertAlmostEqual(crossings / (len(samples) / 8000), 880, delta=30)
            # A standalone output is valid too and must not seek into history.
            scope["_sync_audio_output_to_chain"]("test", parts[1])
            self.assertAlmostEqual(duration(target), 1, delta=0.1)


if __name__ == "__main__":
    unittest.main()

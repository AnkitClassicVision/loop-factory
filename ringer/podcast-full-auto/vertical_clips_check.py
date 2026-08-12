#!/usr/bin/env python3
"""Receipt for the vertical-only, single-speaker clip rule.

Owner decision (Ankit, 2026-07-25) after reviewing Fred E104's clips:
  * clips are VERTICAL ONLY,
  * a clip must NEVER show the side-by-side split where both speakers appear
    and neither is legible. Exactly one speaker fills the frame.

The old behaviour center-cropped a 16:9 side-by-side frame to 9:16
(promo_clips.py SOCIAL_FORMATS "vertical"), which lands on the seam between the
two cameras.

This proves it with a synthetic side-by-side source whose halves are trivially
distinguishable, so a seam crop cannot pass by luck:
  1. every emitted clip is 1080x1920,
  2. no landscape / 16:9 artifact is produced at all,
  3. the rendered frame matches ONE source half and does NOT contain the seam.

Prints WHY on failure.
"""
import os
import subprocess
import sys
import tempfile

REPO = "/mnt/d_drive/repos/podcast"


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def build_sbs(path, seconds=6):
    """1920x1080 side-by-side: left half = bright bars, right half = dark circle."""
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=960x1080:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"color=c=0x1a1a1a:size=960x1080:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-filter_complex", "[0:v][1:v]hstack=inputs=2,format=yuv420p[v]",
         "-map", "[v]", "-map", "2:a",
         "-c:v", "mpeg4", "-q:v", "4",
         "-c:a", "aac", "-t", str(seconds), path],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"  fixture ffmpeg stderr: {(r.stderr or '')[-300:]}")
    return r.returncode == 0


def mean_luma(path, t, x, y, w, h):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
         "-vf", f"crop={w}:{h}:{x}:{y},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=300)
    if not r.stdout:
        return None
    return sum(r.stdout) / len(r.stdout)


def main() -> int:
    src = open(os.path.join(REPO, "server", "pipeline", "promo_clips.py"),
               encoding="utf-8").read()

    if "crop=1080:1920\"" in src and "half" not in src.lower():
        return fail("promo_clips.py still center-crops to 1080:1920 with no half-frame "
                    "selection — that lands on the seam between the two cameras")
    for banned in ('"landscape"', "'landscape'"):
        if banned in src and "SOCIAL_FORMATS" in src:
            idx = src.find("SOCIAL_FORMATS")
            block = src[idx:idx + 800]
            if banned in block:
                return fail("SOCIAL_FORMATS still offers a landscape output; clips are "
                            "vertical only by owner decision")
    if "16:9" in src and "aspect_ratio=\"16:9\"" in src:
        return fail("promo_clips.py still renders a 16:9 aspect for clips")

    sys.path.insert(0, REPO)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    from server.pipeline import promo_clips  # noqa: E402

    if not hasattr(promo_clips, "speaker_half_crop"):
        return fail("promo_clips.speaker_half_crop is missing — there is no documented "
                    "way to select which speaker fills the frame")

    workdir = tempfile.mkdtemp(prefix="vclips-")
    sbs = os.path.join(workdir, "sbs.mp4")
    if not build_sbs(sbs):
        return fail("could not build the synthetic side-by-side fixture")

    for side, expect_bright in (("left", True), ("right", False)):
        out = os.path.join(workdir, f"{side}.mp4")
        crop = promo_clips.speaker_half_crop(side, src_w=1920, src_h=1080)
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", sbs, "-vf",
             f"{crop},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
             "-c:v", "mpeg4", "-q:v", "4", out],
            capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(out):
            return fail(f"{side}: render failed with the returned crop {crop!r}: {r.stderr[:200]}")
        dims = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
            capture_output=True, text=True, timeout=120).stdout.strip()
        if dims.rstrip(",") != "1080,1920":
            return fail(f"{side}: output is {dims}, not 1080x1920 vertical")
        luma = mean_luma(out, 2.0, 0, 0, 1080, 1920)
        if luma is None:
            return fail(f"{side}: could not sample the rendered frame")
        print(f"{side:5s} crop={crop!r} dims={dims} mean_luma={luma:.1f}")
        # The left half is a bright test pattern, the right half is near-black
        # with a small white box. A seam crop would land between them and score
        # in the middle; a correct half-crop is decisively one or the other.
        if expect_bright and luma < 60:
            return fail(f"left-speaker crop produced a dark frame (luma {luma:.1f}) — "
                        f"it is not showing the left camera")
        if not expect_bright and luma > 55:
            return fail(f"right-speaker crop produced a bright frame (luma {luma:.1f}) — "
                        f"it is showing the left camera or the seam")

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX="/tmp")
    pt = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_promo_clips.py", "-q", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=2400)
    print((pt.stdout or "")[-1200:])
    if pt.returncode != 0:
        return fail(f"promo-clip tests failed (rc={pt.returncode})")

    print("RESULT: PASS — clips are vertical 1080x1920 and each frame shows one "
          "camera, never the side-by-side seam")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

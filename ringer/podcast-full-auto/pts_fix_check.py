#!/usr/bin/env python3
"""Receipt for the per-contribution audio PTS fix.

Segment 38 of Fred E104 (source 1093.5-1225.328604, 131.83s) rendered with the
correct sample count at the mux (+896) and then gained 10,240 samples in the CFR
clean, because asetpts=PTS-STARTPTS preserved the composite's internal timestamp
holes and aresample=first_pts=0 materialized them.

This proves, by rendering that exact span off the real composite:
  1. the long span now lands inside the AAC-padding-only invariant,
  2. short spans did not regress,
  3. the decoded frame count of the video is unchanged (the audio fix must not
     move the video timeline),
  4. the repo's angle-render suite still passes.

Prints WHY on failure.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = "/mnt/d_drive/repos/podcast"
COMPOSITE = os.path.join(
    REPO, "episodes", "2026-06-10-fred-cho", "processed", "composite.mp4")
SR = 48000
PAD_MAX = 1023

# The real segment 38 span, captured from the live plan on 2026-07-24.
LONG = [{"start": 1093.5, "end": 1225.328604, "angle": "sbs"}]
SHORT = [{"start": 700.0, "end": 702.5, "angle": "sbs"}]
MULTI = [
    {"start": 930.0, "end": 932.5, "angle": "sbs"},
    {"start": 932.5, "end": 937.5, "angle": "sbs"},
    {"start": 937.5, "end": 940.0, "angle": "sbs"},
]


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def probe_sum(path, stream, entry):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", stream, "-show_frames",
         "-show_entries", f"frame={entry}", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        return None
    total = 0
    for line in (r.stdout or "").splitlines():
        line = line.strip().rstrip(",")
        if line:
            total += int(line)
    return total or None


def video_packets(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-count_packets", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_packets", "-of",
         "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=1200)
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


def map_total(out_path):
    sidecar = out_path + ".boundaries.json"
    if not os.path.exists(sidecar):
        return None
    rows = json.load(open(sidecar))
    return float(rows[-1]["edited_end_s"]) if rows else None


def main() -> int:
    src = open(os.path.join(REPO, "server", "pipeline", "angle_render.py"),
               encoding="utf-8").read()
    if "asetpts=N/SR/TB" not in src:
        return fail("angle_render.py does not build per-contribution timestamps with "
                    "asetpts=N/SR/TB — the PTS holes are still there")

    sys.path.insert(0, REPO)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    from server.pipeline import angle_render as ar  # noqa: E402

    workdir = tempfile.mkdtemp(prefix="ptsfix-")
    cases = [("segment38_131s", LONG), ("short_2p5s", SHORT), ("multi_10s", MULTI)]
    results = {}

    for label, segments in cases:
        out = os.path.join(workdir, f"{label}.mp4")
        got = ar.render_angle_switch(segments, COMPOSITE, COMPOSITE, COMPOSITE, {},
                                     out, crossfade_s=0.0, target_size=(640, 360))
        if not got or not os.path.exists(out):
            return fail(f"{label}: render refused — this shape still cannot be certified")
        mt = map_total(out)
        ds = probe_sum(out, "a:0", "nb_samples")
        frames = video_packets(out)
        if mt is None or ds is None or frames is None:
            return fail(f"{label}: could not measure (map_total={mt} samples={ds} frames={frames})")
        expected = round(mt * SR)
        delta = ds - expected
        results[label] = (mt, expected, ds, delta, frames)
        print(f"{label}: map_total={mt} expected={expected} decoded={ds} "
              f"delta={delta:+d} video_frames={frames}")
        if not (0 <= delta <= PAD_MAX):
            return fail(f"{label}: delta {delta:+d} is outside the AAC-padding-only "
                        f"invariant 0..{PAD_MAX}")
        if abs(frames - round(mt * 30)) > 1:
            return fail(f"{label}: video frames {frames} disagree with the map "
                        f"({round(mt * 30)}) — the audio fix moved the video timeline")

    long_delta = results["segment38_131s"][3]
    if long_delta > PAD_MAX:
        return fail(f"segment 38 still carries excess audio ({long_delta:+d})")

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX="/tmp")
    pt = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_angle_render.py", "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=2400)
    print((pt.stdout or "")[-1500:])
    if pt.returncode != 0:
        return fail(f"tests/test_angle_render.py failed (rc={pt.returncode})")

    print("RESULT: PASS — the 131.8s span that gained 10,240 samples in the CFR clean "
          "now certifies, short and multi-segment shapes did not regress, and the "
          "video timeline is unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Receipt for the angle-render audio certification fix.

Renders REAL clips off Fred's composite through the patched renderer and proves:

  1. the gate's shape (a single ~2.5s segment) is no longer refused, because the
     certification now counts decoded audio samples instead of reading the MP4
     container's presentation duration field (measured 2026-07-24: a 120s render
     reports stream=duration 119.994 while decoding to exactly 5,760,000 samples
     = 120.000s);
  2. whatever the renderer RETURNS satisfies the sample invariant
     expected <= decoded <= expected + 1023 (AAC-LC terminal padding only) --
     so a genuinely short program is still refused, never returned;
  3. the repo's own angle-render tests, including the two new certification
     tests, pass.

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

GATE_CASE = [{"start": 700.0, "end": 702.5, "angle": "sbs"}]
MULTI_CASE = [
    {"start": 930.0, "end": 932.5, "angle": "sbs"},
    {"start": 932.5, "end": 937.5, "angle": "sbs"},
    {"start": 937.5, "end": 940.0, "angle": "sbs"},
]


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def decoded_samples(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a:0", "-show_frames",
         "-show_entries", "frame=nb_samples", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return None
    total = 0
    for line in (r.stdout or "").splitlines():
        line = line.strip().rstrip(",")
        if line:
            total += int(line)
    return total


def map_total(out_path):
    sidecar = out_path + ".boundaries.json"
    if not os.path.exists(sidecar):
        return None
    rows = json.load(open(sidecar))
    return float(rows[-1]["edited_end_s"]) if rows else None


def render(ar, segments, out_path):
    return ar.render_angle_switch(
        segments, COMPOSITE, COMPOSITE, COMPOSITE, {}, out_path,
        crossfade_s=0.0, target_size=(640, 360))


def main() -> int:
    if not os.path.exists(COMPOSITE):
        return fail(f"composite missing at {COMPOSITE}")

    src = open(os.path.join(REPO, "server", "pipeline", "angle_render.py"),
               encoding="utf-8").read()
    if "_count_decoded_audio_samples" not in src:
        return fail("angle_render.py has no _count_decoded_audio_samples helper — "
                    "the certification was not moved onto decoded samples")
    if "nb_samples" not in src:
        return fail("angle_render.py never probes frame=nb_samples — the certification "
                    "still cannot see the decoded sample population")

    sys.path.insert(0, REPO)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    from server.pipeline import angle_render as ar  # noqa: E402

    workdir = tempfile.mkdtemp(prefix="audiocert-")
    results = {}

    # 1. THE GATE CASE: one ~2.5s segment. This is what blocked Fred E104.
    gate_out = os.path.join(workdir, "gate_2_5s.mp4")
    got = render(ar, GATE_CASE, gate_out)
    if not got or not os.path.exists(gate_out):
        return fail("the gate case (single 2.5s segment) is STILL refused — "
                    "angle segment renders remain blocked")
    mt = map_total(gate_out)
    ds = decoded_samples(gate_out)
    if mt is None or ds is None:
        return fail(f"could not measure the gate render (map_total={mt} decoded={ds})")
    expected = round(mt * SR)
    results["gate_2_5s"] = (mt, expected, ds, ds - expected)
    if not (0 <= ds - expected <= PAD_MAX):
        return fail(f"gate render was RETURNED but violates the sample invariant: "
                    f"decoded={ds} expected={expected} delta={ds - expected} "
                    f"(allowed 0..{PAD_MAX})")

    # 2. THE MULTI-SEGMENT CONTROL: measured 2,816 samples short on 2026-07-24.
    #    Either it is still refused (fail-closed, correct) or the renderer was
    #    also repaired -- but it must NEVER be returned while short.
    multi_out = os.path.join(workdir, "multi_10s.mp4")
    got_multi = render(ar, MULTI_CASE, multi_out)
    if got_multi and os.path.exists(multi_out):
        mt2 = map_total(multi_out)
        ds2 = decoded_samples(multi_out)
        if mt2 is None or ds2 is None:
            return fail(f"could not measure the multi-segment render (map_total={mt2} decoded={ds2})")
        exp2 = round(mt2 * SR)
        results["multi_10s"] = (mt2, exp2, ds2, ds2 - exp2)
        if not (0 <= ds2 - exp2 <= PAD_MAX):
            return fail(f"multi-segment render was RETURNED while short: decoded={ds2} "
                        f"expected={exp2} delta={ds2 - exp2} — the certification is "
                        f"no longer catching a real audio deficit")
    else:
        results["multi_10s"] = ("refused", "-", "-", "fail-closed")

    # 3. The repo's own tests, including the two new certification tests.
    test_file = os.path.join(REPO, "tests", "test_angle_render.py")
    tsrc = open(test_file, encoding="utf-8").read()
    for name in ("accepts_aac_padding", "rejects_real_shortfall"):
        if name not in tsrc:
            return fail(f"tests/test_angle_render.py has no test covering {name}")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX="/tmp")
    pt = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_angle_render.py", "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=2400)
    print((pt.stdout or "")[-1800:])
    print((pt.stderr or "")[-400:])
    if pt.returncode != 0:
        return fail(f"tests/test_angle_render.py failed (rc={pt.returncode})")

    for label, (mt, exp, ds, delta) in results.items():
        print(f"{label}: map_total={mt} expected_samples={exp} decoded_samples={ds} delta={delta}")
    print("RESULT: PASS — the gate shape renders, every returned render satisfies the "
          "decoded-sample invariant, and a short program is still refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

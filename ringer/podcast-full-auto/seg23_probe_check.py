#!/usr/bin/env python3
"""Receipt for the angle-segment-23 audio-vs-map diagnosis.

Proves the worker actually RENDERED and PROBED short and long single-segment
angle renders instead of reasoning about them: probe.json must carry >=3
experiments with internally consistent measured numbers (decoded sample count
must match the decoded duration it reports), probe.log must contain the raw
ffprobe sample-count evidence, and report.md must carry the required sections
with file:line citations. Prints WHY on failure.
"""
import json
import os
import re
import sys

REQUIRED_NUM_KEYS = [
    "source_start_s",
    "requested_duration_s",
    "final_video_frames",
    "map_total_s",
    "container_audio_duration_s",
    "decoded_audio_samples",
    "decoded_audio_duration_s",
]
SECTIONS = [
    "## what was measured",
    "## root cause",
    "## is the check or the render wrong",
    "## fix design",
    "## proof command",
    "## risks",
]


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def main() -> int:
    for name in ("probe.json", "probe.log", "report.md"):
        if not os.path.exists(name):
            return fail(f"missing required artifact {name}")

    try:
        probe = json.load(open("probe.json"))
    except Exception as exc:  # noqa: BLE001
        return fail(f"probe.json is not valid JSON: {exc}")

    exps = probe.get("experiments")
    if not isinstance(exps, list) or len(exps) < 3:
        return fail(f"probe.json needs >=3 experiments, got {exps if not isinstance(exps, list) else len(exps)}")

    for i, e in enumerate(exps):
        if not isinstance(e, dict):
            return fail(f"experiment {i} is not an object")
        for k in REQUIRED_NUM_KEYS:
            v = e.get(k)
            if not isinstance(v, (int, float)):
                return fail(f"experiment {i} missing numeric key {k} (got {v!r})")
        # Internal consistency: a fabricated number set will not survive this.
        expected = float(e["decoded_audio_samples"]) / 48000.0
        if abs(expected - float(e["decoded_audio_duration_s"])) > 1e-3:
            return fail(
                f"experiment {i} inconsistent: decoded_audio_samples/48000="
                f"{expected:.6f} but decoded_audio_duration_s="
                f"{float(e['decoded_audio_duration_s']):.6f}"
            )

    durations = [float(e["requested_duration_s"]) for e in exps]
    if not any(d <= 3.0 for d in durations):
        return fail(f"no SHORT (<=3s) experiment — that is the failing case; durations={durations}")
    if not any(d >= 60.0 for d in durations):
        return fail(f"no LONG (>=60s) experiment — needed to tell fixed offset from accumulating drift; durations={durations}")

    verdict = probe.get("verdict")
    if not isinstance(verdict, str) or len(verdict.split()) < 3:
        return fail(f"probe.json needs a verdict sentence, got {verdict!r}")

    log = open("probe.log", encoding="utf-8", errors="replace").read()
    if "nb_read_samples" not in log:
        return fail("probe.log has no nb_read_samples evidence — decoded sample counts were not actually probed")
    if "render_angle_switch" not in log and "angle_render" not in log:
        return fail("probe.log shows no angle_render invocation")

    text = open("report.md", encoding="utf-8", errors="replace").read()
    low = text.lower()
    missing = [s for s in SECTIONS if s not in low]
    if missing:
        return fail(f"report.md missing sections: {missing}")
    cites = len(re.findall(r"[A-Za-z0-9_/.-]+\.(?:py|json|md):?\d*", text))
    if cites < 8:
        return fail(f"report.md has only {cites} file citations (need >=8)")
    if "angle_render.py" not in text:
        return fail("report.md never cites angle_render.py")
    if not any(t in low for t in ("fail-closed", "fail closed", "deny-by-default")):
        return fail("report.md never addresses the fail-closed guarantee")
    if len(text.split()) < 400:
        return fail(f"report.md is only {len(text.split())} words (need >400)")

    print(f"experiments: {len(exps)} durations={durations}")
    for e in exps:
        print(
            f"  d={float(e['requested_duration_s']):.3f}s frames={e['final_video_frames']} "
            f"map={float(e['map_total_s']):.6f} container_audio={float(e['container_audio_duration_s']):.6f} "
            f"decoded_audio={float(e['decoded_audio_duration_s']):.6f} "
            f"(container-map={float(e['container_audio_duration_s'])-float(e['map_total_s']):+.6f}, "
            f"decoded-map={float(e['decoded_audio_duration_s'])-float(e['map_total_s']):+.6f})"
        )
    print(f"verdict: {verdict}")
    print(f"report.md: {cites} citations, {len(text.split())} words")
    print("RESULT: PASS — measured, self-consistent probe plus a grounded report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

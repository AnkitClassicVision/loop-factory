#!/usr/bin/env python3
"""Receipt for the segment-38 audio-longer-than-map diagnosis.

Segment 23 now renders (decoded-sample certification fix) and Fred E104 fails
further along: a ~132s single-segment gate render whose audio decodes 11,136
samples (232ms) LONGER than its frame-count map. This receipt proves the worker
identified the failing segment concretely and reproduced the excess with
measurements rather than reasoning: plan.json must name segment 38's real span
and angle, and probe.json must carry measured renders including at least one
that reproduces a >1023-sample excess.

Prints WHY on failure.
"""
import json
import os
import re
import sys

NUM_KEYS = ["requested_duration_s", "map_total_s", "expected_samples",
            "decoded_samples", "delta_samples"]
SECTIONS = ["## the failing segment", "## what was measured",
            "## root cause", "## fix design", "## proof command", "## risks"]


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def main() -> int:
    for name in ("plan.json", "probe.json", "probe.log", "report.md"):
        if not os.path.exists(name):
            return fail(f"missing required artifact {name}")

    try:
        plan = json.load(open("plan.json"))
    except Exception as exc:  # noqa: BLE001
        return fail(f"plan.json is not valid JSON: {exc}")
    seg = plan.get("segment_38")
    if not isinstance(seg, dict):
        return fail("plan.json has no segment_38 object — the failing segment was never identified")
    for key in ("start", "end", "angle"):
        if key not in seg:
            return fail(f"plan.json segment_38 missing {key}")
    try:
        span = float(seg["end"]) - float(seg["start"])
    except (TypeError, ValueError):
        return fail(f"plan.json segment_38 start/end are not numeric: {seg}")
    if span <= 0:
        return fail(f"plan.json segment_38 span is not positive: {span}")
    total = plan.get("total_segments")
    if not isinstance(total, int) or total <= 38:
        return fail(f"plan.json total_segments must be an int > 38, got {total!r}")

    try:
        probe = json.load(open("probe.json"))
    except Exception as exc:  # noqa: BLE001
        return fail(f"probe.json is not valid JSON: {exc}")
    exps = probe.get("experiments")
    if not isinstance(exps, list) or len(exps) < 2:
        n = len(exps) if isinstance(exps, list) else exps
        return fail(f"probe.json needs >=2 measured experiments, got {n}")

    for i, e in enumerate(exps):
        if not isinstance(e, dict):
            return fail(f"experiment {i} is not an object")
        for k in NUM_KEYS:
            if not isinstance(e.get(k), (int, float)):
                return fail(f"experiment {i} missing numeric {k} (got {e.get(k)!r})")
        if e["delta_samples"] != e["decoded_samples"] - e["expected_samples"]:
            return fail(
                f"experiment {i} inconsistent: delta_samples={e['delta_samples']} but "
                f"decoded-expected={e['decoded_samples'] - e['expected_samples']}")

    reproduced = [e for e in exps if e["delta_samples"] > 1023]
    if not reproduced:
        return fail("no experiment reproduced the >1023-sample excess — the defect was "
                    "never actually observed, so any fix is unproven")

    verdict = probe.get("verdict")
    if not isinstance(verdict, str) or len(verdict.split()) < 3:
        return fail(f"probe.json needs a verdict sentence, got {verdict!r}")

    log = open("probe.log", encoding="utf-8", errors="replace").read()
    if "nb_samples" not in log:
        return fail("probe.log has no nb_samples evidence — sample counts were not probed")

    text = open("report.md", encoding="utf-8", errors="replace").read()
    low = text.lower()
    missing = [s for s in SECTIONS if s not in low]
    if missing:
        return fail(f"report.md missing sections: {missing}")
    cites = len(re.findall(r"[A-Za-z0-9_/.-]+\.(?:py|json|md):?\d*", text))
    if cites < 6:
        return fail(f"report.md has only {cites} file citations (need >=6)")
    if not any(t in low for t in ("fail-closed", "fail closed", "deny-by-default")):
        return fail("report.md never addresses the fail-closed guarantee")
    if len(text.split()) < 350:
        return fail(f"report.md is only {len(text.split())} words (need >350)")

    print(f"segment_38: start={seg['start']} end={seg['end']} span={span:.3f}s "
          f"angle={seg['angle']} (of {total} planned segments)")
    for e in exps:
        print(f"  {e.get('label', '?')}: dur={float(e['requested_duration_s']):.3f}s "
              f"expected={e['expected_samples']} decoded={e['decoded_samples']} "
              f"delta={e['delta_samples']:+d}")
    print(f"reproduced the excess in {len(reproduced)} experiment(s)")
    print(f"verdict: {verdict}")
    print("RESULT: PASS — failing segment identified and its excess reproduced by measurement")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

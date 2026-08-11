#!/usr/bin/env python3
"""Receipt for the multi-segment mux-truncation experiment.

The 2026-07-24 diagnosis measured a REAL deficit in the n>1 path: a 3-segment
contiguous 10s program whose one-pass audio filter emits exactly 480,000 PCM
samples decodes to 477,184 samples after the mux stage (-2,816). This receipt
proves the worker actually BUILT and MEASURED alternative mux strategies rather
than reasoning about them: variants.json must carry >=3 measured variants with
self-consistent numbers, at least one of them the current production command,
and probe.log must show the raw ffprobe evidence.

Prints WHY on failure.
"""
import json
import os
import re
import sys

REQUIRED = ["label", "pcm_reference_samples", "decoded_samples_after_mux",
            "decoded_samples_final", "expected_samples", "delta_final"]


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def main() -> int:
    for name in ("variants.json", "probe.log", "report.md"):
        if not os.path.exists(name):
            return fail(f"missing required artifact {name}")

    try:
        data = json.load(open("variants.json"))
    except Exception as exc:  # noqa: BLE001
        return fail(f"variants.json is not valid JSON: {exc}")

    variants = data.get("variants")
    if not isinstance(variants, list) or len(variants) < 3:
        n = len(variants) if isinstance(variants, list) else variants
        return fail(f"need >=3 measured variants, got {n}")

    labels = []
    for i, v in enumerate(variants):
        if not isinstance(v, dict):
            return fail(f"variant {i} is not an object")
        for k in REQUIRED:
            if k not in v:
                return fail(f"variant {i} missing key {k}")
        for k in REQUIRED[1:]:
            if not isinstance(v[k], (int, float)):
                return fail(f"variant {i} key {k} is not numeric (got {v[k]!r})")
        if v["delta_final"] != v["decoded_samples_final"] - v["expected_samples"]:
            return fail(
                f"variant {i} inconsistent: delta_final={v['delta_final']} but "
                f"decoded_final-expected={v['decoded_samples_final'] - v['expected_samples']}")
        labels.append(str(v["label"]))

    if not any(re.search(r"current|baseline|shortest", l, re.I) for l in labels):
        return fail(f"no baseline/current-production variant measured; labels={labels}")

    winners = [v for v in variants if 0 <= v["delta_final"] <= 1023]
    baseline_bad = any(
        re.search(r"current|baseline", l, re.I) and not (0 <= v["delta_final"] <= 1023)
        for l, v in zip(labels, variants))
    if not baseline_bad:
        print("NOTE: the baseline variant did not reproduce a deficit — read the report "
              "carefully before trusting a 'nothing to fix' conclusion")

    recommendation = data.get("recommendation")
    if not isinstance(recommendation, str) or len(recommendation.split()) < 3:
        return fail(f"variants.json needs a recommendation sentence, got {recommendation!r}")

    log = open("probe.log", encoding="utf-8", errors="replace").read()
    if "nb_samples" not in log:
        return fail("probe.log has no nb_samples evidence — sample counts were not probed")
    if "ffmpeg" not in log:
        return fail("probe.log shows no ffmpeg invocation — no variant was actually built")

    text = open("report.md", encoding="utf-8", errors="replace").read()
    low = text.lower()
    for section in ("## variants measured", "## why the baseline loses samples",
                    "## recommendation", "## risks"):
        if section not in low:
            return fail(f"report.md missing section {section}")
    cites = len(re.findall(r"[A-Za-z0-9_/.-]+\.(?:py|json|md):?\d*", text))
    if cites < 5:
        return fail(f"report.md has only {cites} file citations (need >=5)")
    if len(text.split()) < 300:
        return fail(f"report.md is only {len(text.split())} words (need >300)")

    for v in variants:
        print(f"{v['label']}: pcm={v['pcm_reference_samples']} after_mux={v['decoded_samples_after_mux']} "
              f"final={v['decoded_samples_final']} expected={v['expected_samples']} delta={v['delta_final']}")
    print(f"variants inside the AAC-padding-only invariant: "
          f"{[v['label'] for v in winners] or 'NONE'}")
    print(f"recommendation: {recommendation}")
    print("RESULT: PASS — alternatives were built and measured with consistent numbers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Receipt for the A/V sync divergence diagnosis on Fred E104.

The transfer gate (mode=av_anchors, threshold 40ms) failed the rendered program
at edited 945.728s -> mapped source 1517.494s with av_divergence_ms=+57.000 and
audio_confidence=0.405, five thousandths above the gate's own
min_audio_confidence=0.4 unmeasurable floor. A second anchor near the content
end reported +189.5ms.

This proves the worker MEASURED the true offset independently of the gate's own
anchor matcher rather than reasoning about it: offsets.json must carry
cross-correlation measurements at >=3 anchors including the two the gate
reported, with a stated correlation quality per anchor, and a verdict that
commits to drift-vs-measurement.

Prints WHY on failure.
"""
import json
import os
import re
import sys

NUM_KEYS = ["edited_time_s", "mapped_source_time_s", "measured_offset_ms",
            "correlation_peak"]
SECTIONS = ["## what was measured", "## drift or measurement",
            "## root cause", "## fix design", "## proof command", "## risks"]
GATE_ANCHORS = (945.728, 1891.055)


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def main() -> int:
    for name in ("offsets.json", "probe.log", "report.md"):
        if not os.path.exists(name):
            return fail(f"missing required artifact {name}")

    try:
        data = json.load(open("offsets.json"))
    except Exception as exc:  # noqa: BLE001
        return fail(f"offsets.json is not valid JSON: {exc}")

    anchors = data.get("anchors")
    if not isinstance(anchors, list) or len(anchors) < 3:
        n = len(anchors) if isinstance(anchors, list) else anchors
        return fail(f"need >=3 measured anchors, got {n}")

    for i, a in enumerate(anchors):
        if not isinstance(a, dict):
            return fail(f"anchor {i} is not an object")
        for k in NUM_KEYS:
            if not isinstance(a.get(k), (int, float)):
                return fail(f"anchor {i} missing numeric {k} (got {a.get(k)!r})")
        peak = float(a["correlation_peak"])
        if not 0.0 <= peak <= 1.0:
            return fail(f"anchor {i} correlation_peak {peak} is not a 0..1 quality value")

    edited_times = [float(a["edited_time_s"]) for a in anchors]
    for want in GATE_ANCHORS:
        if not any(abs(t - want) < 1.0 for t in edited_times):
            return fail(f"the gate's own failing anchor at edited {want}s was never "
                        f"measured; measured anchors were {edited_times}")

    verdict = data.get("verdict")
    if not isinstance(verdict, str) or len(verdict.split()) < 5:
        return fail(f"offsets.json needs a verdict sentence, got {verdict!r}")
    low_verdict = verdict.lower()
    if not any(t in low_verdict for t in ("drift", "measurement", "mis-located",
                                          "mislocated", "anchor")):
        return fail("the verdict does not commit to drift vs measurement error")

    log = open("probe.log", encoding="utf-8", errors="replace").read()
    if not re.search(r"correlat|xcorr|np\.correlate|scipy", log, re.I):
        return fail("probe.log shows no cross-correlation work — the offset was not "
                    "measured independently of the gate's own matcher")
    if "ffmpeg" not in log:
        return fail("probe.log shows no ffmpeg extraction of the compared audio")

    text = open("report.md", encoding="utf-8", errors="replace").read()
    lowtext = text.lower()
    missing = [s for s in SECTIONS if s not in lowtext]
    if missing:
        return fail(f"report.md missing sections: {missing}")
    cites = len(re.findall(r"[A-Za-z0-9_/.-]+\.(?:py|json|md):?\d*", text))
    if cites < 5:
        return fail(f"report.md has only {cites} file citations (need >=5)")
    if "min_audio_confidence" not in text and "0.4" not in text:
        return fail("report.md never engages with the gate's confidence floor, which is "
                    "the thing a lazy fix would raise")
    if len(text.split()) < 350:
        return fail(f"report.md is only {len(text.split())} words (need >350)")

    print(f"measured {len(anchors)} anchors independently:")
    for a in anchors:
        print(f"  edited={float(a['edited_time_s']):.3f}s "
              f"source={float(a['mapped_source_time_s']):.3f}s "
              f"offset={float(a['measured_offset_ms']):+.1f}ms "
              f"peak={float(a['correlation_peak']):.3f}")
    print(f"verdict: {verdict}")
    print("RESULT: PASS — the gate's failing anchors were re-measured independently "
          "with stated correlation quality")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

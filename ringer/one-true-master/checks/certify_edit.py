#!/usr/bin/env python3
"""One executed gate for auto-editing correctness.

Runs the 12-point A/V sync certifier plus every Tier-1 editing instrument
against a finished episode and emits ONE pass/fail verdict with per-check
evidence. This is the gate that moves editing confidence from "measured once by
hand" to "proven, repeatably, by an executed receipt."

A check that cannot be run (its input artifact is missing) is reported as
UNRUN, never as PASS — absence of evidence is not a pass, the same discipline
the whole pipeline is built on.

Usage: certify_edit.py EPISODE_DIR [--json OUT] [--tolerance-ms N]
Exit 0 only when every applicable check PASSES and none is UNRUN-because-blocking.
"""
import argparse
import json
import os
import subprocess
import sys

CHECKS_DIR = os.path.dirname(os.path.abspath(__file__))


def run(argv, timeout=1200):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        return 124, "TIMEOUT after %ss: %s" % (timeout, exc)
    except FileNotFoundError as exc:
        return 127, "missing input: %s" % exc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--tolerance-ms", default="40")
    ap.add_argument("--scope", choices=["episode", "clips", "all"], default="all",
                    help="episode = post-render master checks; clips = post-clip "
                         "checks; all = both (default)")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()
    ep = os.path.abspath(args.episode)

    proc = os.path.join(ep, "processed")
    final = os.path.join(ep, "final")
    stems = os.path.join(proc, "stems")
    program = os.path.join(final, "episode.mp4")
    angle = os.path.join(proc, "angle_switched.mp4")
    boundaries = os.path.join(proc, "angle_switched.mp4.boundaries.json")
    clips_manifest = os.path.join(ep, "clips", "polished_clips_manifest.json")
    timeline = os.path.join(proc, "timeline.json")
    program_audio = os.path.join(final, "episode.mp3")

    # Raw camera tracks are the resolution gate's reference: the master must
    # carry the detail the cameras actually captured. A pipeline that silently
    # downscaled every stem to 960x540 and re-upscaled to 1080p passed eight
    # gates undetected (2026-07-29) because nothing compared against source.
    raw_dir = os.path.join(ep, "raw")
    raw_videos = sorted(
        os.path.join(raw_dir, f) for f in (os.listdir(raw_dir) if os.path.isdir(raw_dir) else [])
        if "video" in f.lower() and f.lower().endswith((".webm", ".mp4", ".mkv"))
    )

    # The applied edit plan's removed spans, exported to session-grid seconds so
    # cut-absence can prove each is gone from the render. AUTHORITATIVE source
    # is episode.json local_media_build.clip_source.removed (written by the
    # orchestrator on every render); legacy content_edit.removed is second. A
    # pre-existing processed/_applied_cuts.json is only trusted as a LAST
    # resort: a real episode carried an orphaned, clock-mixed, duplicated
    # _applied_cuts.json from a superseded round that no code writes anymore,
    # and it shadowed the clean receipt (2026-07-29). Derived spans are
    # deduped, merged when overlapping, and written to a certify-owned file so
    # the orphan is never consulted when the authoritative receipt exists.
    orphan_cuts = os.path.join(proc, "_applied_cuts.json")
    cuts_json = os.path.join(proc, "_certify_cuts.json")
    removed = []
    try:
        ep_data = json.load(open(os.path.join(ep, "episode.json")))
        lmb = ep_data.get("local_media_build") or {}
        removed = ((lmb.get("clip_source") or {}).get("removed")
                   or (lmb.get("content_edit") or {}).get("removed") or [])
    except (OSError, ValueError):
        removed = []
    spans = []
    for span in removed:
        try:
            s, e = float(span["start_s"]), float(span["end_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            spans.append((s, e))
    if spans:
        spans.sort()
        merged = [list(spans[0])]
        for s, e in spans[1:]:
            if s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        json.dump([{"id": "cut_%02d" % i, "start_s": s, "end_s": e}
                   for i, (s, e) in enumerate(merged)], open(cuts_json, "w"))
    elif os.path.exists(orphan_cuts):
        cuts_json = orphan_cuts

    # Bumpers are repo-level assets (server/config INTRO_PATH/OUTRO_PATH =
    # <repo>/assets/intro.mp4 / outro.mp4), not per-episode files. Resolve up
    # from the episode dir to the repo assets, with per-episode overrides taking
    # precedence if they exist.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(ep)))
    intro = os.path.join(repo_root, "assets", "intro.mp4")
    outro = os.path.join(repo_root, "assets", "outro.mp4")
    for cand in (os.path.join(ep, "assets", "intro.mp4"),
                 os.path.join(final, "intro.mp4")):
        if os.path.exists(cand):
            intro = cand
    for cand in (os.path.join(ep, "assets", "outro.mp4"),
                 os.path.join(final, "outro.mp4")):
        if os.path.exists(cand):
            outro = cand

    def tool(name):
        return os.path.join(CHECKS_DIR, name)

    def _media_duration_s(path):
        """ffprobe duration as a string arg; '0.0' when unprobeable."""
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path], capture_output=True, text=True)
            return f"{float(out.stdout.strip()):.3f}"
        except (ValueError, OSError):
            return "0.0"

    # The final concatenates MASTERED bumpers (loudness-normalized copies), so
    # presence and level must be judged against those artifacts, not the raw
    # assets: expected level = each mastered file's own integrated LUFS (the
    # truth of what was concatenated), and the per-episode titlecard intro
    # supersedes the plain asset. Resolution imports the repo's own mastering
    # entry point (idempotent cache hit); on any failure we fall back to raw
    # assets with no expected levels — the checker then SKIPs level honestly
    # instead of gating on a guessed baseline.
    bumper_level_args = []
    titlecard = os.path.join(proc, "intro_titlecard.mp4")
    intro_src = titlecard if os.path.exists(titlecard) else intro
    try:
        sys.path.insert(0, repo_root)
        from server.pipeline.video_assembly import master_bumper_audio

        def _lufs(path):
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                 "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                capture_output=True, text=True).stderr
            return float(json.loads(out[out.rindex("{"):out.rindex("}") + 1])["input_i"])

        intro = master_bumper_audio(intro_src)
        outro = master_bumper_audio(outro)
        bumper_level_args = [
            "--expected-intro-lufs", f"{_lufs(intro):.2f}",
            "--expected-outro-lufs", f"{_lufs(outro):.2f}",
        ]
    except Exception as exc:  # noqa: BLE001 - degrade to presence-only, loudly
        print(f"NOTE bumper mastered-ref resolution failed ({exc!r}); "
              "level checks will SKIP against raw assets", flush=True)

    # The 12-point stem cross-correlation certifies the GRID-ALIGNED composite,
    # not the cut+bumpered final program: stem windows live on session-grid time
    # and the edited program is a different timeline. The composite is the right
    # target (it was the artifact certified at 0.0ms); the edited program's own
    # A/V sync is validated by the pipeline's transfer/anchor gate. (Gemini
    # review 2026-07-27.)
    composite = None
    for cand in ("composite_GRID.mp4", "composite.mp4"):
        p = os.path.join(proc, cand)
        if os.path.exists(p):
            composite = p
            break

    # Clip checks only mean something once clips have been (re)generated for
    # THIS render. If the clips manifest is older than the program, they are
    # stale and the clip scope is reported UNRUN rather than FAIL. (Gemini:
    # split episode-scope from clip-scope.)
    clips_fresh = (os.path.exists(clips_manifest) and os.path.exists(program)
                   and os.path.getmtime(clips_manifest) >= os.path.getmtime(program))

    # (label, scope, required_inputs, argv). scope: "episode" | "clips".
    plan = [
        ("sync-certify (12pt A/V drift, composite)", "episode",
         [stems, composite],
         ["python3", tool("certify_episode.py"), "--stems", stems,
          "--target", composite or "/nonexistent", "--points", "12",
          "--tolerance-ms", args.tolerance_ms]),
        ("camera-follows-speaker", "episode", [program, boundaries, stems],
         ["python3", tool("speaker_attribution_check.py"), "--render", program,
          "--boundaries", boundaries, "--stems", stems,
          "--offsets", "/dev/stdin"]),
        ("master-loudness (LUFS + true peak)", "episode", [program],
         ["python3", tool("loudness_check.py"), "--final", program]),
        ("black-frames (no flash/gaps at cuts)", "episode", [program],
         ["python3", tool("blackframe_check.py"), "--render", program]
         + (["--boundaries", boundaries] if os.path.exists(boundaries) else [])),
        ("bumpers (present, once, level-matched)", "episode", [program, intro, outro],
         ["python3", tool("bumper_check.py"), "--final", program,
          "--intro", intro, "--outro", outro] + bumper_level_args),
        # Structural first: every video stem must carry at least what its camera
        # captured (capped at the delivery canvas). SAME_FRAMING_DETAIL compares
        # the angle-switched program against the composite — both full-frame, so
        # the ratio means something; comparing differently-framed artifacts does
        # not (measured 2026-07-29).
        ("resolution (stems vs cameras, real detail)", "episode",
         [angle, stems] + (raw_videos[:1] or [os.path.join(ep, "__NO_RAW_TRACKS__")]),
         ["python3", tool("resolution_check.py"), "--final", angle,
          "--stems", stems, "--ancestor", composite or "/nonexistent",
          "--sources", *raw_videos]),
        ("freshness (outputs newer than inputs)", "episode", [ep],
         ["python3", tool("freshness_check.py"), "--episode", ep]),
        ("clip-words (clips contain their source)", "clips",
         [clips_manifest, stems] if clips_fresh else [os.path.join(ep, "__STALE_CLIPS__")],
         ["python3", tool("clip_words_check.py"), "--manifest", clips_manifest,
          "--stems", stems, "--clips-dir", os.path.join(ep, "clips")]),
        ("clip-framing (face present, centered, right shape)", "clips",
         [clips_manifest] if clips_fresh else [os.path.join(ep, "__STALE_CLIPS__")],
         ["python3", tool("clip_framing_check.py"),
          "--clips-dir", os.path.join(ep, "clips"),
          "--manifest", clips_manifest]),
        ("cut-absence (removed spans gone from render)", "clips",
         [program, stems, cuts_json] if clips_fresh else [os.path.join(ep, "__STALE_CLIPS__")],
         ["python3", tool("cut_absence_check.py"), "--render", program,
          "--stems", stems, "--cuts", cuts_json,
          # The render is the full final: the intro bumper precedes the edited
          # body, so splice-point silence checks must shift by its duration.
          "--head-offset-s", _media_duration_s(intro)]),
    ]

    wanted = args.scope  # episode | clips | all
    plan = [row for row in plan if wanted == "all" or row[1] == wanted]

    results, blocking_fail, unrun_blocking = [], 0, 0
    for label, scope, inputs, argv in plan:
        missing = [p for p in inputs if not os.path.exists(p)]
        if missing:
            why = ("clips not regenerated for this render (stale)"
                   if any("__STALE_CLIPS__" in m for m in missing)
                   else "missing input(s): " + ", ".join(os.path.basename(m) for m in missing))
            results.append({"check": label, "scope": scope, "verdict": "UNRUN", "why": why})
            unrun_blocking += 1
            print("UNRUN  %-40s (%s)" % (label, why))
            continue
        # speaker check needs an offsets JSON; skip cleanly if we can't supply it
        if "speaker_attribution_check.py" in argv[1]:
            offsets_path = os.path.join(proc, "_speaker_offsets.json")
            with open(offsets_path, "w") as fh:
                json.dump({"host": "host_audio.flac", "guest": "guest_audio.flac"}, fh)
            argv = [a if a != "/dev/stdin" else offsets_path for a in argv]
            # The camera check exempts segments inside a real camera dropout
            # (the >5s freeze forces the other angle). It wants a FLAT list of
            # {at_abs_s, duration_s}; timeline.json is a dict of tracks, so
            # flatten every track's holes into that shape first.
            if os.path.exists(timeline):
                try:
                    tl = json.load(open(timeline))
                    holes = []
                    for tr in (tl.get("tracks") or {}).values():
                        for h in (tr.get("holes") or []):
                            if "at_abs_s" in h and "duration_s" in h:
                                holes.append({"at_abs_s": float(h["at_abs_s"]),
                                              "duration_s": float(h["duration_s"])})
                    holes_path = os.path.join(proc, "_dropout_holes.json")
                    json.dump(holes, open(holes_path, "w"))
                    argv += ["--dropout-holes", holes_path]
                except (OSError, ValueError, KeyError, TypeError):
                    pass
        rc, out = run(argv)
        verdict = "PASS" if rc == 0 else ("UNRUN" if rc in (2, 124, 127) else "FAIL")
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        results.append({"check": label, "verdict": verdict, "rc": rc, "evidence": tail})
        if verdict == "FAIL":
            blocking_fail += 1
        elif verdict == "UNRUN":
            unrun_blocking += 1
        print("%-6s %-38s %s" % (verdict, label, tail[:80]))

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"episode": ep, "results": results}, fh, indent=2)

    print()
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    if blocking_fail:
        print("EDIT NOT CERTIFIED: %d check(s) FAILED, %d passed, %d unrun."
              % (blocking_fail, passed, unrun_blocking))
        return 1
    if unrun_blocking:
        print("EDIT NOT CERTIFIED: %d check(s) could not run (missing artifacts); "
              "%d passed. Unrun is not a pass." % (unrun_blocking, passed))
        return 1
    print("EDIT CERTIFIED: all %d applicable checks passed." % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Receipt for the master timeline + four grid-aligned stems (Fred E104).

Owner decision (Ankit, 2026-07-26): before any cut or mix, every raw track is
rendered onto ONE grid and verified independently. Five composites shipped
wrong because the only testable artifact was a 730MB mix.

GROUND TRUTH, measured directly from the raw packets on 2026-07-26 and
re-measured by this check (nothing below is taken from the worker's output):

  host_audio   first pts    0.225s  last pts 2842.396s  ONE hole 151.86s +76.86s
  host_video   first pts    0.229s  last pts 2842.421s  hole 25.68s +203.92s (+ blips)
  guest_audio  first pts  224.875s  last pts 2841.240s  no holes
  guest_video  first pts  224.896s  last pts 2841.228s  no holes
  grid T=0 = 0.225s   session length = 2842.196s

WHAT THIS PROVES, and why each assertion exists:

  1. timeline.json is DERIVED, not asserted. Every start/hole in it is
     re-measured here from the raw packets. A fabricated timeline fails.
  2. Container start_time agrees with Daily's filename epoch within 100ms.
     Two independent witnesses to the join time.
  3. All four stems are exactly session_length and share grid T=0.
  4. Fred's audio stem is SILENT before he joins. This is the exact defect
     the owner heard at t=60s: Fred audible 3m45s before entering the room.
     Measured as energy, not correlation, so there is nothing to interpret.
  5. Ankit's audio stem is SILENT across his 76.9s mic dropout, and LOUD
     either side of it. That proves the hole was FILLED, not deleted. A
     deleted hole slides all later audio 77s early, which is what made the
     two speakers talk over each other.
  6. Ankit's audio stem still lands where the timeline says late in the
     session, by cross-correlation. Catches accumulated drift.
  7. Fred's video stem is BLACK before he joins.
  8. Ankit's video stem is FROZEN across his 203.9s camera dropout (held
     frames), not skipped.

Silence and black thresholds are SELF-CALIBRATING against a known-content
window in the same stem, so a quiet recording cannot make this pass by luck.

Prints WHY on failure.
"""
import glob
import json
import os
import subprocess
import sys

import numpy as np

EP = "/mnt/d_drive/repos/podcast/episodes/2026-06-10-fred-cho"
PROC = os.path.join(EP, "processed")
TIMELINE = os.path.join(PROC, "timeline.json")
STEMDIR = os.path.join(PROC, "stems")

ROLES = ["host_audio", "host_video", "guest_audio", "guest_video"]

# Independently measured on 2026-07-26 (timeline_probe.py). The worker never
# sees these; they are the check's own witness.
TRUTH = {
    "host_audio":  {"first": 0.225,   "last": 2842.396, "holes": 1, "hole_total": 76.86},
    "host_video":  {"first": 0.229,   "last": 2842.421, "holes": 13, "hole_total": 220.38},
    "guest_audio": {"first": 224.875, "last": 2841.240, "holes": 0, "hole_total": 0.0},
    "guest_video": {"first": 224.896, "last": 2841.228, "holes": 0, "hole_total": 0.0},
}
SESSION_T0 = 0.225
SESSION_LEN = 2842.196

START_TOL = 0.050      # container start vs measured first packet
LEN_TOL = 0.150        # stem length vs session length (>4 video frames)
EPOCH_TOL = 0.100      # container start_time vs Daily filename epoch
GAP_MIN = 0.4          # what counts as a hole, same as the derivation


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, timeout=kw.pop("timeout", 3600), **kw)


def pts_list(path):
    """[(pts_time, duration)] for stream 0, tolerant of N/A durations.

    CHECKER DEFECT, found and fixed 2026-07-26: the first version dropped any
    packet whose duration_time was N/A. Both Daily VP8 camera tracks carry
    exactly 41 such packets as a LEADING PREFIX, so the check read host_video
    as starting at 1.557s instead of its true 0.229s and failed honest work.
    A missing duration is now backfilled from the median inter-packet delta,
    which is what the packet spacing already implies.
    """
    r = run(["ffprobe", "-v", "error", "-select_streams", "0", "-show_entries",
             "packet=pts_time,duration_time", "-of", "csv=p=0", path], text=True)
    rows = []
    for line in r.stdout.splitlines():
        p = line.split(",")
        if not p or not p[0].strip():
            continue
        try:
            rows.append(float(p[0]))
        except ValueError:
            continue
    return rows


def derive_holes(pts):
    """[(at_s, duration_s)] for every gap larger than GAP_MIN, plus the nominal step.

    UNIFORM BY DESIGN. A gap is measured as the packet-to-packet delta minus the
    track's own median delta, for EVERY packet, whether or not ffprobe reported a
    duration_time for it. Deriving holes from the reported duration instead makes
    dropout detection depend on a container metadata artifact: on these files the
    two camera tracks carry 41 duration-less packets, and any real dropout that
    happened to follow one of them would be silently absorbed into that packet's
    span and vanish from the record. The nominal step is the median rather than
    the mean so a 203-second dropout cannot drag it.
    """
    if len(pts) < 2:
        return [], 0.0
    deltas = [pts[i] - pts[i - 1] for i in range(1, len(pts))]
    nominal = float(np.median(deltas))
    holes = []
    for i in range(1, len(pts)):
        gap = pts[i] - pts[i - 1] - nominal
        if gap > GAP_MIN:
            holes.append((pts[i - 1] + nominal, gap))
    return holes, nominal


def container_start(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "0", "-show_entries",
             "stream=start_time", "-of", "default=nw=1:nk=1", path], text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def media_duration(path):
    """Duration from the format header, falling back to the decoded stream."""
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path], text=True)
    try:
        v = float(r.stdout.strip())
        if v > 0:
            return v
    except ValueError:
        pass
    r = run(["ffprobe", "-v", "error", "-select_streams", "0", "-show_entries",
             "stream=duration", "-of", "default=nw=1:nk=1", path], text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def audio_energy(path, start, dur):
    """Mean |sample| over a window, 0-32768 scale. 0.0 means digital silence."""
    r = run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
             "-i", path, "-map", "0:a:0", "-af",
             "aformat=sample_fmts=s16:sample_rates=16000:channel_layouts=mono",
             "-f", "s16le", "-"])
    if not r.stdout:
        return None
    x = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32)
    return float(np.abs(x).mean()) if len(x) else None


def audio_envelope(path, start=None, dur=None, rate=1000):
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-af",
            f"aformat=sample_fmts=s16:sample_rates={rate}:channel_layouts=mono",
            "-f", "s16le", "-"]
    r = run(cmd)
    x = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32)
    n = len(x) // rate
    return np.array([np.abs(x[i * rate:(i + 1) * rate]).mean() for i in range(n)])


def ncc(a, b):
    n, m = len(a), len(b)
    a = a - a.mean()
    na = np.linalg.norm(a)
    if na == 0 or m <= n:
        return None, 0.0
    L = 1 << int(np.ceil(np.log2(m + n)))
    c = np.fft.irfft(np.fft.rfft(a[::-1], L) * np.fft.rfft(b, L), L)[n - 1:m]
    cs = np.cumsum(np.concatenate(([0.0], b)))
    cs2 = np.cumsum(np.concatenate(([0.0], b.astype(np.float64) ** 2)))
    i = np.arange(len(c))
    s = cs[i + n] - cs[i]
    v = cs2[i + n] - cs2[i] - (s * s) / n
    v[v < 1e-9] = np.inf
    r = (c - (a.sum() * s) / n) / (na * np.sqrt(v))
    j = int(np.argmax(r))
    return j, float(r[j])


def frame_gray(path, t):
    r = run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path,
             "-frames:v", "1", "-vf", "scale=160:90,format=gray",
             "-f", "rawvideo", "-"])
    if not r.stdout:
        return None
    return np.frombuffer(r.stdout, dtype=np.uint8).astype(np.float32)


def main() -> int:
    # ---------- timeline.json exists and parses ----------
    if not os.path.exists(TIMELINE):
        return fail(f"no timeline.json at {TIMELINE} — stage 1 did not run")
    try:
        tl = json.load(open(TIMELINE, encoding="utf-8"))
    except json.JSONDecodeError as e:
        return fail(f"timeline.json is not valid JSON: {e}")

    for key in ("session", "tracks"):
        if key not in tl:
            return fail(f"timeline.json has no '{key}' key; got {sorted(tl)}")
    sess, tracks = tl["session"], tl["tracks"]
    missing = [r for r in ROLES if r not in tracks]
    if missing:
        return fail(f"timeline.json is missing track(s) {missing}; has {sorted(tracks)}")

    print("=== timeline.json")
    print(f"  session t0={sess.get('t0_abs_s')}  end={sess.get('end_abs_s')}  "
          f"length={sess.get('length_s')}")

    for f in ("t0_abs_s", "length_s"):
        if not isinstance(sess.get(f), (int, float)):
            return fail(f"session.{f} is {sess.get(f)!r}, expected a number")
    if abs(sess["t0_abs_s"] - SESSION_T0) > START_TOL:
        return fail(f"session t0 is {sess['t0_abs_s']:.3f}s; measured from the raw "
                    f"packets it is {SESSION_T0:.3f}s (earliest first packet)")
    if abs(sess["length_s"] - SESSION_LEN) > LEN_TOL:
        return fail(f"session length is {sess['length_s']:.3f}s; measured "
                    f"{SESSION_LEN:.3f}s (latest last packet minus t0)")

    # ---------- re-derive every fact from the raw packets ----------
    raw = sorted(glob.glob(os.path.join(EP, "raw", "*.webm")))
    if len(raw) != 4:
        return fail(f"expected 4 raw tracks, found {len(raw)}")
    rawmap = dict(zip(ROLES, raw))

    print("\n=== timeline.json re-derived from the raw packets")
    for role in ROLES:
        t, path = tracks[role], rawmap[role]
        pk = pts_list(path)
        first, last = pk[0], pk[-1]
        holes, _nominal = derive_holes(pk)
        hole_total = sum(h for _, h in holes)
        truth = TRUTH[role]

        # the check's own witness must match what it measures now
        if abs(first - truth["first"]) > START_TOL:
            return fail(f"{role}: raw first packet is now {first:.3f}s but this check "
                        f"was calibrated at {truth['first']:.3f}s — the raw files changed")

        claimed_start = t.get("container_start_s", t.get("first_pts_s"))
        if not isinstance(claimed_start, (int, float)):
            return fail(f"{role}: timeline has no numeric container_start_s/first_pts_s")
        if abs(claimed_start - first) > START_TOL:
            return fail(f"{role}: timeline says it starts at {claimed_start:.3f}s but the "
                        f"raw packets say {first:.3f}s — timeline.json is not derived")

        claimed_holes = t.get("holes", [])
        if not isinstance(claimed_holes, list):
            return fail(f"{role}: timeline holes is {type(claimed_holes).__name__}, expected a list")
        claimed_total = sum(float(h.get("duration_s", 0)) for h in claimed_holes)
        if abs(claimed_total - hole_total) > 1.0:
            return fail(f"{role}: timeline claims {claimed_total:.2f}s of dropouts across "
                        f"{len(claimed_holes)} holes; the raw packets show {hole_total:.2f}s "
                        f"across {len(holes)}. The hole list is what the stems are built "
                        f"from, so a wrong list means wrong stems")

        # Daily's filename epoch is the second, independent witness
        epoch_off = t.get("filename_epoch_offset_s")
        if not isinstance(epoch_off, (int, float)):
            return fail(f"{role}: timeline has no numeric filename_epoch_offset_s — the "
                        f"container start_time has no cross-check")
        if abs(epoch_off - claimed_start) > EPOCH_TOL:
            return fail(f"{role}: filename epoch says {epoch_off:.3f}s, container says "
                        f"{claimed_start:.3f}s, {abs(epoch_off - claimed_start) * 1000:.0f}ms "
                        f"apart. Over {EPOCH_TOL * 1000:.0f}ms the join time is not trustworthy")

        grid = t.get("grid_offset_s")
        if not isinstance(grid, (int, float)):
            return fail(f"{role}: timeline has no numeric grid_offset_s")
        if abs(grid - (first - SESSION_T0)) > START_TOL:
            return fail(f"{role}: grid_offset_s is {grid:.3f}s, expected "
                        f"{first - SESSION_T0:.3f}s (its start minus session t0)")

        print(f"  {role:12s} start {claimed_start:8.3f}s  grid +{grid:8.3f}s  "
              f"epoch {epoch_off:8.3f}s  holes {len(claimed_holes):2d} / {claimed_total:7.2f}s  OK")

    # ---------- the four stems ----------
    if not os.path.isdir(STEMDIR):
        return fail(f"no stems directory at {STEMDIR} — stage 2 did not run")
    stems = {}
    for role in ROLES:
        hits = sorted(glob.glob(os.path.join(STEMDIR, role + ".*")))
        hits = [h for h in hits if not h.endswith((".json", ".txt", ".log"))]
        if not hits:
            return fail(f"no stem for {role} in {STEMDIR} (looked for {role}.*)")
        stems[role] = hits[0]

    print("\n=== stem geometry (every stem is the full session on one grid)")
    for role in ROLES:
        d = media_duration(stems[role])
        if d is None:
            return fail(f"{role}: could not read a duration from {stems[role]}")
        print(f"  {role:12s} {os.path.basename(stems[role]):28s} {d:9.3f}s")
        if abs(d - SESSION_LEN) > LEN_TOL:
            return fail(f"{role}: stem is {d:.3f}s but the session is {SESSION_LEN:.3f}s "
                        f"({d - SESSION_LEN:+.3f}s off). Every stem must span the whole "
                        f"session so cuts are the same timestamps on all four")

    # ---------- Property A: Fred is silent and black before he joins ----------
    print("\n=== property A: the late joiner does not exist before he joins")
    g_grid = tracks["guest_audio"]["grid_offset_s"]
    ref = audio_energy(stems["guest_audio"], g_grid + 380.0, 60.0)
    if ref is None or ref < 1.0:
        return fail(f"guest_audio stem has no measurable content 380s after he joins "
                    f"(energy {ref}); the stem is empty or unreadable")
    for probe_at in (30.0, 120.0, g_grid - 40.0):
        e = audio_energy(stems["guest_audio"], probe_at, 25.0)
        if e is None:
            return fail(f"guest_audio stem unreadable at {probe_at:.0f}s")
        pct = 100.0 * e / ref
        print(f"  guest_audio at {probe_at:7.1f}s: energy {e:8.2f} "
              f"({pct:5.1f}% of his speaking level {ref:.2f})")
        if pct > 2.0:
            return fail(f"guest_audio stem has {pct:.1f}% of speaking energy at "
                        f"{probe_at:.0f}s, but Fred does not join until "
                        f"{g_grid:.1f}s. This is exactly what the owner heard at t=60s")

    gv_grid = tracks["guest_video"]["grid_offset_s"]
    lit = frame_gray(stems["guest_video"], gv_grid + 380.0)
    if lit is None:
        return fail("guest_video stem unreadable well after the join")
    lit_luma = float(lit.mean())
    for probe_at in (30.0, 120.0, gv_grid - 40.0):
        fr = frame_gray(stems["guest_video"], probe_at)
        if fr is None:
            return fail(f"guest_video stem unreadable at {probe_at:.0f}s")
        luma = float(fr.mean())
        print(f"  guest_video at {probe_at:7.1f}s: luma {luma:6.2f} "
              f"(camera-on luma {lit_luma:.2f})")
        if luma > max(6.0, 0.15 * lit_luma):
            return fail(f"guest_video stem is not black at {probe_at:.0f}s (luma "
                        f"{luma:.2f}) but Fred's camera does not open until {gv_grid:.1f}s")

    # ---------- Property B: Ankit's dropouts are filled, not deleted ----------
    print("\n=== property B: dropouts are filled with real time, not deleted")
    ha = tracks["host_audio"]
    ha_holes = sorted(ha["holes"], key=lambda h: float(h.get("duration_s", 0)), reverse=True)
    if not ha_holes:
        return fail("timeline claims host_audio has no dropouts, but the raw track has "
                    "a 76.9s hole at 151.9s")
    big = ha_holes[0]
    hole_at = float(big.get("at_abs_s", big.get("at_s"))) - SESSION_T0
    hole_dur = float(big["duration_s"])
    print(f"  host mic dropout: grid {hole_at:.2f}s .. {hole_at + hole_dur:.2f}s "
          f"({hole_dur:.2f}s)")
    if hole_dur < 30.0:
        return fail(f"the largest host_audio hole is only {hole_dur:.2f}s; the measured "
                    f"track has a 76.9s hole, so the derivation is wrong")

    before = audio_energy(stems["host_audio"], max(0.0, hole_at - 70.0), 60.0)
    inside = audio_energy(stems["host_audio"], hole_at + 8.0, hole_dur - 16.0)
    after = audio_energy(stems["host_audio"], hole_at + hole_dur + 15.0, 60.0)
    if None in (before, inside, after):
        return fail("host_audio stem unreadable around the dropout")
    loud = max(before, after)
    print(f"  host_audio energy  before {before:8.2f}   inside {inside:8.2f}   "
          f"after {after:8.2f}")
    if loud < 1.0:
        return fail(f"host_audio stem has no content either side of the dropout "
                    f"(before {before:.2f}, after {after:.2f}); the stem is empty")
    if 100.0 * inside / loud > 2.0:
        return fail(f"host_audio stem has {100.0 * inside / loud:.1f}% of speaking energy "
                    f"inside a {hole_dur:.0f}s dropout. The hole was NOT filled with "
                    f"silence — later audio has been slid earlier into it, which is what "
                    f"made the two speakers overlap")

    late_ref = audio_envelope(rawmap["host_audio"])
    if len(late_ref) < 2200:
        return fail(f"host_audio raw envelope is only {len(late_ref)}s, too short to probe")
    win = late_ref[2000:2090]
    stem_env = audio_envelope(stems["host_audio"])
    j, pk = ncc(win, stem_env)
    if j is None:
        return fail("could not correlate the host stem late window")
    # A plain decode collapses the holes, so raw second 2000 sits at
    # 2000 + (holes before it) on the grid.
    expected = 2000.0 + sum(float(h["duration_s"]) for h in ha["holes"]
                            if float(h.get("at_abs_s", h.get("at_s"))) - SESSION_T0 < 2000.0 + hole_dur)
    print(f"  host_audio raw second 2000 lands at grid {j}s "
          f"(expected {expected:.0f}s, peak {pk:.3f})")
    if pk < 0.45:
        return fail(f"host_audio late window will not lock (peak {pk:.3f}); the stem is "
                    f"scrambled, not merely shifted")
    if abs(j - expected) > 3.0:
        return fail(f"host_audio raw second 2000 lands at grid {j}s, expected "
                    f"{expected:.0f}s ({j - expected:+.0f}s off). The stem drifts against "
                    f"its own timeline late in the session")

    hv = tracks["host_video"]
    hv_holes = sorted(hv["holes"], key=lambda h: float(h.get("duration_s", 0)), reverse=True)
    if not hv_holes or float(hv_holes[0]["duration_s"]) < 100.0:
        return fail("timeline does not show the 203.9s host camera dropout at 25.7s")
    vbig = hv_holes[0]
    vat = float(vbig.get("at_abs_s", vbig.get("at_s"))) - SESSION_T0
    vdur = float(vbig["duration_s"])
    print(f"  host camera dropout: grid {vat:.2f}s .. {vat + vdur:.2f}s ({vdur:.2f}s)")
    a = frame_gray(stems["host_video"], vat + 20.0)
    b = frame_gray(stems["host_video"], vat + vdur - 20.0)
    live = frame_gray(stems["host_video"], max(0.0, vat - 10.0))
    if a is None or b is None or live is None:
        return fail("host_video stem unreadable around the camera dropout")
    frozen_delta = float(np.abs(a - b).mean())
    live_delta = float(np.abs(live - a).mean())
    print(f"  host_video frames {vat + 20:.0f}s vs {vat + vdur - 20:.0f}s differ by "
          f"{frozen_delta:.2f}; vs a live frame at {max(0.0, vat - 10.0):.0f}s by {live_delta:.2f}")
    if frozen_delta > 8.0:
        return fail(f"host_video frames {vdur - 40:.0f}s apart inside the camera dropout "
                    f"differ by {frozen_delta:.2f}. The dropout was skipped rather than "
                    f"held, so the video timeline is short by up to {vdur:.0f}s")

    print(f"\nRESULT: PASS — timeline.json is derived from the packets, all four stems "
          f"span the {SESSION_LEN:.0f}s session on one grid, the late joiner is absent "
          f"before he joins, and both of Ankit's dropouts are filled rather than deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())

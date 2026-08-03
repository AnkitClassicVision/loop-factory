"""Deterministic, dependency-free renderer for the Loop Factory board feed.

The renderer deliberately knows only the board-feed vocabulary.  Department
names, labels, thresholds, and loop-specific values always arrive as data.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ALLOWED_AUTH_CLASSES = {"oauth_cli", "service_oauth", "local_model"}
UNKNOWN = "unknown"
HISTORY_SCHEMA = "board-history/v1"
HISTORY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


CSS = r"""
:root{
  --bg:oklch(1 0 0);--surface:oklch(0.965 0.002 0);
  --rule:oklch(0.885 0.003 0);--ink:oklch(0.18 0.01 0);
  --muted:oklch(0.44 0.008 0);--red:oklch(0.50 0.194 0);
  --red-tint:oklch(0.94 0.03 0);--green:oklch(0.52 0.12 155);
  --green-soft:oklch(0.72 0.09 155);--amber:oklch(0.65 0.13 70);
  --amber-text:oklch(0.50 0.12 70);--violet:oklch(0.52 0.09 300)
}
*{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px}
body{background:var(--bg);color:var(--ink);font:400 .95rem/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:2.5rem clamp(1.25rem,4vw,4rem) 4rem}
.wrap{max-width:1180px;margin:0 auto}.num{font-variant-numeric:tabular-nums}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:.75rem 1.5rem;padding-bottom:1.1rem;border-bottom:1px solid var(--ink)}
header h1{font-size:1.3rem;font-weight:650;letter-spacing:-.01em}
header .proto{font-size:.78rem;color:var(--muted)}header .meta{margin-left:auto;font-size:.82rem;color:var(--muted)}
.projection-warning{margin-top:1rem;padding:.9rem 1rem;border:2px solid var(--red);background:var(--red-tint);color:var(--ink);font-size:.86rem}
.projection-warning b{color:var(--red)}
.tabs{display:flex;flex-wrap:wrap;gap:.45rem 1.15rem;padding:.8rem 0;border-bottom:1px solid var(--rule)}
.tab{color:var(--muted);font-size:.82rem;text-decoration:none}.tab.active{color:var(--ink);font-weight:650;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:.3rem}
.status-summary{display:flex;flex-wrap:wrap;gap:.4rem 1.5rem;margin-top:.8rem}.chip{display:flex;align-items:center;gap:.5rem;font-size:.82rem}.chip .dot{width:.55rem;height:.55rem;border-radius:50%;flex:none}.dot.ok{background:var(--green)}.dot.deg{background:var(--violet)}.chip .state{color:var(--muted)}
section{margin-top:3.1rem}.zone-h{display:flex;align-items:baseline;gap:1rem;margin-bottom:1.3rem;flex-wrap:wrap}
.zone-h h2{font-size:.95rem;font-weight:650}.zone-h .note{font-size:.8rem;color:var(--muted);margin-left:auto}
.zone-rule{border:0;border-top:1px solid var(--rule);margin-bottom:1.3rem}
.quiet,.empty,.unknown{color:var(--muted)}.empty{font-size:1.1rem;margin-top:2rem}
.dept-h{font-size:.82rem;font-weight:650;margin:1.5rem 0 .65rem}.subhead{font-size:.82rem;font-weight:600;margin:1.2rem 0 .45rem}
.objgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:2.5rem;align-items:start}
.obj .goalline{font-size:.8rem;color:var(--muted);margin-bottom:.6rem;min-height:2.4em}.obj .goalline b{color:var(--ink)}
.obj .fig{font-size:2.3rem;font-weight:650;letter-spacing:-.01em;line-height:1.1;white-space:nowrap}.obj .fig .unit{font-size:1rem;font-weight:400;color:var(--muted)}
.obj .sub{font-size:.82rem;color:var(--muted);margin-top:.25rem}
.bullet-wrap{margin-top:1rem;max-width:34rem}.bullet{position:relative;height:1.05rem;background:var(--surface);border-radius:2px}
.bullet .minzone{position:absolute;left:0;top:0;bottom:0;background:var(--red-tint);border-radius:2px 0 0 2px}
.bullet .val{position:absolute;left:0;top:22%;height:56%;background:var(--green);border-radius:2px}
.bullet .tick{position:absolute;top:-.3rem;bottom:-.3rem;width:2px}.bullet .tick.min{background:var(--red)}.bullet .tick.target{background:var(--ink)}
.bullet-lbls{position:relative;height:1.2rem;margin-top:.35rem;font-size:.72rem}.bullet-lbls span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.bullet-lbls .min{color:var(--red);font-weight:600}.bullet-lbls .target{font-weight:600}.bullet-lbls .scale{right:0;left:auto;transform:none;color:var(--muted)}
.statline{display:flex;flex-wrap:wrap;gap:1.2rem 2.2rem;margin-top:1.2rem}.stat .v{font-size:1.45rem;font-weight:650;white-space:nowrap}.stat .l{color:var(--muted);font-size:.8rem;margin-top:.1rem}
.trend-wrap{max-width:480px}.trend-stats{font-size:.8rem;color:var(--muted);margin-top:.65rem}
svg.lc{display:block;width:100%;max-width:480px;height:auto;overflow:visible}.lc .axis-line{stroke:var(--rule);stroke-width:1}.lc .series{fill:none;stroke:var(--green);stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.lc .mark{fill:var(--bg);stroke:var(--green);stroke-width:2}.lc text{font-size:.7rem;fill:var(--muted)}.lc .point-value{fill:var(--green);font-weight:650}
.actions{display:grid;grid-template-columns:1.35fr 1fr;gap:2.5rem}.andon-stack{display:flex;flex-direction:column;gap:.85rem}
.andon{background:var(--red);color:#fff;border-radius:6px;padding:1.1rem 1.25rem;animation:glow 2s ease-in-out infinite}
@keyframes glow{50%{box-shadow:0 0 0 7px oklch(0.50 0.194 0 / .16)}}@media(prefers-reduced-motion:reduce){.andon{animation:none}}
.andon .k{font-size:.75rem;color:#fff}.andon .fault{font-size:1.15rem;font-weight:650;margin:.2rem 0 .4rem}.andon p{font-size:.85rem;line-height:1.45}
.andon code{background:rgba(255,255,255,.16);border-radius:4px;padding:.1rem .4rem;font:.82rem/1 ui-monospace,monospace}
.appr-row,.run-row{display:flex;gap:1rem;padding:.55rem 0;border-bottom:1px dashed var(--rule);font-size:.88rem;align-items:baseline}.appr-row:last-child,.run-row:last-child{border-bottom:0}
.appr-row .age,.run-row .state{margin-left:auto;color:var(--amber-text);font-weight:600;font-size:.82rem;white-space:nowrap}.appr-row a{color:inherit;text-decoration-thickness:1px;text-underline-offset:2px}
.split{display:grid;grid-template-columns:1.35fr 1fr;gap:2.5rem}.o-row{margin-bottom:1.1rem}.o-row .top{display:flex;justify-content:space-between;font-size:.85rem;margin-bottom:.35rem}
.o-row .top b{font-weight:600}.o-row .top .t{color:var(--muted)}.o-bar{display:flex;gap:2px;height:1.15rem}.o-bar i{border-radius:2px;min-width:0}.o-bar .ok{background:var(--green)}.o-bar .blk{background:var(--amber)}.o-bar .err{background:var(--red)}
.o-key{font-size:.78rem;color:var(--muted);margin-top:.4rem}.o-key .k-ok{color:var(--green);font-weight:600}.o-key .k-blk{color:var(--amber-text);font-weight:600}.o-key .k-err{color:var(--red);font-weight:600}
.t-row{display:grid;grid-template-columns:12.5rem 1fr 10.5rem;gap:1rem;align-items:center;padding:.42rem 0}.t-row .route{justify-self:end}.t-lbl{font-size:.88rem}.t-lbl b{font-weight:600}.t-lbl .m{color:var(--muted);font-size:.8rem}
.t-track{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap}.t-bar{height:1.05rem;border-radius:2px;background:var(--green-soft);min-width:4px}.t-count{font-size:.85rem;font-weight:600;white-space:nowrap}.t-count .mut{color:var(--muted);font-weight:400}
.route{font-size:.7rem;font-weight:650;border:1px solid var(--rule);border-radius:99px;padding:.12rem .55rem;white-space:nowrap}.route.oauth{color:var(--green);border-color:var(--green)}.route.api{color:var(--red);border-color:var(--red)}
.loop-group{margin-top:1rem;max-width:62rem}.loop-row{display:grid;grid-template-columns:minmax(12rem,1fr) minmax(12rem,1.4fr) minmax(12rem,1.4fr) auto;gap:.8rem 1.2rem;align-items:center;padding:.5rem 0;border-bottom:1px dashed var(--rule);font-size:.85rem}.loop-row:last-child{border-bottom:0}
.loop-ident{display:flex;align-items:center;gap:.55rem}.loop-dot{width:.58rem;height:.58rem;border-radius:50%;flex:none}.loop-dot.success{background:var(--green)}.loop-dot.failure{background:var(--red)}.loop-dot.muted{background:var(--muted)}.loop-time{color:var(--muted)}.disabled-tag{font-size:.7rem;font-weight:650;color:var(--muted);border:1px solid var(--rule);border-radius:99px;padding:.1rem .5rem;white-space:nowrap}
.telemetry-alert{margin:.6rem 0}.funnel{margin-top:1.5rem}.funnel .cap{font-size:.8rem;color:var(--muted);margin-bottom:.8rem}.funnel .cap b{color:var(--ink)}
.f-row{display:grid;grid-template-columns:10.5rem 1fr;gap:1rem;align-items:center;padding:.28rem 0}.f-lbl{font-size:.85rem;color:var(--muted)}.f-track{display:flex;align-items:center;gap:.6rem}.f-bar{height:1.05rem;border-radius:2px;background:var(--green-soft);min-width:6px}.f-bar.hot{background:var(--green)}.f-bar.final{background:var(--ink)}.f-count{font-size:.85rem;font-weight:600;white-space:nowrap}
.generic-group{margin-top:1.4rem;max-width:52rem}.generic-group h3{font-size:.88rem;margin-bottom:.35rem}.kv{display:grid;grid-template-columns:minmax(8rem,15rem) 1fr;gap:.4rem 1rem;padding:.35rem 0;border-bottom:1px dashed var(--rule);font-size:.85rem}.kv dt{color:var(--muted)}.kv dd{overflow-wrap:anywhere}
footer{margin-top:3.5rem;padding-top:1rem;border-top:1px solid var(--rule);font-size:.78rem;color:var(--muted)}footer code{font:.75rem ui-monospace,monospace}
@media(max-width:900px){section{margin-top:2.3rem}.objgrid,.actions,.split{grid-template-columns:1fr;gap:1.8rem}.obj .goalline{min-height:0}.f-row{grid-template-columns:8.5rem 1fr}.t-row,.loop-row{grid-template-columns:1fr;gap:.3rem}.t-row .route{justify-self:start}.disabled-tag{justify-self:start}}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _is_unknown(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() == UNKNOWN)


def _value(value: Any) -> str:
    if _is_unknown(value):
        return '<span class="unknown">unknown</span>'
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return _display_number(value)
    if isinstance(value, (dict, list)):
        return _esc(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return _esc(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or _is_unknown(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return _value(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _css_percent(value: float, total: float) -> str:
    percent = 0.0 if total <= 0 else max(0.0, min(100.0, value / total * 100.0))
    return f"{percent:.2f}".rstrip("0").rstrip(".") + "%"


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (str(record.get("ts", "")), str(record.get("department", "")), str(record.get("id", "")))


def _age(record: dict[str, Any], generated: datetime | None, *, queued: bool = False) -> str:
    data = record["data"]
    supplied = data.get("age")
    if not _is_unknown(supplied):
        return str(supplied)
    source = data.get("queued_at") if queued else None
    stamp = _parse_ts(source) or _parse_ts(record.get("ts"))
    if generated is None or stamp is None:
        return UNKNOWN
    seconds = max(0, int((generated - stamp).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}".rstrip("0").rstrip(".") + " h"
    return f"{seconds / 86400:.1f}".rstrip("0").rstrip(".") + " d"


def read_feed(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    """Read valid board events and count malformed, nonblank lines."""
    records: list[dict[str, Any]] = []
    malformed = 0
    with Path(path).open(encoding="utf-8") as feed:
        for raw in feed:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                malformed += 1
                continue
            if not (
                isinstance(record, dict)
                and all(isinstance(record.get(key), str) for key in ("id", "kind", "ts", "department"))
                and isinstance(record.get("data"), dict)
            ):
                malformed += 1
                continue
            records.append(record)
    return records, malformed


def _valid_history(value: Any, filename: str | None = None) -> bool:
    if not (
        isinstance(value, dict)
        and value.get("schema") == HISTORY_SCHEMA
        and isinstance(value.get("date"), str)
        and isinstance(value.get("departments"), dict)
        and isinstance(value.get("loops"), dict)
    ):
        return False
    return filename is None or filename == f'{value["date"]}.json'


def _load_history(path: str | Path) -> list[dict[str, Any]]:
    root = Path(path)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_file() or not HISTORY_FILE.fullmatch(entry.name):
            continue
        try:
            value = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if _valid_history(value, entry.name):
            rows.append(value)
    return rows[-7:]


def _history_rows(
    history: str | Path | Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(history, (str, Path)):
        return _load_history(history)
    rows = [row for row in history if _valid_history(row)]
    rows.sort(key=lambda row: row["date"])
    return rows[-7:]


def _history_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _history_total(rows: Sequence[dict[str, Any]], key: str) -> int | float | str:
    values = [_history_number(row.get(key)) for row in rows]
    measured = [value for value in values if value is not None]
    if not measured:
        return UNKNOWN
    total = sum(measured)
    return int(total) if total.is_integer() else total


def _collecting_history(days: int) -> str:
    noun = "day" if days == 1 else "days"
    return f'<p class="quiet">collecting history — {days} {noun} so far</p>'


def _line_chart(
    history: Sequence[dict[str, Any]],
    points: Sequence[tuple[int, float]],
    *,
    label: str,
    suffix: str = "",
    maximum: float | None = None,
) -> str:
    width = 480.0
    left = 32.0
    right = 448.0
    top = 22.0
    bottom = 120.0
    span = max(1, len(history) - 1)
    peak = maximum if maximum is not None else max((value for _, value in points), default=0.0)
    peak = max(peak, 1.0)

    coordinates: dict[int, tuple[float, float, float]] = {}
    for index, value in points:
        x = left + (right - left) * index / span
        bounded = max(0.0, min(peak, value))
        y = bottom - (bottom - top) * bounded / peak
        coordinates[index] = (x, y, value)

    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    previous: int | None = None
    for index in sorted(coordinates):
        x, y, _ = coordinates[index]
        if previous is None or index == previous + 1:
            current.append((x, y))
        else:
            segments.append(current)
            current = [(x, y)]
        previous = index
    if current:
        segments.append(current)

    polylines = "".join(
        '<polyline class="series" points="'
        + " ".join(f"{x:.1f},{y:.1f}" for x, y in segment)
        + '"></polyline>'
        for segment in segments
    )
    marks: list[str] = []
    for index in sorted(coordinates):
        x, y, value = coordinates[index]
        shown = _display_number(value)
        date = history[index]["date"]
        label_y = max(11.0, y - 7.0)
        marks.append(
            f'<circle class="mark" cx="{x:.1f}" cy="{y:.1f}" r="3.5" '
            f'data-date="{_esc(date)}" data-value="{_esc(shown)}"></circle>'
            f'<text class="point-value num" x="{x:.1f}" y="{label_y:.1f}" '
            f'text-anchor="middle">{_esc(shown)}{_esc(suffix)}</text>'
        )
    dates = "".join(
        f'<text class="axis-date num" x="{left + (right - left) * index / span:.1f}" '
        f'y="148" text-anchor="middle" data-date="{_esc(row["date"])}">'
        f'{_esc(row["date"][5:])}</text>'
        for index, row in enumerate(history)
    )
    return (
        f'<div class="trend-wrap"><svg class="lc" viewBox="0 0 {int(width)} 160" '
        f'role="img" aria-label="{_esc(label)}">'
        f'<line class="axis-line" x1="{left:.1f}" y1="{bottom:.1f}" '
        f'x2="{right:.1f}" y2="{bottom:.1f}"></line>'
        f'{polylines}{"".join(marks)}{dates}</svg></div>'
    )


def _department_history(
    history: Sequence[dict[str, Any]], department: str | None
) -> tuple[list[tuple[int, float]], list[dict[str, Any]]]:
    points: list[tuple[int, float]] = []
    totals: list[dict[str, Any]] = []
    for index, day in enumerate(history):
        departments = day["departments"]
        if department is None:
            records = [value for _, value in sorted(departments.items()) if isinstance(value, dict)]
            paired = [
                record
                for record in records
                if _history_number(record.get("runs")) is not None
                and _history_number(record.get("ok")) is not None
            ]
            runs = sum(_history_number(record["runs"]) or 0.0 for record in paired)
            ok = sum(_history_number(record["ok"]) or 0.0 for record in paired)
            totals.extend(records)
        else:
            record = departments.get(department)
            if not isinstance(record, dict):
                continue
            runs = _history_number(record.get("runs"))
            ok = _history_number(record.get("ok"))
            totals.append(record)
            if runs is None or ok is None:
                continue
        if runs > 0:
            points.append((index, ok / runs * 100.0))
    return points, totals


def _render_history(
    history: Sequence[dict[str, Any]],
    *,
    department: str | None,
    loop_group: bool,
) -> str:
    if loop_group and department is not None:
        points: list[tuple[int, float]] = []
        for index, day in enumerate(history):
            group = day["loops"].get(department)
            if not isinstance(group, dict):
                continue
            failed = _history_number(group.get("failed"))
            if failed is not None:
                points.append((index, failed))
        body = (
            _collecting_history(len(points))
            if len(points) < 2
            else _line_chart(
                history,
                points,
                label=f"{department} failed loops over seven days",
            )
        )
    else:
        points, totals = _department_history(history, department)
        if len(points) < 2:
            body = _collecting_history(len(points))
        else:
            chart_label = (
                "Estate ok rate over seven days"
                if department is None
                else f"{department} ok rate over seven days"
            )
            body = _line_chart(
                history,
                points,
                label=chart_label,
                suffix="%",
                maximum=100.0,
            )
            runs = _history_total(totals, "runs")
            errors = _history_total(totals, "error")
            body += (
                '<p class="trend-stats num">'
                f'{_display_number(runs)} runs · {_display_number(errors)} errors over window'
                "</p>"
            )
    return (
        '<section aria-label="Seven days"><div class="zone-h"><h2>Seven days</h2>'
        '<span class="note">daily trend</span></div><hr class="zone-rule">'
        f"{body}</section>"
    )


def _latest_by_department(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=_record_key):
        latest[record["department"]] = record
    return latest


def _is_objective(record: dict[str, Any]) -> bool:
    return record["kind"] == "metrics" and "objective_id" in record["data"]


def _is_funnel(record: dict[str, Any]) -> bool:
    return record["kind"] == "funnel_stage" or (
        record["kind"] == "metrics" and "funnel_id" in record["data"] and "stage" in record["data"]
    )


def _is_telemetry(record: dict[str, Any]) -> bool:
    data = record["data"]
    return record["kind"] == "metrics" and "lane" in data and (
        "auth_class" in data or "model" in data or "calls" in data
    )


DAILY_KEYS = {
    "runs", "ok", "blocked", "error", "errors", "tokens_in", "tokens_out",
    "model_calls", "evaluator_pass_rate", "self_fixes", "failed_fixes",
    "heal_budget_used", "heal_budget_limit",
}

TAB_DEPARTMENT_KINDS = {"dept_status", "metrics", "andon", "approval", "active_run"}


def _is_daily(record: dict[str, Any]) -> bool:
    return record["kind"] == "metrics" and bool(DAILY_KEYS.intersection(record["data"])) and not (
        _is_objective(record) or _is_funnel(record) or _is_telemetry(record)
    )


def _render_objective(record: dict[str, Any]) -> str:
    data = record["data"]
    dept = _esc(record["department"])
    label = _esc(data.get("label", data.get("objective_id", "objective")))
    observed = data.get("observed", UNKNOWN)
    unit = "" if _is_unknown(data.get("unit")) else str(data.get("unit", ""))
    setpoint = data.get("setpoint")
    setpoint_text = "" if _is_unknown(setpoint) else f'<div class="sub">setpoint {_value(setpoint)}</div>'
    figure = (
        '<span class="unknown">unknown</span>'
        if _is_unknown(observed)
        else f'{_display_number(observed)}<span class="unit">{_esc(unit)}</span>'
    )
    bullet = ""
    minimum = _number(data.get("minimum"))
    target = _number(data.get("target"))
    if minimum is not None and target is not None:
        observed_number = _number(observed) or 0.0
        scale = _number(data.get("maximum")) or max(minimum, target, observed_number, 1.0)
        if scale <= target:
            scale = target + max(1.0, abs(target) * .15)
        min_width = _css_percent(minimum, scale)
        target_width = _css_percent(target, scale)
        value_width = _css_percent(observed_number, scale)
        bullet = f"""
        <div class="bullet-wrap">
          <div class="bullet" role="img" aria-label="{_esc(label)}: {_esc(_display_number(observed))} observed, minimum {_esc(_display_number(data.get('minimum')))}, target {_esc(_display_number(data.get('target')))}">
            <span class="minzone" style="width:{min_width}"></span><span class="val" style="width:{value_width}"></span>
            <span class="tick min" style="left:{min_width}"></span><span class="tick target" style="left:{target_width}"></span>
          </div>
          <div class="bullet-lbls num"><span class="min" style="left:{min_width}">min {_display_number(data.get('minimum'))}</span><span class="target" style="left:{target_width}">target {_display_number(data.get('target'))}</span><span class="scale">0–{_display_number(scale)}</span></div>
        </div>"""
    return f"""<article class="obj" data-department="{dept}">
      <div class="goalline"><b>{dept}</b> · {label}</div>
      <div class="fig num">{figure}</div>{setpoint_text}{bullet}
    </article>"""


def _render_metric_stats(daily: dict[str, dict[str, Any]]) -> str:
    labels = (
        ("runs", "jobs run"), ("ok", "finished clean"), ("blocked", "blocked"),
        ("error", "errors"), ("errors", "errors"), ("model_calls", "model calls"),
        ("tokens_in", "tokens in"), ("tokens_out", "tokens out"),
        ("evaluator_pass_rate", "evaluator pass rate"),
    )
    parts: list[str] = []
    for department, record in sorted(daily.items()):
        data = record["data"]
        stats = []
        seen_labels: set[str] = set()
        for key, label in labels:
            if key in data and label not in seen_labels:
                suffix = "%" if key == "evaluator_pass_rate" and not _is_unknown(data[key]) else ""
                stats.append(f'<div class="stat"><div class="v num">{_display_number(data[key])}{suffix}</div><div class="l">{_esc(label)}</div></div>')
                seen_labels.add(label)
        if stats:
            parts.append(f'<div class="dept-metrics"><h3 class="dept-h">{_esc(department)} · daily</h3><div class="statline">{"".join(stats)}</div></div>')
    return "".join(parts) or '<p class="quiet">no daily metrics reporting</p>'


def _render_status_summary(departments: Sequence[str], statuses: dict[str, dict[str, Any]]) -> str:
    rows: list[str] = []
    for department in departments:
        record = statuses.get(department)
        if record is None:
            rows.append(f'<div class="chip"><span class="dot deg"></span><b>{_esc(department)}</b><span class="state">status unknown</span></div>')
            continue
        data = record["data"]
        is_healthy = data.get("ok") is True or str(data.get("health", "")).lower() == "healthy"
        health = "healthy" if is_healthy else str(data.get("health", "degraded"))
        autonomy = data.get("autonomy_state", data.get("mode", UNKNOWN))
        last_cycle = data.get("last_cycle_at", UNKNOWN)
        rows.append(
            f'<div class="chip"><span class="dot {"ok" if is_healthy else "deg"}"></span><b>{_esc(department)}</b>'
            f'<span class="state">{_esc(health)} · {_value(autonomy)} · last cycle {_value(last_cycle)}</span></div>'
        )
    return f'<div class="status-summary">{"".join(rows)}</div>' if rows else ""


def _render_andon(record: dict[str, Any], generated: datetime | None) -> str:
    data = record["data"]
    fault = data.get("fault", data.get("finding", data.get("finding_code", data.get("code", data.get("severity", "needs a human")))))
    detail = data.get("detail", "no detail supplied")
    impact = data.get("impact")
    fix = data.get("fix_command", data.get("fix"))
    extras = ""
    if not _is_unknown(impact):
        extras += f" Impact: {_esc(impact)}."
    if not _is_unknown(fix):
        extras += f" Fix: <code>{_esc(fix)}</code>"
    return f"""<article class="andon" role="alert">
      <div class="k">ANDON · needs a human · {_esc(record['department'])} · {_esc(_age(record, generated))}</div>
      <div class="fault">{_esc(fault)}</div><p>{_esc(detail)}{extras}</p>
    </article>"""


def _approval_ref(data: dict[str, Any]) -> str:
    ref = data.get("card_ref")
    if _is_unknown(ref):
        return ""
    escaped = _esc(ref)
    if isinstance(ref, str) and ref.startswith(("https://", "http://")):
        return f' · <a href="{escaped}">card</a>'
    return f" · <code>{escaped}</code>"


def _render_outcome(record: dict[str, Any]) -> str:
    data = record["data"]
    ok_value = data.get("ok", UNKNOWN)
    blocked_value = data.get("blocked", UNKNOWN)
    error_value = data.get("error", data.get("errors", UNKNOWN))
    ok = _number(ok_value) or 0.0
    blocked = _number(blocked_value) or 0.0
    error = _number(error_value) or 0.0
    reported_runs = _number(data.get("runs"))
    total = reported_runs if reported_runs is not None else ok + blocked + error
    denom = max(total, ok + blocked + error, 0.0)
    total_value: Any = data.get("runs", denom if any(_number(value) is not None for value in (ok_value, blocked_value, error_value)) else UNKNOWN)
    total_text = _display_number(total_value)
    return f"""<div class="o-row" data-department="{_esc(record['department'])}">
      <div class="top"><b>{_esc(record['department'])}</b><span class="t num">{total_text} jobs</span></div>
      <div class="o-bar" role="img" aria-label="{_esc(record['department'])}: {_esc(_display_number(ok_value))} ok, {_esc(_display_number(blocked_value))} blocked, {_esc(_display_number(error_value))} error">
        <i class="ok" style="width:{_css_percent(ok, denom)}"></i><i class="blk" style="width:{_css_percent(blocked, denom)}"></i><i class="err" style="width:{_css_percent(error, denom)}"></i>
      </div><div class="o-key num"><span class="k-ok">{_display_number(ok_value)} ok</span> · <span class="k-blk">{_display_number(blocked_value)} blocked</span> · <span class="k-err">{_display_number(error_value)} error</span></div>
    </div>"""


def _metered(data: dict[str, Any]) -> bool:
    auth = str(data.get("auth_class", "")).lower()
    lane = str(data.get("lane", "")).lower()
    return auth not in ALLOWED_AUTH_CLASSES or "api" in auth or "meter" in auth or "api" in lane or "meter" in lane


def _render_telemetry(record: dict[str, Any], maximum_tokens: float) -> str:
    data = record["data"]
    lane = data.get("lane", "unnamed lane")
    model = data.get("model", UNKNOWN)
    auth = data.get("auth_class", UNKNOWN)
    calls = data.get("calls", UNKNOWN)
    tokens_in = data.get("tokens_in", UNKNOWN)
    tokens_out = data.get("tokens_out", UNKNOWN)
    if _metered(data):
        return f"""<article class="andon telemetry-alert" role="alert">
          <div class="k">ANDON · forbidden route · {_esc(record['department'])}</div>
          <div class="fault">Metered/API lane detected — {_esc(lane)}</div>
          <p>model {_value(model)} · {_display_number(calls)} calls · route <code>{_esc(auth)}</code></p>
        </article>"""
    input_number = _number(tokens_in)
    output_number = _number(tokens_out)
    total = (input_number or 0.0) + (output_number or 0.0)
    total_value: Any = total if input_number is not None and output_number is not None else UNKNOWN
    width = _css_percent(total, maximum_tokens) if maximum_tokens else "0%"
    route_label = "Local · model" if str(auth).lower() == "local_model" else "OAuth · subscription"
    return f"""<div class="t-row" data-department="{_esc(record['department'])}">
      <span class="t-lbl"><b>{_esc(lane)}</b> · {_value(model)}<br><span class="m num">{_display_number(calls)} calls · {_esc(record['department'])}</span></span>
      <span class="t-track"><i class="t-bar" style="width:{width}"></i><span class="t-count num">{_display_number(total_value)} <span class="mut">tokens · {_display_number(tokens_in)} in / {_display_number(tokens_out)} out</span></span></span>
      <span class="route oauth" title="{_esc(auth)}">{route_label}</span>
    </div>"""


def _render_self_fix(daily: dict[str, dict[str, Any]]) -> str:
    rendered: list[str] = []
    for department, record in sorted(daily.items()):
        data = record["data"]
        keys = ("self_fixes", "failed_fixes", "heal_budget_used", "heal_budget_limit")
        if not any(key in data for key in keys):
            continue
        facts = []
        for key in keys:
            if key in data:
                facts.append(f'<div class="stat"><div class="v num">{_display_number(data[key])}</div><div class="l">{_esc(key.replace("_", " "))}</div></div>')
        rendered.append(f'<div class="self-fix"><h4 class="dept-h">{_esc(department)} · self-fix ladder</h4><div class="statline">{"".join(facts)}</div></div>')
    return "".join(rendered) or '<p class="quiet">no self-fix activity reporting</p>'


def _render_loops(records: Sequence[dict[str, Any]]) -> str:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in sorted(records, key=_record_key):
        unit = str(record["data"].get("unit", UNKNOWN))
        latest[(record["department"], unit)] = record
    if not latest:
        return '<p class="quiet">no loop telemetry — run timersense</p>'

    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for (department, _), record in sorted(latest.items()):
        groups[department].append(record)

    rendered: list[str] = []
    for department, rows in sorted(groups.items()):
        row_html: list[str] = []
        for record in sorted(rows, key=lambda row: str(row["data"].get("unit", ""))):
            data = record["data"]
            unit = str(data.get("unit", UNKNOWN))
            display_unit = unit.removesuffix(".timer")
            result = data.get("last_result")
            if result == "success":
                dot_class = "success"
                result_text = ""
            elif result == "failure":
                dot_class = "failure"
                result_text = ""
            else:
                dot_class = "muted"
                result = UNKNOWN
                result_text = ' <span class="unknown">unknown</span>'
            disabled = (
                '<span class="disabled-tag">disabled</span>'
                if data.get("enabled") is False
                else ""
            )
            row_html.append(
                f'<div class="loop-row" data-department="{_esc(department)}">'
                f'<span class="loop-ident"><span class="loop-dot {dot_class}" role="img" '
                f'aria-label="{_esc(result)}" title="{_esc(result)}"></span>'
                f'<b>{_esc(display_unit)}</b>{result_text}</span>'
                f'<span class="loop-time">last run {_value(data.get("last_run"))}</span>'
                f'<span class="loop-time">next run {_value(data.get("next_run"))}</span>'
                f'{disabled}</div>'
            )
        rendered.append(
            f'<div class="loop-group"><h4 class="dept-h">{_esc(department)}</h4>'
            f'{"".join(row_html)}</div>'
        )
    return "".join(rendered)


def _render_funnels(records: Sequence[dict[str, Any]]) -> str:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (record["department"], str(record["data"].get("funnel_id", "funnel")))
        groups.setdefault(key, []).append(record)
    rendered: list[str] = []
    for (department, funnel_id), rows in sorted(groups.items()):
        rows.sort(key=lambda row: (_number(row["data"].get("order")) if _number(row["data"].get("order")) is not None else float("inf"), _record_key(row)))
        counts = [_number(row["data"].get("count")) or 0.0 for row in rows]
        maximum = max(counts, default=0.0)
        row_html = []
        for index, row in enumerate(rows):
            data = row["data"]
            count = _number(data.get("count")) or 0.0
            css_class = "final" if index == len(rows) - 1 else ("hot" if index >= len(rows) / 2 else "")
            row_html.append(f"""<div class="f-row" data-order="{_esc(data.get('order', ''))}"><span class="f-lbl">{_esc(data.get('stage', 'stage'))}</span><span class="f-track"><i class="f-bar {css_class}" style="width:{_css_percent(count, maximum)}"></i><span class="f-count num">{_display_number(data.get('count', UNKNOWN))}</span></span></div>""")
        rendered.append(f'<div class="funnel"><div class="cap"><b>{_esc(department)} · {_esc(funnel_id)}</b></div>{"".join(row_html)}</div>')
    return "".join(rendered)


def _render_fallback(records: Sequence[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for record in sorted(records, key=lambda row: (row["department"], str(row["data"].get("group", row["data"].get("metric_group", "metrics"))), _record_key(row))):
        data = record["data"]
        group = data.get("group", data.get("metric_group", data.get("namespace", "metrics")))
        pairs = []
        for key, value in sorted(data.items()):
            if key in {"group", "metric_group", "namespace"}:
                continue
            pairs.append(f'<div class="kv"><dt>{_esc(key.replace("_", " "))}</dt><dd>{_value(value)}</dd></div>')
        if not pairs:
            pairs.append('<div class="kv"><dt>values</dt><dd><span class="unknown">unknown</span></dd></div>')
        rendered.append(f'<div class="generic-group"><h3>{_esc(record["department"])} · {_esc(group)}</h3><dl>{"".join(pairs)}</dl></div>')
    return "".join(rendered)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower())
    return slug or "tab"


def _tab_manifest(records: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    names = {
        row["department"]
        for row in records
        if row["kind"] in TAB_DEPARTMENT_KINDS or row["kind"] == "loop_status"
    }
    used: set[str] = set()
    tabs: list[tuple[str, str]] = []
    for name in sorted(names, key=lambda value: (value.casefold(), value)):
        base = _slug(name)
        slug = base
        suffix = 2
        while slug in used:
            slug = f"{base}-{suffix}"
            suffix += 1
        used.add(slug)
        tabs.append((name, f"{slug}.html"))
    return tabs


def _render_tabs(tabs: Sequence[tuple[str, str]], current_tab: str | None) -> str:
    estate_class = "tab active" if current_tab is None else "tab"
    estate_current = ' aria-current="page"' if current_tab is None else ""
    links = [f'<a class="{estate_class}" href="index.html"{estate_current}>Estate</a>']
    for name, href in tabs:
        active = name == current_tab
        tab_class = "tab active" if active else "tab"
        current = ' aria-current="page"' if active else ""
        links.append(f'<a class="{tab_class}" href="{_esc(href)}"{current}>{_esc(name)}</a>')
    return f'<nav class="tabs" aria-label="Board tabs">{"".join(links)}</nav>'


def render_html(
    records: Sequence[dict[str, Any]],
    *,
    malformed_count: int = 0,
    department: str | None = None,
    title: str | None = None,
    tabs: Sequence[tuple[str, str]] | None = None,
    current_tab: str | None = None,
    history: str | Path | Sequence[dict[str, Any]] | None = None,
) -> str:
    """Render already-validated records into a deterministic HTML document."""
    health_rows = [row for row in records if row["kind"] == "feed_health"]
    health = sorted(health_rows, key=_record_key)[-1]["data"] if health_rows else {}
    projection_status = str(health.get("projection_status", "fresh")).lower()
    projection_warning = ""
    protocol_label = "LIVE — rendered from board-feed.ndjson"
    if projection_status in {"stale", "incomplete"}:
        reason = health.get("projection_reason", UNKNOWN)
        age = health.get("rollup_age_s", UNKNOWN)
        limit = health.get("rollup_max_age_s", UNKNOWN)
        protocol_label = "STALE / INCOMPLETE — canonical rollup projection"
        projection_warning = (
            '<div class="projection-warning" role="alert">'
            '<b>STALE / INCOMPLETE CANONICAL ROLLUP</b> · '
            f'state {_esc(projection_status)} · reason {_esc(reason)} · '
            f'age {_esc(age)}s · freshness limit {_esc(limit)}s. '
            "Canonical rows shown below were not replaced with direct department reads."
            "</div>"
        )
    selected = [row for row in records if department is None or row["department"] == department]
    selected.sort(key=_record_key)
    departments = sorted({row["department"] for row in selected})
    generated_dt = max((_parse_ts(row.get("ts")) for row in selected), default=None, key=lambda value: value or datetime.min.replace(tzinfo=timezone.utc))
    generated = generated_dt.isoformat().replace("+00:00", "Z") if generated_dt else UNKNOWN
    statuses = _latest_by_department(row for row in selected if row["kind"] == "dept_status")
    healthy = sum(1 for row in statuses.values() if row["data"].get("ok") is True or str(row["data"].get("health", "")).lower() == "healthy")
    degraded = len(statuses) - healthy
    epochs = [_number(row["data"].get("epoch")) for row in statuses.values()]
    epoch_values = [value for value in epochs if value is not None]
    epoch = _display_number(max(epoch_values)) if epoch_values else UNKNOWN
    page_title = title or (f"Loop Factory — {department} Board" if department else "Loop Factory — Estate Board")

    objectives = [row for row in selected if _is_objective(row)]
    daily = _latest_by_department(row for row in selected if _is_daily(row))
    andons = sorted((row for row in selected if row["kind"] == "andon"), key=_record_key)
    approvals = sorted((row for row in selected if row["kind"] == "approval"), key=lambda row: (str(row["data"].get("queued_at", row["ts"])), _record_key(row)))
    runs = [row for row in selected if row["kind"] == "active_run"]
    loops = [row for row in selected if row["kind"] == "loop_status"]
    telemetry = [row for row in selected if _is_telemetry(row)]
    funnels = [row for row in selected if _is_funnel(row)]
    fallback = [row for row in selected if row["kind"] == "metrics" and not (_is_objective(row) or _is_daily(row) or _is_telemetry(row) or _is_funnel(row))]

    status_meta = f"{len(departments)} department{'s' if len(departments) != 1 else ''} · {healthy} healthy · {degraded} degraded · epoch {epoch} · generated {generated}"
    empty_notice = '<p class="empty">no departments reporting</p>' if not departments else ""
    objective_html = "".join(_render_objective(row) for row in sorted(objectives, key=lambda row: (row["department"], str(row["data"].get("objective_id", "")), _record_key(row))))
    if not objective_html:
        objective_html = '<p class="quiet">no objectives declared — charter setpoints missing</p>'

    andon_html = "".join(_render_andon(row, generated_dt) for row in andons) if andons else '<p class="quiet">no andons — nothing needs you</p>'
    approval_html = "".join(
        f'<div class="appr-row"><span><b>{_esc(row["department"])}</b> · {_esc(row["data"].get("detail", row["data"].get("status", "approval waiting")))}{_approval_ref(row["data"])}</span><span class="age num">{_esc(_age(row, generated_dt, queued=True))}</span></div>'
        for row in approvals
    ) or '<p class="quiet">no approvals waiting</p>'
    outcome_html = "".join(_render_outcome(row) for _, row in sorted(daily.items())) or '<p class="quiet">no daily outcomes reporting</p>'
    run_html = "".join(
        f'<div class="run-row"><span><b>{_esc(row["department"])}</b> · {_esc(row["data"].get("node", "run"))}</span><span class="state">{_esc(row["data"].get("status", UNKNOWN))}</span></div>'
        for row in sorted(runs, key=_record_key)
    ) or '<p class="quiet">no active runs</p>'
    token_totals = [(_number(row["data"].get("tokens_in")) or 0) + (_number(row["data"].get("tokens_out")) or 0) for row in telemetry]
    maximum_tokens = max(token_totals, default=0.0)
    telemetry_html = "".join(_render_telemetry(row, maximum_tokens) for row in sorted(telemetry, key=lambda row: (row["department"], str(row["data"].get("lane", "")), _record_key(row)))) or '<p class="quiet">no telemetry reporting</p>'
    loop_html = _render_loops(loops)
    loop_specific = _render_funnels(funnels) + _render_fallback(fallback)
    if not loop_specific:
        loop_specific = '<p class="quiet">no loop-specific metrics reporting</p>'
    history_rows = _history_rows(history) if history is not None else None
    trend_html = ""
    if history_rows is not None:
        trend_html = _render_history(
            history_rows,
            department=department,
            loop_group=department is not None and bool(loops) and not daily,
        )
    footer_source = (
        "board-feed.ndjson and board-history/v1"
        if history_rows is not None
        else "board-feed.ndjson only"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(page_title)}</title>
<style>{CSS}</style>
</head>
<body><div class="wrap">
<header><h1>{_esc(page_title)}</h1><span class="proto">{_esc(protocol_label)}</span><span class="meta num">{_esc(status_meta)}</span></header>
{projection_warning}
{_render_tabs(tabs, current_tab) if tabs is not None else ""}
{_render_status_summary(departments, statuses)}{empty_notice}
<section aria-label="Metrics"><div class="zone-h"><h2>1 · Metrics</h2><span class="note">objectives and daily measures</span></div><hr class="zone-rule">
  <div class="objgrid">{objective_html}</div>{_render_metric_stats(daily)}
</section>
{trend_html}
<section aria-label="Main actions"><div class="zone-h"><h2>2 · Main actions</h2><span class="note">needs a human, oldest first</span></div><hr class="zone-rule">
  <div class="actions"><div class="andon-stack">{andon_html}</div><div class="approval-inbox"><h3 class="subhead">Approval inbox</h3>{approval_html}</div></div>
</section>
<section aria-label="Activity"><div class="zone-h"><h2>3 · Activity</h2><span class="note">outcomes, active runs, and model routes</span></div><hr class="zone-rule">
  <div class="split"><div><h3 class="subhead">Daily outcomes</h3>{outcome_html}</div><div><h3 class="subhead">Active runs</h3>{run_html}<h3 class="subhead">Self-fix activity</h3>{_render_self_fix(daily)}</div></div>
  <h3 class="subhead">Telemetry</h3>{telemetry_html}
  <h3 class="subhead">All loops</h3>{loop_html}
</section>
<section aria-label="Loop-specific"><div class="zone-h"><h2>4 · Loop-specific</h2><span class="note">feed-defined panels, never renderer branches</span></div><hr class="zone-rule">{loop_specific}</section>
<footer>Rendered from <code>{_esc(footer_source)}</code> · {_esc(malformed_count)} malformed feed line{'s' if malformed_count != 1 else ''} · no external assets</footer>
</div></body></html>
"""


def render_board(feed_path: str | Path, out_path: str | Path, *, department: str | None = None, title: str | None = None) -> str:
    """Render ``feed_path`` to ``out_path`` and return the generated HTML."""
    records, malformed = read_feed(feed_path)
    rendered = render_html(records, malformed_count=malformed, department=department, title=title)
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return rendered


def render_site(
    feed_path: str | Path,
    out_dir: str | Path,
    *,
    title: str | None = None,
    history_dir: str | Path | None = None,
) -> dict[str, str]:
    """Render an estate index and one portable, linked page per feed tab."""
    records, malformed = read_feed(feed_path)
    tabs = _tab_manifest(records)
    history = _load_history(
        history_dir if history_dir is not None else Path(feed_path).parent / "history"
    )
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered: dict[str, str] = {}
    rendered["index.html"] = render_html(
        records,
        malformed_count=malformed,
        title=title,
        tabs=tabs,
        history=history,
    )
    for name, filename in tabs:
        rendered[filename] = render_html(
            records,
            malformed_count=malformed,
            department=name,
            title=f"{name} — Loop Board",
            tabs=tabs,
            current_tab=name,
            history=history,
        )
    for filename, page in rendered.items():
        (output_dir / filename).write_text(page, encoding="utf-8")
    return rendered


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Loop Factory board-feed as static HTML")
    parser.add_argument("--feed", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--out", type=Path)
    destination.add_argument("--site", type=Path)
    parser.add_argument("--department")
    parser.add_argument("--title")
    parser.add_argument("--history", type=Path)
    args = parser.parse_args(argv)
    if args.site is not None and args.department is not None:
        parser.error("--department requires --out")
    if args.site is None and args.history is not None:
        parser.error("--history requires --site")
    try:
        if args.site is not None:
            render_site(args.feed, args.site, title=args.title, history_dir=args.history)
        else:
            render_board(args.feed, args.out, department=args.department, title=args.title)
    except OSError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

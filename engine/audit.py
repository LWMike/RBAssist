# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pyrekordbox"]
# ///
"""M1 grid audit: check the beatgrid Rekordbox gave each track against the audio.

Read-only. The Rekordbox database is copied to a temp folder before opening,
so this is safe to run while Rekordbox is open.

usage: uv run engine/audit.py --playlist NAME [--playlist NAME ...]
                              [--crate-xml PATH] [--previews] [--jobs N]

Checks (SPEC 4.2): exact tempo, tempo drift, half-beat error, double/half
tempo, dynamic grid on a steady track. Beat position and bar 1 are taken from
Rekordbox as they are. Every finding is a proposal for review, never applied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spike"))
import gridfit  # noqa: E402
import cues as cuelib  # noqa: E402

RB_DIR = os.path.expanduser("~/Library/Pioneer/rekordbox")
OUT = os.path.join(HERE, "out")
CACHE = os.path.join(HERE, ".cache")

MIN_CONFIDENCE = 0.8     # fitter confidence needed to audit a track at all
DRIFT_FLAG_MS = 15.0     # grid slide across the kicked part that gets flagged
HALF_BEAT_FRAC = 0.38    # |offset| beyond this share of a beat counts as half a beat out
TEMPO_RANGE = (95.0, 190.0)  # where a tempo is expected to sit; outside it, double/half is considered
STEADY_WANDER_MS = 8.0   # fitter wander below this means one tempo fits the track
RATIOS = {0.5: "half", 2.0: "double", 2 / 3: "two-thirds", 1.5: "one and a half times", 0.75: "three-quarters", 4 / 3: "four-thirds"}


# ---------- Rekordbox, read-only ----------

def open_library(rb_dir: str = RB_DIR):
    """Open a private copy of master.db so Rekordbox's own file is never touched."""
    logging.disable(logging.CRITICAL)
    from pyrekordbox import MasterDatabase
    tmp = tempfile.mkdtemp(prefix="rbassist-")
    for name in ("master.db", "master.db-wal", "master.db-shm"):
        src = os.path.join(rb_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, tmp)
    return MasterDatabase(os.path.join(tmp, "master.db")), tmp


def read_grid(anlz_dat: str) -> dict | None:
    from pyrekordbox.anlz import AnlzFile
    if not os.path.exists(anlz_dat):
        return None
    tag = AnlzFile.parse_file(anlz_dat).get_tag("PQTZ")
    t = np.asarray(tag.times, float)
    if len(t) < 16:
        return None
    return dict(times=t, bpms=np.asarray(tag.bpms, float), beats=np.asarray(tag.beats, int))


def list_playlists(db) -> list[dict]:
    """Ordinary playlists (not folders or smart lists) with their folder path."""
    allp = {p.ID: p for p in db.get_playlist().all() if not p.rb_local_deleted}
    out = []
    for p in allp.values():
        if p.Attribute != 0:
            continue
        folder, q = [], allp.get(p.ParentID)
        while q is not None and len(folder) < 8:
            folder.insert(0, q.Name or "")
            q = allp.get(q.ParentID)
        missing = sum(1 for s in p.Songs if s.Content is None or not os.path.exists(s.Content.FolderPath or ""))
        out.append(dict(id=str(p.ID), name=p.Name or "", folder=" / ".join(folder), count=len(p.Songs), missing=missing, seq=p.Seq or 0))
    return sorted(out, key=lambda d: (d["folder"], d["seq"]))


def _slot(kind: int) -> str | None:
    """Rekordbox DjmdCue.Kind: 0 memory, 1–3 hot A–C, 5–9 hot D–H (4 unused)."""
    if kind == 0:
        return None
    return "ABC"[kind - 1] if kind <= 3 else "DEFGH"[kind - 5] if 5 <= kind <= 9 else f"#{kind}"


def existing_cues(c) -> list[dict]:
    out = []
    for q in c.Cues or []:
        if q.rb_local_deleted:
            continue
        out.append(dict(slot=_slot(q.Kind or 0), time=round((q.InMsec or 0) / 1000, 3),
                        end=round(q.OutMsec / 1000, 3) if (q.OutMsec or -1) > 0 else None, name=q.Comment or ""))
    return sorted(out, key=lambda d: (d["slot"] is None, d["slot"] or "", d["time"]))


def playlist_tracks(db, names: list[str], rb_dir: str = RB_DIR, ids: list[str] | None = None) -> list[dict]:
    out, seen = [], set()
    share = os.path.join(rb_dir, "share")
    for p in db.get_playlist().all():
        if (p.Name in names or str(p.ID) in (ids or [])) and not p.rb_local_deleted:
            for s in sorted(p.Songs, key=lambda s: s.TrackNo or 0):
                c = s.Content
                if c is None or c.ID in seen:
                    continue
                seen.add(c.ID)
                out.append(dict(
                    content_id=str(c.ID), playlist=p.Name, title=c.Title or "", artist=(c.Artist.Name if c.Artist else "") or "",
                    path=c.FolderPath or "", rb_bpm=(c.BPM or 0) / 100.0,
                    anlz=share + c.AnalysisDataPath if c.AnalysisDataPath else None,
                    existing=existing_cues(c),
                ))
    return out


def crate_bpms(xml_path: str | None) -> dict[str, float]:
    if not xml_path or not os.path.exists(xml_path):
        return {}
    out = {}
    for t in ET.parse(xml_path).getroot().find("COLLECTION"):
        loc = urllib.parse.unquote(t.get("Location").replace("file://localhost", ""))
        out[loc] = float(t.get("AverageBpm") or 0)
    return out


# ---------- the audit ----------

def _cached_fit(path: str, hint: float | None):
    """Fit result and decoded audio. The fit is cached on path, size and mtime."""
    st = os.stat(path)
    key = hashlib.sha1(f"{path}|{st.st_size}|{int(st.st_mtime)}|{hint}".encode()).hexdigest()
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, key + ".json")
    if os.path.exists(f):
        d = json.load(open(f))
        d["sections"] = [tuple(s) for s in d["sections"]]
        return gridfit.Grid(**d), None
    x = gridfit.decode(path)
    g = gridfit.fit(x, hint)
    json.dump(g.to_dict(), open(f, "w"))
    return g, x


def crate_multiple(rb: dict, crate_bpm: float | None) -> dict | None:
    """Double/half tempo. Flagged only when Rekordbox's tempo is outside the
    expected range and Crate has the multiple that is inside it. Crate is not
    treated as the authority: it has some drum & bass at 87 and some at 174.
    Needs no audio, so it covers tracks the kick fitter cannot audit."""
    if not crate_bpm or not (TEMPO_RANGE[0] <= crate_bpm < TEMPO_RANGE[1]):
        return None
    if TEMPO_RANGE[0] <= float(np.median(rb["bpms"])) < TEMPO_RANGE[1]:
        return None
    t, nums = rb["times"], rb["beats"]
    rb_bpm = float(np.median(rb["bpms"]))
    if abs(rb_bpm * 2 - crate_bpm) < 1.0:
        times = np.empty(len(t) * 2 - 1)
        times[0::2] = t
        times[1::2] = (t[:-1] + t[1:]) / 2
        ref = int(np.flatnonzero(nums == 1)[0]) * 2 if (nums == 1).any() else 0
        word = "half"
    elif abs(rb_bpm / 2 - crate_bpm) < 0.5:
        ref = int(np.flatnonzero(nums == 1)[0]) if (nums == 1).any() else 0
        times = t[ref % 2::2]
        ref = (ref - ref % 2) // 2
        word = "double"
    else:
        return None
    numbers = ((np.arange(len(times)) - ref) % 4) + 1
    bpm = rb_bpm * 2 if word == "half" else rb_bpm / 2
    return dict(
        finding=dict(check="tempo-multiple", severity="major", text=f"Rekordbox has {rb_bpm:.2f}, {word} of Crate's {crate_bpm:.0f}."),
        proposal=dict(bpm=round(bpm, 2), first_beat_ms=round(float(times[0]) * 1000, 1), first_beat_number=int(numbers[0]),
                      n_beats=int(len(times)), times=[round(float(v), 4) for v in times], numbers=[int(v) for v in numbers]))


def audit_grid(g: gridfit.Grid, rb: dict) -> dict:
    """Compare Rekordbox's grid with the fitted kicks. Returns measurements,
    findings and, when something is proposed, the proposed grid."""
    t, bpms, nums = rb["times"], rb["bpms"], rb["beats"]
    rb_bpm = float(np.median(bpms))
    tempos = sorted({round(float(b), 2) for b in bpms})
    T = 60.0 / g.bpm
    res = dict(rb_bpm=rb_bpm, rb_tempos=len(tempos), rb_first_beat_ms=round(float(t[0]) * 1000, 1), fit_bpm=g.bpm, fit_bpm_raw=round(g.bpm_fit, 3),
               snapped=g.snapped, confidence=round(g.confidence, 2), findings=[], proposal=None)

    weak = [f for f in g.flags if f in ("too-few-kicks", "sparse-kicks", "loose-onsets")]
    if g.confidence < MIN_CONFIDENCE or weak or g.first_kick is None or g.last_kick is None:
        res["status"] = "unaudited"
        res["reason"] = "not four-on-the-floor enough to check: " + (", ".join(weak) or f"confidence {g.confidence:.2f}")
        return res

    # Rekordbox beats inside the kicked part of the track
    span = (t >= g.first_kick - 0.01) & (t <= g.last_kick + 0.01)
    if span.sum() < 32:
        res["status"] = "unaudited"
        res["reason"] = "Rekordbox's grid barely overlaps the kicked part"
        return res
    ts = t[span]

    # Offset of each Rekordbox beat from the nearest fitted beat, followed
    # continuously so a slow slide is not folded back at half a beat.
    phase = (ts - g.beat0) / T
    d = np.unwrap((phase - np.round(phase)) * 2 * np.pi) / (2 * np.pi) * T  # seconds, + = Rekordbox late
    q = max(len(d) // 8, 8)
    d_start, d_end, d_mid = float(np.median(d[:q])), float(np.median(d[-q:])), float(np.median(d))
    drift_ms = (d_end - d_start) * 1000
    step = np.diff(d) * 1000
    res.update(drift_ms=round(drift_ms, 1), offset_ms=round(((d_mid / T + 0.5) % 1 - 0.5) * T * 1000, 1),
               kick_span_s=round(float(ts[-1] - ts[0]), 1), fit_drift_ms=round(g.drift_ms, 1), fit_wander_ms=round(g.wander_ms, 1))

    findings = res["findings"]
    new_bpm = None

    # double / half tempo
    ratio = rb_bpm / g.bpm
    for r, name in RATIOS.items():
        if abs(ratio - r) < 0.015:
            findings.append(dict(check="tempo-multiple", severity="major",
                                 text=f"Rekordbox has {rb_bpm:.2f}, which is {name} of the kick tempo {g.bpm:.2f}."))
            new_bpm = g.bpm
            break
    multiple = new_bpm is not None

    steady = g.wander_ms < STEADY_WANDER_MS and abs(g.drift_ms) < 6.0
    if not multiple:
        if len(tempos) > 1 and steady:
            findings.append(dict(check="dynamic-grid", severity="major",
                                 text=f"Rekordbox used {len(tempos)} tempos ({tempos[0]:.2f}–{tempos[-1]:.2f}) but one tempo, {g.bpm:.2f}, "
                                      f"fits every kick within {g.wander_ms:.0f} ms."))
            new_bpm = g.bpm
        elif len(tempos) == 1 and abs(drift_ms) >= DRIFT_FLAG_MS and steady:
            findings.append(dict(check="tempo-drift", severity="major",
                                 text=f"Rekordbox's grid at {rb_bpm:.2f} slides {abs(drift_ms):.0f} ms {'late' if drift_ms > 0 else 'early'} "
                                      f"against the kicks over {res['kick_span_s'] / 60:.1f} min. {g.bpm:.2f} holds."))
            new_bpm = g.bpm
        elif len(tempos) == 1 and g.snapped and abs(rb_bpm - g.bpm) >= 0.005 and steady:
            findings.append(dict(check="tempo-exact", severity="minor",
                                 text=f"Rekordbox has {rb_bpm:.2f}; the kicks fit {g.bpm:.2f} exactly. The difference is only "
                                      f"{abs(drift_ms):.0f} ms over the track."))
            new_bpm = g.bpm

    # half-beat error: only meaningful when the grid is not sliding much
    half = False
    if not multiple and abs(drift_ms) < T * 250 and float(np.max(np.abs(step), initial=0.0)) < T * 250:
        frac = (d_mid / T + 0.5) % 1 - 0.5
        if abs(frac) > HALF_BEAT_FRAC:
            half = True
            findings.append(dict(check="half-beat", severity="major",
                                 text=f"Rekordbox's beats sit {abs(frac) * T * 1000:.0f} ms from the kicks, about half a beat "
                                      f"({T * 500:.0f} ms). Either the grid is on the off-beat, or the track has off-beat bass and Rekordbox "
                                      f"is right. Listen to both before accepting."))

    if new_bpm is not None or half:
        Tn = 60.0 / (new_bpm or rb_bpm)
        idx = np.flatnonzero(span)
        if multiple:
            # beat count changes: take position from the fitted kicks, bar 1 from Rekordbox's first bar line in the span
            first = g.beat0 + np.ceil((t[0] - g.beat0) / Tn - 1e-6) * Tn
            n = int((t[-1] - first) / Tn) + 1
            times = first + np.arange(n) * Tn
            bar1 = idx[nums[idx] == 1]
            ref = int(np.argmin(np.abs(times - t[bar1[0]]))) if len(bar1) else 0
            numbers = ((np.arange(n) - ref) % 4) + 1
        else:
            # same beats, new spacing: keep Rekordbox's average position over the kicked part
            i = np.arange(len(t))
            c = float(np.median(t[idx] - idx * Tn))
            times = c + i * Tn
            if half:
                times = times - ((d_mid / T + 0.5) % 1 - 0.5) * T  # onto the kicks, by the shorter way round
            numbers = nums.copy()
            keep = times >= 0
            times, numbers = times[keep], numbers[keep]
        res["proposal"] = dict(bpm=round(float(new_bpm or rb_bpm), 2), first_beat_ms=round(float(times[0]) * 1000, 1),
                               first_beat_number=int(numbers[0]), n_beats=int(len(times)),
                               times=[round(float(v), 4) for v in times], numbers=[int(v) for v in numbers])
    res["status"] = "flagged" if any(f["severity"] == "major" for f in findings) else ("note" if findings else "pass")
    return res


# ---------- click previews ----------

def click_track(x: np.ndarray, times: np.ndarray, numbers: np.ndarray, start: float, length: float = 16.0) -> np.ndarray:
    a = max(int(start * gridfit.SR), 0)
    b = min(a + int(length * gridfit.SR), len(x))
    y = x[a:b].astype(np.float32) * 0.7
    n = int(0.012 * gridfit.SR)
    env = np.exp(-np.arange(n) / (0.003 * gridfit.SR))
    for tt, num in zip(times, numbers):
        pos = int(round(tt * gridfit.SR)) - a
        if 0 <= pos and pos + n <= len(y):
            f = 2400.0 if num == 1 else 1500.0
            y[pos:pos + n] += (0.6 * np.sin(2 * np.pi * f * np.arange(n) / gridfit.SR) * env).astype(np.float32)
    return np.clip(y, -1.0, 1.0)


def write_m4a(y: np.ndarray, dst: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(gridfit.SR), "-ac", "1", "-i", "-",
                    "-c:a", "aac", "-b:a", "160k", dst], input=y.tobytes(), check=True)


def _peaks(x: np.ndarray, a: float, b: float, bins: int, band: tuple = (None, 150.0)) -> list[float]:
    """Peak levels of one band. The default is the kick band, below 150 Hz."""
    i0, i1 = max(int(a * gridfit.SR), 0), int(b * gridfit.SR)
    pad = gridfit.SR // 4
    lo = gridfit._band(x[max(i0 - pad, 0):i1 + pad].astype(np.float64), *band)
    seg = np.abs(lo[i0 - max(i0 - pad, 0):][:i1 - i0])
    if len(seg) < bins:
        return []
    seg = seg[:len(seg) // bins * bins].reshape(bins, -1).max(1)
    return [round(float(v), 3) for v in seg / max(float(seg.max()), 1e-6)]


def make_previews(tr: dict, res: dict, rb: dict, g: gridfit.Grid, x: np.ndarray | None, out_dir: str = OUT, length: float = 16.0) -> dict:
    """Two clips where the grids disagree most: Rekordbox's grid, and the proposal.
    Also waveform peaks and beat positions for drawing the same stretch."""
    if x is None:
        x = gridfit.decode(tr["path"])
    d = os.path.join(out_dir, "previews")
    os.makedirs(d, exist_ok=True)
    start = max((g.last_kick or g.duration) - 20.0, 0.0)
    if any(f["check"] in ("half-beat", "tempo-multiple") for f in res["findings"]):
        start = max((g.first_kick or g.duration / 3) + 16 * 60.0 / g.bpm, 0.0)
    p = res["proposal"]
    out = {}
    for tag, times, numbers in (("rekordbox", rb["times"], rb["beats"]), ("proposed", np.array(p["times"]), np.array(p["numbers"]))):
        dst = os.path.join(d, f"{tr['content_id']}-{tag}.m4a")
        write_m4a(click_track(x, times, numbers, start, length), dst)
        out[tag] = dst
        win = (times >= start) & (times <= start + length)
        out[tag + "_beats"] = [[round(float(t), 4), int(n)] for t, n in zip(times[win], numbers[win])]
    end = min(start + length, len(x) / gridfit.SR)
    out.update(start_s=round(start, 3), length_s=round(end - start, 3), wave=_peaks(x, start, end, 1600), wave_hi=_peaks(x, start, end, 1600, (1000.0, None)))
    # close-up: one second around a proposed beat near the middle of the clip
    pt = np.array(p["times"])
    mid = pt[np.argmin(np.abs(pt - (start + length / 2)))] if len(pt) else start + length / 2
    z0 = max(float(mid) - 0.3, 0.0)
    out.update(zoom_start_s=round(z0, 4), zoom_length_s=1.0, zoom_wave=_peaks(x, z0, z0 + 1.0, 1000), zoom_wave_hi=_peaks(x, z0, z0 + 1.0, 1000, (1000.0, None)))
    return out


# ---------- driver ----------

def work(args) -> dict:
    tr, hint, previews, out_dir, want_cues = args
    row = {k: tr[k] for k in ("content_id", "playlist", "title", "artist", "path")}
    row["existing_cues"] = tr.get("existing", [])
    try:
        if not os.path.exists(tr["path"]):
            return dict(row, status="unaudited", reason="audio file is missing", findings=[])
        rb = read_grid(tr["anlz"]) if tr["anlz"] else None
        if rb is None:
            return dict(row, status="unaudited", reason="Rekordbox has not analysed this track yet", findings=[])
        g, x = _cached_fit(tr["path"], hint)
        res = audit_grid(g, rb)
        cm = crate_multiple(rb, hint)
        if cm:  # takes precedence: the other checks assume the tempo is the right multiple
            res.update(status="flagged", findings=[cm["finding"]], proposal=cm["proposal"])
            res.pop("reason", None)
        res["crate_bpm"] = hint
        if previews and res.get("proposal"):
            if x is None:
                x = gridfit.decode(tr["path"])
            res["previews"] = make_previews(tr, res, rb, g, x, out_dir)
        not_4x4 = res["status"] == "unaudited" or any(f["check"] == "tempo-multiple" for f in res["findings"])
        if want_cues and not_4x4:
            res["cues"] = {"rekordbox": dict(ok=False, reason="not a four-on-the-floor track at this tempo; cues left to you")}
        elif want_cues:
            if x is None:
                x = gridfit.decode(tr["path"])
            res["cues"] = {"rekordbox": cuelib.place_cues(x, rb["times"], rb["beats"])}
            p = res.get("proposal")
            if p and p.get("times"):
                res["cues"]["proposed"] = cuelib.place_cues(x, np.array(p["times"]), np.array(p["numbers"]))
            res["overview"] = cuelib.overview(x)
        return dict(row, **res)
    except Exception as e:  # noqa: BLE001
        return dict(row, status="error", reason=repr(e), findings=[])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--playlist", action="append", default=[])
    ap.add_argument("--playlist-id", action="append", default=[])
    ap.add_argument("--list-playlists", action="store_true", help="print playlists as JSON and exit")
    ap.add_argument("--crate-xml")
    ap.add_argument("--rb-dir", default=RB_DIR)
    ap.add_argument("--previews", action="store_true")
    ap.add_argument("--cues", action="store_true", help="also place the cue template on each track")
    ap.add_argument("--progress", action="store_true", help="print one JSON line per finished track instead of the table")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=os.path.join(OUT, "audit.json"))
    a = ap.parse_args()

    db, tmp = open_library(a.rb_dir)
    try:
        if a.list_playlists:
            print(json.dumps(list_playlists(db)))
            return
        tracks = playlist_tracks(db, a.playlist, a.rb_dir, a.playlist_id)
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
    if not tracks:
        raise SystemExit("no tracks found in the chosen playlists")
    hints = crate_bpms(a.crate_xml)

    out_dir = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(out_dir, exist_ok=True)
    shutil.rmtree(os.path.join(out_dir, "previews"), ignore_errors=True)
    rows = []
    with ProcessPoolExecutor(a.jobs) as ex:
        for r in ex.map(work, [(t, hints.get(t["path"]) or None, a.previews, out_dir, a.cues) for t in tracks]):
            rows.append(r)
            if a.progress:
                print(json.dumps(dict(done=len(rows), total=len(tracks), title=r["title"])), flush=True)
    # The full beat lists are large and the review needs only the summary, so
    # they go to grids.json beside the audit, where write-back reads them.
    grids = {}
    for r in rows:
        if r.get("proposal"):
            grids[r["content_id"]] = {k: r["proposal"][k] for k in ("bpm", "times", "numbers") if k in r["proposal"]}
            r["proposal"] = {k: v for k, v in r["proposal"].items() if k not in ("times", "numbers")}
    json.dump(grids, open(os.path.join(out_dir, "grids.json"), "w"))
    json.dump(rows, open(a.out, "w"), indent=1)
    if a.progress:
        return

    order = {"flagged": 0, "note": 1, "error": 2, "unaudited": 3, "pass": 4}
    print(f"{'status':9} {'rb bpm':>7} {'fit':>7} {'drift':>6} {'offs':>6} {'conf':>4}  track")
    for r in sorted(rows, key=lambda r: order.get(r["status"], 9)):
        print(f"{r['status']:9} {r.get('rb_bpm', 0):7.2f} {r.get('fit_bpm', 0):7.2f} {r.get('drift_ms', 0):6.1f} {r.get('offset_ms', 0):6.1f} "
              f"{r.get('confidence', 0):4.2f}  {r['artist'][:16]} - {r['title'][:34]}")
        for f in r["findings"]:
            print(f"{'':12}[{f['check']}] {f['text']}")
        if r.get("proposal"):
            p = r["proposal"]
            print(f"{'':12}-> propose {p['bpm']:.2f} BPM, first beat at {p['first_beat_ms']:.0f} ms")
        if r.get("reason"):
            print(f"{'':12}{r['reason']}")
    counts = {s: sum(r["status"] == s for r in rows) for s in order}
    print("\n" + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    print(f"written: {a.out}")


if __name__ == "__main__":
    main()

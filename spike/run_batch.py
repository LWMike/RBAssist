# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy"]
# ///
"""Run the grid fitter over tracks in Crate's bridge XML and report.

usage: uv run spike/run_batch.py <crate.xml> [--playlist NAME] [--limit N] [--previews N]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import gridfit  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "out")


def load(xml_path: str, playlist: str | None):
    root = ET.parse(xml_path).getroot()
    tracks = {}
    for t in root.find("COLLECTION"):
        loc = urllib.parse.unquote(t.get("Location").replace("file://localhost", ""))
        tracks[t.get("TrackID")] = dict(
            id=t.get("TrackID"), name=t.get("Name"), artist=t.get("Artist") or "",
            crate_bpm=float(t.get("AverageBpm") or 0), path=loc,
        )
    if playlist:
        for node in root.iter("NODE"):
            if node.get("Name") == playlist and node.get("Type") == "1":
                return [tracks[k.get("Key")] for k in node.findall("TRACK")]
        raise SystemExit(f"playlist {playlist!r} not found")
    return list(tracks.values())


def work(args):
    tr, want_preview = args
    t0 = time.time()
    try:
        x = gridfit.decode(tr["path"])
        g = gridfit.fit(x, tr['crate_bpm'] or None)
    except Exception as e:  # noqa: BLE001
        return dict(tr, error=repr(e))
    res = dict(tr, **g.to_dict(), secs=round(time.time() - t0, 1))
    if want_preview:
        os.makedirs(os.path.join(OUT, "previews"), exist_ok=True)
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in f"{tr['artist']} - {tr['name']}")[:70]
        T = 60.0 / g.bpm
        starts = {
            "1-start": max((g.first_kick or g.anchor) - 4 * T, 0.0),
            "2-end": max((g.last_kick or g.duration) - 20.0, 0.0),
        }
        for tag, s in starts.items():
            y = gridfit.click_preview(x, g, s)
            dst = os.path.join(OUT, "previews", f"{tr['id']} {safe} [{tag}].m4a")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(gridfit.SR), "-ac", "1", "-i", "-", "-c:a", "aac", "-b:a", "160k", dst],
                input=y.tobytes(), check=True,
            )
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xml")
    ap.add_argument("--playlist")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--previews", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()

    tracks = load(a.xml, a.playlist)
    if a.limit:
        step = max(len(tracks) // a.limit, 1)
        tracks = tracks[::step][: a.limit]
    pv = set()
    if a.previews:
        step = max(len(tracks) // a.previews, 1)
        pv = {t["id"] for t in tracks[::step][: a.previews]}

    os.makedirs(OUT, exist_ok=True)
    with ProcessPoolExecutor(a.jobs) as ex:
        results = list(ex.map(work, [(t, t["id"] in pv) for t in tracks]))
    with open(os.path.join(OUT, "results.json"), "w") as f:
        json.dump(results, f, indent=1)

    good = [r for r in results if "error" not in r]
    print(f"{len(good)}/{len(results)} analysed, median {np.median([r['secs'] for r in good]):.1f}s per track\n")
    print(f"{'bpm':>7} {'fit':>8} {'crate':>6} {'mad':>5} {'drift':>6} {'wand':>5} {'cov':>4} {'bar':>4} {'conf':>4} {'kick@':>6}  track / flags")
    for r in sorted(good, key=lambda r: r["confidence"]):
        fk = f"{r['first_kick']:.2f}" if r["first_kick"] is not None else "-"
        print(f"{r['bpm']:7.2f} {r['bpm_fit']:8.3f} {r['crate_bpm']:6.1f} {r['resid_mad_ms']:5.1f} {r['drift_ms']:6.1f} {r['wander_ms']:5.1f} "
              f"{r['kick_coverage']:4.2f} {r['bar_conf']:4.2f} {r['confidence']:4.2f} {fk:>6}  {r['artist'][:18]} - {r['name'][:30]}  {' '.join(r['flags'])}")
    for r in results:
        if "error" in r:
            print("ERROR", r["name"], r["error"])

    conf = np.array([r["confidence"] for r in good])
    print(f"\nsnapped to integer: {sum(r['snapped'] for r in good)}/{len(good)}")
    print(f"confidence >=0.8: {(conf >= 0.8).sum()}   0.5-0.8: {((conf >= 0.5) & (conf < 0.8)).sum()}   <0.5: {(conf < 0.5).sum()}")
    agree = sum(abs(r["bpm"] - r["crate_bpm"]) < 0.6 for r in good)
    print(f"tempo agrees with Crate (within 0.6 BPM): {agree}/{len(good)}")


if __name__ == "__main__":
    main()

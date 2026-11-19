# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pyrekordbox", "rbox"]
# ///
"""M3 write-back: put accepted grid corrections and cues into Rekordbox.

usage:
  uv run engine/writeback.py check --audit A.json --plan P.json
  uv run engine/writeback.py write --audit A.json --plan P.json --backups DIR
  uv run engine/writeback.py undo  --backups DIR [--force]
  uv run engine/writeback.py status --backups DIR

The plan says only which tracks to write and how:
  [{"content_id": "...", "grid": true|false,
    "cues": null | {"grid": "rekordbox"|"proposed", "replace": true|false}}]
Grid times and cue positions come from the audit file, never from the plan.

Rules (SPEC 3, 4.4):
- Nothing is written while Rekordbox is running.
- Every write takes a timestamped backup of master.db and of each ANLZ file
  it changes; `undo` puts the last backup back.
- A track is skipped if its grid in Rekordbox is no longer the one audited.
- Grid: the DAT file's beat grid and the database tempo (proven in the spike;
  the EXT file is left as it is, which Rekordbox accepts).
- Cues: DjmdCue rows. "Replace" removes the track's existing cues first.
  Otherwise existing hot cues keep their slots and only free slots are filled;
  memory cues already at the same place are not doubled.

Output is JSON on stdout.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import uuid

RB_DIR = os.path.expanduser("~/Library/Pioneer/rekordbox")
DB_FILES = ("master.db", "master.db-wal", "master.db-shm")

KIND = {"A": 1, "B": 2, "C": 3, "D": 5, "E": 6, "F": 7, "G": 8, "H": 9}  # DjmdCue.Kind; 0 is a memory cue
# Rekordbox hot cue colour table indices (ANLZ colour ids) closest to the app's colours.
COLOR_INDEX = {"green": 22, "orange": 36, "blue": 1, "red": 42, "yellow": 33}
SAME_PLACE_MS = 5
GRID_MATCH_MS = 2.0


class Refused(Exception):
    pass


def rekordbox_running() -> bool:
    return subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).returncode == 0


def sha1(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def live_grid(dat: str):
    from pyrekordbox.anlz import AnlzFile
    if not dat or not os.path.exists(dat):
        return None
    tag = AnlzFile.parse_file(dat).get_tag("PQTZ")
    return list(tag.times), list(tag.bpms), list(tag.beats)


def open_db(rb_dir: str, copy: bool):
    """The live database for writing, or a temp copy for checking while Rekordbox is open."""
    logging.disable(logging.CRITICAL)
    from pyrekordbox import MasterDatabase
    if not copy:
        return MasterDatabase(os.path.join(rb_dir, "master.db"))
    import tempfile
    tmp = tempfile.mkdtemp(prefix="rbassist-")
    for n in DB_FILES:
        if os.path.exists(os.path.join(rb_dir, n)):
            shutil.copy2(os.path.join(rb_dir, n), tmp)
    return MasterDatabase(os.path.join(tmp, "master.db"))


# ---------- the plan ----------

def load_audit(path: str) -> list[dict]:
    """The audit rows, with each proposal's full beat list put back from grids.json."""
    rows = json.load(open(path))
    gp = os.path.join(os.path.dirname(os.path.abspath(path)), "grids.json")
    grids = json.load(open(gp)) if os.path.exists(gp) else {}
    for r in rows:
        g = grids.get(r["content_id"])
        if r.get("proposal") and g and abs(g.get("bpm", 0) - r["proposal"]["bpm"]) < 1e-6:
            r["proposal"].update(times=g["times"], numbers=g["numbers"])
    return rows


def build(audit: list[dict], plan: list[dict], db, rb_dir: str) -> list[dict]:
    """Check each plan entry against the audit and the live library and say
    exactly what would be written. Nothing is changed here."""
    rows = {r["content_id"]: r for r in audit}
    share = os.path.join(rb_dir, "share")
    out = []
    for p in plan:
        cid = str(p["content_id"])
        r = rows.get(cid)
        item = dict(content_id=cid, title=(r or {}).get("title", ""), artist=(r or {}).get("artist", ""), ok=True, skip=None,
                    grid=None, cues=None)
        out.append(item)

        def skip(why):
            item.update(ok=False, skip=why)

        if r is None:
            skip("not in the latest audit")
            continue
        c = db.get_content(ID=cid)
        if c is None:
            skip("no longer in the Rekordbox collection")
            continue
        dat = share + c.AnalysisDataPath if c.AnalysisDataPath else None
        g = live_grid(dat)
        if g is None:
            skip("Rekordbox has no beat grid for it")
            continue
        times, bpms, _ = g  # seconds, BPM
        live_bpm = float(sorted(bpms)[len(bpms) // 2])
        first_ms = float(times[0]) * 1000
        if r.get("rb_first_beat_ms") is not None and (abs(first_ms - r["rb_first_beat_ms"]) > GRID_MATCH_MS or abs(live_bpm - r.get("rb_bpm", live_bpm)) > 0.005):
            skip("its grid in Rekordbox changed after the audit; audit it again")
            continue
        item["anlz"] = dat

        if p.get("grid"):
            prop = r.get("proposal")
            if not prop or not prop.get("times"):
                skip("no grid correction in the audit")
                continue
            T = 60000.0 / prop["bpm"]
            d = prop["first_beat_ms"] - first_ms
            item["grid"] = dict(old_bpm=round(live_bpm, 2), bpm=prop["bpm"], beats=len(prop["times"]), old_beats=len(times),
                                first_beat_ms=prop["first_beat_ms"], shift_ms=round(((d / T + 0.5) % 1 - 0.5) * T, 1))

        cq = p.get("cues")
        if cq:
            which = cq.get("grid", "rekordbox")
            if which == "proposed" and not p.get("grid"):
                skip("cues were placed on the grid correction, which is not being written")
                continue
            cs = (r.get("cues") or {}).get(which)
            if not cs or not cs.get("ok"):
                skip("no cues in the audit for it")
                continue
            existing = [q for q in (c.Cues or []) if not q.rb_local_deleted]
            replace = bool(cq.get("replace"))
            taken = set() if replace else {q.Kind for q in existing if q.Kind}
            mem_at = set() if replace else {q.InMsec for q in existing if not q.Kind}
            hot = [h for h in cs["hot"] if KIND[h["slot"]] not in taken]
            kept_slots = [h["slot"] for h in cs["hot"] if KIND[h["slot"]] in taken]
            memory = [m for m in cs["memory"] if not any(abs(round(m["time"] * 1000) - t) <= SAME_PLACE_MS for t in mem_at)]
            item["cues"] = dict(replace=replace, remove=len(existing) if replace else 0, hot=hot, memory=memory,
                                kept_slots=kept_slots, on=which)
            if not hot and not memory:
                item["cues"]["nothing"] = True
        if not item["grid"] and (not item["cues"] or item["cues"].get("nothing")):
            skip("nothing to write: every cue slot is already taken" if item["cues"] else "nothing to write")
    return out


# ---------- writing ----------

def _cue_row(m, c, kind: int, in_s: float, end_s: float | None, comment: str, color_idx: int, now):
    in_ms = int(round(in_s * 1000))
    out_ms = int(round(end_s * 1000)) if end_s is not None else -1
    return m.DjmdCue(
        ID=None, ContentID=c.ID, InMsec=in_ms, InFrame=in_ms * 150 // 1000, InMpegFrame=0, InMpegAbs=0,
        OutMsec=out_ms, OutFrame=(out_ms * 150 // 1000 if out_ms > 0 else 0), OutMpegFrame=0, OutMpegAbs=0,
        Kind=kind, Color=255, ColorTableIndex=color_idx, ActiveLoop=0, Comment=comment, BeatLoopSize=0, CueMicrosec=0,
        ContentUUID=c.UUID, UUID=str(uuid.uuid4()), rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
        rb_local_synced=0, created_at=now, updated_at=now)


def write_grid(dat: str, times_s: list[float], numbers: list[int], bpm: float):
    import rbox
    a = rbox.Anlz(dat)
    bg = a.get_beat_grid()
    tempo = int(round(bpm * 100))
    bg.beats = [rbox.anlz.Beat(beat_number=int(n), tempo=tempo, time=int(round(t * 1000))) for t, n in zip(times_s, numbers)]
    a.set_beat_grid(bg)
    a.dump_copy(dat + ".rbassist-new")
    os.replace(dat + ".rbassist-new", dat)


def backup(rb_dir: str, backups: str, items: list[dict]) -> tuple[str, list[dict]]:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = os.path.join(backups, stamp)
    os.makedirs(os.path.join(d, "anlz"), exist_ok=True)
    for n in DB_FILES:
        if os.path.exists(os.path.join(rb_dir, n)):
            shutil.copy2(os.path.join(rb_dir, n), d)
    files = []
    for i, it in enumerate(items):
        if it.get("grid"):
            dst = os.path.join(d, "anlz", f"{i:04d}-{os.path.basename(it['anlz'])}")
            shutil.copy2(it["anlz"], dst)
            files.append(dict(original=it["anlz"], copy=dst))
    return d, files


def write(audit: list[dict], plan: list[dict], rb_dir: str, backups: str) -> dict:
    if rekordbox_running():
        raise Refused("Rekordbox is open. Quit it, then write again.")
    db = open_db(rb_dir, copy=False)
    items = build(audit, plan, db, rb_dir)
    todo = [it for it in items if it["ok"]]
    if not todo:
        db.close()
        return dict(written=0, items=items, backup=None)
    db.close()  # copy the files while nothing has them open
    bdir, files = backup(rb_dir, backups, todo)
    manifest = dict(created=datetime.datetime.now().isoformat(timespec="seconds"), rb_dir=rb_dir, db_files=[n for n in DB_FILES if os.path.exists(os.path.join(bdir, n))],
                    anlz=files, tracks=[dict(content_id=it["content_id"], title=it["title"], grid=bool(it["grid"]),
                                             hot=len((it["cues"] or {}).get("hot", [])), memory=len((it["cues"] or {}).get("memory", [])),
                                             removed=(it["cues"] or {}).get("remove", 0)) for it in todo],
                    state="started")
    mpath = os.path.join(bdir, "manifest.json")
    json.dump(manifest, open(mpath, "w"), indent=1)

    from pyrekordbox.masterdb import models as m
    rows = {r["content_id"]: r for r in audit}
    db = open_db(rb_dir, copy=False)
    now = datetime.datetime.now()
    used_ids = {q.ID for q in db.get_cue().all()}

    def new_id():
        while True:
            v = str(random.randint(10**8, 4 * 10**9))
            if v not in used_ids:
                used_ids.add(v)
                return v

    grids_done = []
    try:
        for it in todo:
            c = db.get_content(ID=it["content_id"])
            r = rows[it["content_id"]]
            if it.get("grid"):
                p = r["proposal"]
                write_grid(it["anlz"], p["times"], p["numbers"], p["bpm"])
                grids_done.append(it["anlz"])
                c.BPM = int(round(p["bpm"] * 100))
            cq = it.get("cues")
            if cq and not cq.get("nothing"):
                if cq["replace"]:
                    for q in list(c.Cues or []):
                        db.delete(q)
                for h in cq["hot"]:
                    row = _cue_row(m, c, KIND[h["slot"]], h["time"], h.get("end"), h["name"], COLOR_INDEX.get(h["color"], 0), now)
                    row.ID = new_id()
                    db.add(row)
                for mc in cq["memory"]:
                    row = _cue_row(m, c, 0, mc["time"], None, mc["name"], 0, now)
                    row.ID = new_id()
                    db.add(row)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        for f in files:  # put back any grid already rewritten
            if f["original"] in grids_done:
                shutil.copy2(f["copy"], f["original"])
        manifest["state"] = "failed, nothing kept"
        json.dump(manifest, open(mpath, "w"), indent=1)
        raise
    db.close()
    manifest["state"] = "written"
    manifest["after"] = dict(db=sha1(os.path.join(rb_dir, "master.db")), anlz={f["original"]: sha1(f["original"]) for f in files})
    json.dump(manifest, open(mpath, "w"), indent=1)
    return dict(written=len(todo), items=items, backup=bdir)


# ---------- undo ----------

def last_backup(backups: str) -> tuple[str, dict] | None:
    if not os.path.isdir(backups):
        return None
    for name in sorted(os.listdir(backups), reverse=True):
        mp = os.path.join(backups, name, "manifest.json")
        if os.path.exists(mp):
            m = json.load(open(mp))
            if m.get("state") == "written":
                return os.path.join(backups, name), m
    return None


def status(backups: str) -> dict:
    lb = last_backup(backups)
    out = dict(rekordbox_running=rekordbox_running(), last=None)
    if lb:
        d, m = lb
        rb_dir = m["rb_dir"]
        changed = sha1(os.path.join(rb_dir, "master.db")) != m.get("after", {}).get("db")
        out["last"] = dict(dir=d, created=m["created"], tracks=m["tracks"], changed_since=changed)
    return out


def undo(backups: str, force: bool) -> dict:
    if rekordbox_running():
        raise Refused("Rekordbox is open. Quit it, then undo again.")
    lb = last_backup(backups)
    if not lb:
        raise Refused("There is no write to undo.")
    d, m = lb
    rb_dir = m["rb_dir"]
    if not force and sha1(os.path.join(rb_dir, "master.db")) != m.get("after", {}).get("db"):
        raise Refused("The library has changed since this write (Rekordbox saved something). Undo would lose those changes too; "
                      "confirm to undo anyway.")
    # keep what is there now, in case the undo itself is regretted
    keep = d + "-undone-from"
    os.makedirs(keep, exist_ok=True)
    for n in DB_FILES:
        if os.path.exists(os.path.join(rb_dir, n)):
            shutil.copy2(os.path.join(rb_dir, n), keep)
    for n in DB_FILES:
        live = os.path.join(rb_dir, n)
        if n in m["db_files"]:
            shutil.copy2(os.path.join(d, n), live)
        elif os.path.exists(live):
            os.remove(live)  # a -wal/-shm that did not exist at backup time
    for f in m["anlz"]:
        shutil.copy2(f["copy"], f["original"])
    m["state"] = "undone"
    m["undone"] = datetime.datetime.now().isoformat(timespec="seconds")
    json.dump(m, open(os.path.join(d, "manifest.json"), "w"), indent=1)
    return dict(undone=d, tracks=m["tracks"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["check", "write", "undo", "status"])
    ap.add_argument("--audit")
    ap.add_argument("--plan")
    ap.add_argument("--backups")
    ap.add_argument("--rb-dir", default=RB_DIR)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        if a.action == "check":
            db = open_db(a.rb_dir, copy=True)
            items = build(load_audit(a.audit), json.load(open(a.plan)), db, a.rb_dir)
            res = dict(rekordbox_running=rekordbox_running(), items=items)
        elif a.action == "write":
            res = write(load_audit(a.audit), json.load(open(a.plan)), a.rb_dir, a.backups)
        elif a.action == "undo":
            res = undo(a.backups, a.force)
        else:
            res = status(a.backups)
    except Refused as e:
        print(json.dumps(dict(refused=str(e))))
        return
    for it in res.get("items", []):
        it.pop("anlz", None)
    print(json.dumps(res))


if __name__ == "__main__":
    main()

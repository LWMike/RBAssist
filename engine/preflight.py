# /// script
# requires-python = ">=3.11"
# dependencies = ["pyrekordbox"]
# ///
"""M4 USB preflight (SPEC 4.6): will these playlists play right from a USB stick?

usage:
  uv run engine/preflight.py check --playlist ID [...] [--flac-ok] [--rb-dir DIR]
  uv run engine/preflight.py usb

check  every track of the chosen Rekordbox playlists against what a CDJ needs:
       the file on disk, an analysis with a beat grid, a key, a format and
       sample rate the players read, and cues inside the CDJ limits (8 hot,
       10 memory). Reads a copy of master.db, so Rekordbox can be open.
usb    mounted external volumes, with filesystem, free space and whether
       Rekordbox has exported to them before.

Each issue has a level: "error" (will not play or will play without a grid),
"warn" (plays, but not as prepped or not on every player), "info".
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

SF_DATALESS = 0x40000000
HOT_LIMIT, MEMORY_LIMIT = 8, 10
SAFE_EXT = {".mp3", ".m4a", ".aac", ".mp4", ".wav", ".aif", ".aiff"}
STREAMING_FILE_TYPE = 20


def issue(level: str, code: str, text: str) -> dict:
    return dict(level=level, code=code, text=text)


def open_copy(rb_dir: str):
    """pyrekordbox on a temp copy of master.db (+ WAL), and the temp dir."""
    logging.disable(logging.CRITICAL)
    from pyrekordbox import MasterDatabase
    tmp = tempfile.mkdtemp(prefix="rbassist-")
    for name in ("master.db", "master.db-wal", "master.db-shm"):
        if os.path.exists(os.path.join(rb_dir, name)):
            shutil.copy2(os.path.join(rb_dir, name), tmp)
    return MasterDatabase(os.path.join(tmp, "master.db")), tmp


def grid_beats(rb_dir: str, analysis_path: str | None) -> int | None:
    """Beats in the track's PQTZ grid; None if the analysis file is missing."""
    if not analysis_path:
        return None
    path = os.path.join(rb_dir, "share", analysis_path.lstrip("/"))
    if not os.path.exists(path):
        return None
    from pyrekordbox.anlz import AnlzFile
    try:
        tag = AnlzFile.parse_file(path).get_tag("PQTZ")
        return len(tag.get_times())
    except Exception:  # noqa: BLE001  no PQTZ tag or unreadable file
        return 0


def file_issues(c, flac_ok: bool) -> list[dict]:
    out = []
    if c.FileType == STREAMING_FILE_TYPE or not c.FolderPath or not c.FolderPath.startswith("/"):
        return [issue("error", "streaming", "Streaming track: it can't be exported to USB.")]
    try:
        st = os.stat(c.FolderPath)
    except OSError:
        return [issue("error", "missing", "File not found on this Mac.")]
    if getattr(st, "st_flags", 0) & SF_DATALESS:
        out.append(issue("error", "online-only", "Only in Dropbox: download it before exporting."))
    ext = os.path.splitext(c.FolderPath)[1].lower()
    if ext == ".flac":
        if not flac_ok:
            out.append(issue("warn", "flac", "FLAC: plays on CDJ-2000NXS2, CDJ-3000 and newer XDJs only."))
    elif ext not in SAFE_EXT:
        out.append(issue("error", "format", f"{ext or 'Unknown'} files don't play on CDJs."))
    if (c.SampleRate or 0) > 48000:
        out.append(issue("warn", "rate", f"{c.SampleRate / 1000:g} kHz: older players need 48 kHz or less."))
    if (c.BitDepth or 0) > 24:
        out.append(issue("warn", "depth", f"{c.BitDepth}-bit: players read 24-bit at most."))
    return out


def check_track(db, rb_dir: str, c, flac_ok: bool) -> dict:
    issues = file_issues(c, flac_ok)
    streaming = any(i["code"] == "streaming" for i in issues)
    beats = None if streaming else grid_beats(rb_dir, c.AnalysisDataPath)
    if not streaming:
        if beats is None:
            issues.append(issue("error", "unanalysed", "Not analysed: no waveform or beat grid on the player."))
        elif beats == 0:
            issues.append(issue("error", "nogrid", "Analysed without a beat grid: no sync or quantize."))
        if not c.KeyID:
            issues.append(issue("warn", "nokey", "No key."))
    hot = memory = 0
    for cue in db.get_cue(ContentID=c.ID).all():
        if cue.rb_local_deleted:
            continue
        if cue.Kind == 0:
            memory += 1
        else:
            hot += 1
    if hot > HOT_LIMIT:
        issues.append(issue("warn", "hot", f"{hot} hot cues: players show {HOT_LIMIT}."))
    if memory > MEMORY_LIMIT:
        issues.append(issue("warn", "memory", f"{memory} memory cues: CDJs show the first {MEMORY_LIMIT}."))
    if not streaming:
        if hot == 0 and memory == 0:
            issues.append(issue("warn", "nocues", "No cues: not prepped."))
        elif memory == 0:
            issues.append(issue("warn", "noload", "No memory cue, so it loads at the start of the file, not the first kick."))
    return dict(content_id=str(c.ID), title=c.Title or "", artist=(c.Artist.Name if c.Artist else "") or "",
                path=c.FolderPath or "", size=int(c.FileSize or 0), bpm=(c.BPM or 0) / 100,
                key=(c.Key.ScaleName if c.Key else "") or "", hot=hot, memory=memory, beats=beats, issues=issues)


def check(rb_dir: str, ids: list[str], flac_ok: bool) -> dict:
    db, tmp = open_copy(rb_dir)
    try:
        lists = {str(p.ID): p for p in db.get_playlist().all()}
        unknown = [i for i in ids if i not in lists]
        if unknown:
            raise SystemExit("no such playlist: " + ", ".join(unknown))
        seen: dict[str, dict] = {}
        out = []
        for pid in ids:
            pl = lists[pid]
            rows = []
            for s in sorted(pl.Songs, key=lambda s: s.TrackNo or 0):
                c = s.Content
                if c is None or c.rb_local_deleted:
                    continue
                key = str(c.ID)
                if key not in seen:
                    seen[key] = check_track(db, rb_dir, c, flac_ok)
                rows.append(dict(seen[key], duplicate=any(r["content_id"] == key for r in rows)))
            out.append(dict(id=pid, name=pl.Name, tracks=rows,
                            errors=sum(any(i["level"] == "error" for i in r["issues"]) for r in rows),
                            warnings=sum(any(i["level"] == "warn" for i in r["issues"]) for r in rows)))
        return dict(playlists=out, tracks=len(seen), size=sum(t["size"] for t in seen.values()), flac_ok=flac_ok)
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


FS = {  # filesystem personality → (label, verdict, note)
    "msdos": ("FAT32", "ok", "Every CDJ and XDJ reads it."),
    "exfat": ("exFAT", "warn", "Only newer players (CDJ-3000, XDJ-XZ/RX3 and later) read it."),
    "hfs": ("Mac OS Extended", "warn", "CDJ-2000NXS2 and CDJ-3000 read it; older and XDJ players may not."),
    "apfs": ("APFS", "error", "CDJs can't read APFS. Reformat as FAT32 (MS-DOS) or Mac OS Extended."),
    "ntfs": ("NTFS", "error", "CDJs can't read NTFS. Reformat as FAT32."),
}


def exported(mount: str) -> bool:
    """Has Rekordbox exported here? Its folder is PIONEER, hidden, or .PIONEER on Mac-formatted sticks."""
    return any(os.path.exists(os.path.join(mount, d, "rekordbox", f))
               for d in ("PIONEER", ".PIONEER") for f in ("export.pdb", "exportLibrary.db"))


def stick_library(mount: str, rb_dir: str) -> dict | None:
    """What Rekordbox has exported to the stick (OneLibrary, from a temp copy), and from which libraries.

    Exporting a track that the stick already has from another Rekordbox library adds a second copy
    ("Name-1.flac") instead of updating it, so the user should export from the Mac that made the stick.
    """
    src = next((os.path.join(mount, d, "rekordbox") for d in (".PIONEER", "PIONEER")
                if os.path.exists(os.path.join(mount, d, "rekordbox", "exportLibrary.db"))), None)
    if not src:
        return None
    logging.disable(logging.CRITICAL)
    from collections import Counter
    from pyrekordbox.onelibrary import Content, OneLibrary, Playlist
    tmp = tempfile.mkdtemp(prefix="rbassist-usb-")
    try:
        for name in ("exportLibrary.db", "exportLibrary.db-wal", "exportLibrary.db-shm"):
            if os.path.exists(os.path.join(src, name)):
                shutil.copy2(os.path.join(src, name), tmp)
        lib = OneLibrary(os.path.join(tmp, "exportLibrary.db"))
        rows = lib.session.query(Content).all()
        mine = None
        try:
            db, tmp2 = open_copy(rb_dir)
            try:
                mine = int(db.get_property().first().DBID)
            finally:
                db.close()
                shutil.rmtree(tmp2, ignore_errors=True)
        except Exception:  # noqa: BLE001  no local library: origin unknown
            pass
        origin = Counter(r.masterDbId for r in rows)
        same = Counter(((r.title or "").lower(), r.artist_id_artist, r.length) for r in rows)
        out = dict(tracks=len(rows), playlists=lib.session.query(Playlist).count(),
                   from_this_mac=origin.get(mine, 0) if mine is not None else None,
                   duplicates=sum(n - 1 for n in same.values() if n > 1))
        out["from_elsewhere"] = len(rows) - out["from_this_mac"] if mine is not None else None
        lib.close()
        return out
    except Exception:  # noqa: BLE001  unreadable or newer format
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def usb(rb_dir: str = os.path.expanduser("~/Library/Pioneer/rekordbox")) -> dict:
    vols = []
    for name in sorted(os.listdir("/Volumes")):
        mount = os.path.join("/Volumes", name)
        if os.path.realpath(mount) == "/" or not os.path.ismount(mount):
            continue
        try:
            info = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", mount], capture_output=True, check=True).stdout)
        except (subprocess.CalledProcessError, plistlib.InvalidFileException, OSError):
            continue
        external = not info.get("Internal", True) or info.get("RemovableMediaOrExternalDevice", False)
        if not external or info.get("VirtualOrPhysical") == "Virtual":  # skips disk images
            continue
        fs = (info.get("FilesystemType") or "").lower()
        label, verdict, note = FS.get(fs, (info.get("FilesystemName") or fs or "Unknown", "error", "CDJs may not read this filesystem."))
        free = shutil.disk_usage(mount).free
        vols.append(dict(name=name, mount=mount, filesystem=label, verdict=verdict, note=note,
                         free=free, total=int(info.get("TotalSize") or info.get("Size") or 0),
                         exported=exported(mount), library=stick_library(mount, rb_dir)))
    return dict(volumes=vols)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["check", "usb"])
    ap.add_argument("--playlist", action="append", default=[])
    ap.add_argument("--flac-ok", action="store_true")
    ap.add_argument("--rb-dir", default=os.path.expanduser("~/Library/Pioneer/rekordbox"))
    a = ap.parse_args()
    if a.action == "check":
        if not a.playlist:
            raise SystemExit("check needs --playlist")
        res = check(a.rb_dir, a.playlist, a.flac_ok)
    else:
        res = usb(a.rb_dir)
    print(json.dumps(res))


if __name__ == "__main__":
    sys.exit(main())

# /// script
# requires-python = ">=3.11"
# dependencies = ["pyrekordbox"]
# ///
"""M4 Crate intake (SPEC 4.1): Crate's playlists into a Rekordbox XML.

usage:
  uv run engine/intake.py scan     --crate-xml X [--root DIR]
  uv run engine/intake.py build    --crate-xml X [--root DIR] --playlist NAME [...] --out FILE
  uv run engine/intake.py download --crate-xml X [--root DIR] --playlist NAME [...] [--progress]

scan     every playlist with its tracks, each remapped to this Mac and checked:
         found, missing, or a Dropbox online-only file; and whether Rekordbox
         already has it. Reads a copy of master.db, so Rekordbox can be open.
build    a Rekordbox XML with the chosen playlists under a "Crate" folder, in
         Crate's order. No grid, tempo or key, so Rekordbox analyses the
         tracks itself; Crate's energy and Camelot key stay in the comment.
         Missing files are left out and listed.
download makes the chosen playlists' online-only files local by reading them,
         which has Dropbox fetch them.

Paths: Crate writes paths for the Mac it runs on. With --root, the part after
Crate's common library folder is put under DIR. Without it, a path is used as
it is if the file exists, else with the home folder swapped for this one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET

SF_DATALESS = 0x40000000  # macOS: placeholder whose contents are in the cloud
AUDIO_ATTRS = ("Name", "Artist", "Album", "Genre", "Kind", "Size", "TotalTime", "TrackNumber", "Year", "SampleRate", "Comments")


def loc_to_path(loc: str) -> str:
    p = loc.replace("file://localhost", "", 1)
    if p.startswith("file://"):
        p = p[len("file://"):]
    return urllib.parse.unquote(p)


def path_to_loc(p: str) -> str:
    return "file://localhost" + urllib.parse.quote(p, safe="/()!,'&+=@$;~-_.")


def read_crate(xml_path: str):
    root = ET.parse(xml_path).getroot()
    tracks = {t.get("TrackID"): t for t in root.find("COLLECTION")}
    playlists = []

    def walk(node, folder):
        for n in node.findall("NODE"):
            if n.get("Type") == "0":
                walk(n, folder + ([n.get("Name")] if n.get("Name") not in ("ROOT",) else []))
            else:
                playlists.append(dict(name=n.get("Name") or "", folder=" / ".join(folder[1:] if folder[:1] == ["Crate"] else folder),
                                      ids=[t.get("Key") for t in n.findall("TRACK")]))
    walk(root.find("PLAYLISTS"), [])
    return tracks, playlists


def crate_root(tracks) -> str:
    dirs = [os.path.dirname(loc_to_path(t.get("Location"))) for t in tracks.values() if t.get("Location")]
    return os.path.commonpath(dirs) if dirs else "/"


def remapper(tracks, root: str | None):
    src_root = crate_root(tracks)
    home = os.path.expanduser("~")

    def remap(p: str) -> str:
        if root:
            rel = os.path.relpath(p, src_root)
            return os.path.normpath(os.path.join(root, rel)) if not rel.startswith("..") else p
        if os.path.exists(p):
            return p
        parts = p.split("/")
        if len(parts) > 3 and parts[1] == "Users":
            alt = os.path.join(home, *parts[3:])
            if os.path.exists(alt):
                return alt
        return p
    return remap, src_root


def file_state(p: str) -> str:
    try:
        st = os.stat(p)
    except OSError:
        return "missing"
    return "online-only" if getattr(st, "st_flags", 0) & SF_DATALESS else "found"


def rekordbox_paths(rb_dir: str = os.path.expanduser("~/Library/Pioneer/rekordbox")) -> set[str] | None:
    """Paths of the tracks already in the Rekordbox collection, read from a
    temp copy of master.db. None if it cannot be read."""
    import logging
    import shutil
    import tempfile
    logging.disable(logging.CRITICAL)
    tmp = tempfile.mkdtemp(prefix="rbassist-")
    try:
        from pyrekordbox import MasterDatabase
        for name in ("master.db", "master.db-wal", "master.db-shm"):
            if os.path.exists(os.path.join(rb_dir, name)):
                shutil.copy2(os.path.join(rb_dir, name), tmp)
        db = MasterDatabase(os.path.join(tmp, "master.db"))
        try:
            return {c.FolderPath for c in db.get_content().all() if c.FolderPath and not c.rb_local_deleted}
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def rekordbox_settings(path: str = os.path.expanduser(
        "~/Library/Application Support/Pioneer/rekordbox6/rekordbox3.settings")) -> dict:
    """The analysis settings that decide what Rekordbox fills in for imported
    tracks. Values are None when the settings file cannot be read."""
    import re
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return dict(auto_analysis=None, key_analysis=None)

    def flag(name):
        m = re.search(r'<VALUE name="%s" val="([^"]*)"' % name, text)
        return None if m is None else m.group(1) == "1"
    return dict(auto_analysis=flag("AutoAnalysis"), key_analysis=flag("enableKeyAnalysis"))


def scan(xml_path: str, root: str | None) -> dict:
    tracks, playlists = read_crate(xml_path)
    remap, src_root = remapper(tracks, root)
    rb = rekordbox_paths()
    in_rb = rb or set()
    info = {}
    for tid, t in tracks.items():
        src = loc_to_path(t.get("Location") or "")
        p = remap(src)
        info[tid] = dict(id=tid, title=t.get("Name") or "", artist=t.get("Artist") or "", path=p, state=file_state(p),
                         in_rekordbox=p in in_rb, comment=t.get("Comments") or "", bpm=float(t.get("AverageBpm") or 0))
    out = []
    for pl in playlists:
        rows = [info[i] for i in pl["ids"] if i in info]
        out.append(dict(name=pl["name"], folder=pl["folder"], count=len(rows),
                        found=sum(r["state"] == "found" for r in rows), missing=sum(r["state"] == "missing" for r in rows),
                        online_only=sum(r["state"] == "online-only" for r in rows),
                        in_rekordbox=sum(r["in_rekordbox"] for r in rows), tracks=rows))
    return dict(crate_root=src_root, root=root or "", remapped=bool(root) or any(r["path"] != loc_to_path(tracks[r["id"]].get("Location") or "") for r in info.values()),
                rekordbox_read=rb is not None, rekordbox_settings=rekordbox_settings(), tracks=len(tracks), playlists=out)


def chosen(xml_path: str, names: list[str]):
    tracks, playlists = read_crate(xml_path)
    picked = [p for p in playlists if p["name"] in names]
    unknown = sorted(set(names) - {p["name"] for p in picked})
    if unknown:
        raise SystemExit("not in crate.xml: " + ", ".join(unknown))
    return tracks, picked


def build(xml_path: str, root: str | None, names: list[str], out: str) -> dict:
    tracks, picked = chosen(xml_path, names)
    remap, _ = remapper(tracks, root)
    keep, left_out = {}, []
    for pl in picked:
        for i in pl["ids"]:
            t = tracks.get(i)
            if t is None or i in keep:
                continue
            p = remap(loc_to_path(t.get("Location") or ""))
            st = file_state(p)
            if st == "missing":
                left_out.append(dict(id=i, title=t.get("Name"), artist=t.get("Artist"), path=p, playlist=pl["name"]))
                continue
            keep[i] = (t, p)

    dj = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(dj, "PRODUCT", Name="RBAssist", Version="0.4", Company="")
    coll = ET.SubElement(dj, "COLLECTION", Entries=str(len(keep)))
    for i, (t, p) in keep.items():
        a = {k: t.get(k) for k in AUDIO_ATTRS if t.get(k)}
        ET.SubElement(coll, "TRACK", TrackID=i, Location=path_to_loc(p), **a)
    pls = ET.SubElement(dj, "PLAYLISTS")
    top = ET.SubElement(pls, "NODE", Type="0", Name="ROOT", Count="1")
    folder = ET.SubElement(top, "NODE", Type="0", Name="Crate", Count=str(len(picked)))
    for pl in picked:
        ids = [i for i in pl["ids"] if i in keep]
        node = ET.SubElement(folder, "NODE", Name=pl["name"], Type="1", KeyType="0", Entries=str(len(ids)))
        for i in ids:
            ET.SubElement(node, "TRACK", Key=i)
    ET.indent(dj)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    ET.ElementTree(dj).write(out + ".tmp", encoding="UTF-8", xml_declaration=True)
    os.replace(out + ".tmp", out)
    online = sum(file_state(p) == "online-only" for _, p in keep.values())
    return dict(out=out, playlists=[p["name"] for p in picked], tracks=len(keep), left_out=left_out, online_only=online)


def download(xml_path: str, root: str | None, names: list[str], progress: bool) -> dict:
    tracks, picked = chosen(xml_path, names)
    remap, _ = remapper(tracks, root)
    todo = []
    for pl in picked:
        for i in pl["ids"]:
            t = tracks.get(i)
            if t is not None:
                p = remap(loc_to_path(t.get("Location") or ""))
                if file_state(p) == "online-only" and p not in todo:
                    todo.append(p)
    failed = []
    for k, p in enumerate(todo):
        try:
            with open(p, "rb") as f:  # reading the whole file has the provider fetch it
                while f.read(1 << 20):
                    pass
        except OSError as e:
            failed.append(dict(path=p, error=str(e)))
        if progress:
            print(json.dumps(dict(done=k + 1, total=len(todo), title=os.path.basename(p))), flush=True)
    still = [p for p in todo if file_state(p) == "online-only"]
    return dict(requested=len(todo), still_online_only=len(still), failed=failed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["scan", "build", "download"])
    ap.add_argument("--crate-xml", required=True)
    ap.add_argument("--root")
    ap.add_argument("--playlist", action="append", default=[])
    ap.add_argument("--out")
    ap.add_argument("--progress", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.crate_xml):
        raise SystemExit(f"crate.xml not found at {a.crate_xml}")
    root = os.path.expanduser(a.root) if a.root and a.root.strip() else None
    if a.action == "scan":
        res = scan(a.crate_xml, root)
    elif a.action == "build":
        if not a.playlist or not a.out:
            raise SystemExit("build needs --playlist and --out")
        res = build(a.crate_xml, root, a.playlist, a.out)
    else:
        res = download(a.crate_xml, root, a.playlist, a.progress)
    print(json.dumps(res))


if __name__ == "__main__":
    main()

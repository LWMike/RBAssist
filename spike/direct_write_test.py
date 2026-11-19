"""Spike: write cues (master.db) and one grid change (ANLZ) directly.

usage: python direct_write_test.py <rekordbox-db-dir> <share-dir>
Refuses to run while Rekordbox is open. Back up first.
"""
import datetime, logging, os, random, subprocess, sys, uuid
logging.disable(logging.CRITICAL)
from pyrekordbox import MasterDatabase
from pyrekordbox.masterdb import models as m
from pyrekordbox.anlz import AnlzFile
import rbox

dbdir, share = sys.argv[1:3]
if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).returncode == 0:
    sys.exit("Rekordbox is running; quit it first.")

db = MasterDatabase(os.path.join(dbdir, "master.db"))
CUE_TRACK, GRID_TRACK = "ADSR", "Run"

def content(title):
    pl = next(p for p in db.get_playlist().all() if p.Name == "RBAssist compare (no grid)")
    return next(s.Content for s in pl.Songs if s.Content.Title == title)

def rb_grid(c):
    tag = AnlzFile.parse_file(share + c.AnalysisDataPath).get_tag("PQTZ")
    return list(tag.times), list(tag.beats), list(tag.bpms)

def add_cue(c, kind, in_s, out_s=None, comment="", color_idx=0):
    now = datetime.datetime.now()
    ids = {q.ID for q in db.get_cue().all()}
    cid = str(random.randint(10**8, 4 * 10**9))
    while cid in ids:
        cid = str(random.randint(10**8, 4 * 10**9))
    in_ms = int(round(in_s * 1000))
    out_ms = int(round(out_s * 1000)) if out_s is not None else -1
    db.add(m.DjmdCue(
        ID=cid, ContentID=c.ID, InMsec=in_ms, InFrame=in_ms * 150 // 1000, InMpegFrame=0, InMpegAbs=0,
        OutMsec=out_ms, OutFrame=(out_ms * 150 // 1000 if out_ms > 0 else 0), OutMpegFrame=0, OutMpegAbs=0,
        Kind=kind, Color=255, ColorTableIndex=color_idx, ActiveLoop=0, Comment=comment, BeatLoopSize=0, CueMicrosec=0,
        ContentUUID=c.UUID, UUID=str(uuid.uuid4()), rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
        rb_local_synced=0, created_at=now, updated_at=now))

# --- 1. cues on CUE_TRACK, placed on Rekordbox's own grid ---------------
c = content(CUE_TRACK)
if db.get_cue(ContentID=c.ID).count():
    sys.exit(f"{CUE_TRACK} already has cues; not writing twice.")
times, beats, bpms = rb_grid(c)
i = next(k for k in range(64, len(times)) if beats[k] == 1)   # a bar-1 beat about 27 s in
t_a, t_loop_end = times[i], times[i + 16]
add_cue(c, 1, t_a, comment="Direct A", color_idx=22)                       # hot cue A
add_cue(c, 2, t_a, t_loop_end, comment="Direct loop 4 bars", color_idx=36) # hot cue B, loop
add_cue(c, 0, t_a, comment="Direct memory @A")                             # memory cue sharing A's position
add_cue(c, 0, times[i + 64], comment="Direct memory +16 bars")             # memory cue on its own
print(f"{CUE_TRACK}: 4 cues queued at {t_a:.3f}s (beat {i}), loop to {t_loop_end:.3f}s, memory also at {times[i+64]:.3f}s")

# --- 2. grid change on GRID_TRACK: one constant tempo, 143.00 -----------
g = content(GRID_TRACK)
dat = share + g.AnalysisDataPath
a = rbox.Anlz(dat)
bg = a.get_beat_grid()
old = bg.beats
t0 = old[0].time
new = [rbox.anlz.Beat(beat_number=b.beat_number, tempo=14300, time=int(round(t0 + k * 60000 / 143))) for k, b in enumerate(old)]
bg.beats = new
a.set_beat_grid(bg)
a.dump_copy(dat + ".new")
os.replace(dat + ".new", dat)
print(f"{GRID_TRACK}: grid rewritten, {len(new)} beats, tempo {old[0].tempo/100:.2f} -> 143.00, first beat kept at {t0} ms, last beat moved {new[-1].time - old[-1].time:+d} ms")
print("   EXT (extended grid) left untouched on purpose: variant 1 tests the DAT grid alone")
g.BPM = 14300

# --- 3. variant 2 on SHIFT_TRACK: move the whole grid +100 ms in DAT and EXT ----
# The EXT grid carries a checksum rbox does not recompute, so this also shows
# whether Rekordbox minds a stale one.
h = content("Phasma")
dat = share + h.AnalysisDataPath
a = rbox.Anlz(dat); bg = a.get_beat_grid()
bg.beats = [rbox.anlz.Beat(beat_number=b.beat_number, tempo=b.tempo, time=b.time + 100) for b in bg.beats]
a.set_beat_grid(bg); a.dump_copy(dat + ".new"); os.replace(dat + ".new", dat)
ext = dat[:-3] + "EXT"
e = rbox.Anlz(ext); x = e.get_extended_beat_grid()
fb = x.first_beat; fb.time += 100; x.first_beat = fb
lb = x.last_beat; lb.time += 100; x.last_beat = lb
e.set_extended_beat_grid(x); e.dump_copy(ext + ".new"); os.replace(ext + ".new", ext)
print(f"Phasma: grid shifted +100 ms in DAT and EXT ({len(bg.beats)} beats), EXT checksum left as it was")

db.commit()
print("committed")

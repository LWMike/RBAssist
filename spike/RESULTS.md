# M0 spike results (2026-11-11)

Everything here ran on this Mac against Crate's 197 tracks and a scratch copy
of the Rekordbox 7.2.18 library. The real Rekordbox library was not touched.

## 1. Grid fitter — tempo solid, position and bar 1 not (see section 4)

Prototype: `spike/gridfit.py` (Python, to be ported to Rust). 3.5 s per track.

| | |
|---|---|
| Tracks analysed | 197 / 197 |
| High confidence (≥ 0.8) | 158 |
| Medium (0.5–0.8) | 9 |
| Low (< 0.5) | 30 |
| Snapped to a whole BPM | 180 |
| Tempo agrees with Crate (±0.6) | 178 |
| Kick timing spread, high-confidence tracks | median 0.15 ms |
| Tracks with a beatless intro over 2 s | 36 |

What the numbers do and don't show:
- Tempo and beat position are tight: kicks sit within a fraction of a
  millisecond of the fitted grid across whole tracks.
- **Which beat is bar 1 is the weak part.** 30 of the 158 high-confidence
  tracks had less than half the structural evidence on the chosen bar, and 13
  have a first kick that does not land on a bar line. The confidence score
  currently under-weights this.
- The low group is mostly not four-on-the-floor: drum & bass (fitted at
  130.5, three-quarters of 174), broken beat, and ambient-leaning tracks. They
  are flagged, not silently wrong. Crate's BPM is used as a hint and rescued 5.
- Six kick-driven techno tracks failed with `too-few-kicks` (e.g. Pres - Aros,
  Lars Huismann - Neural). Cause not investigated yet.
- No ground truth yet. Accuracy against your ears or Rekordbox's own grids is
  the next check.

Full table: `spike/out/report.txt`. Click previews for 30 tracks (start and
end of each, bar 1 is the higher click): `spike/out/previews/`.

## 2. Direct writes — work on a copy, Rekordbox's acceptance untested

| Test | Result |
|---|---|
| Open encrypted master.db copy (rbox 0.3.0) | Pass |
| Create playlist + 3 tracks, read back with pyrekordbox, integrity check | Pass |
| ANLZ `.DAT` and `.EXT` load and re-save byte-identical | Pass |
| Rewrite a beatgrid (745 beats at 138.00), reload | Pass |
| Insert a hot cue row into `DjmdCue` | Pass, via pyrekordbox only |

Findings:
- **rbox has no cue-write methods for master.db.** It can write cues into
  ANLZ files and playlists into the database, but `DjmdCue` rows need
  pyrekordbox or raw SQL. Direct mode therefore needs either both libraries
  or our own cue insert on top of rbox.
- ANLZ beat times are whole milliseconds, so grids are quantised to 1 ms.
- Not tested: opening Rekordbox on a library written this way.

## 3. XML import — grids and hot cues survive, one memory cue lost

Imported the 10-track playlist into Rekordbox 7.2.18 by hand, then read
Rekordbox's database and analysis files back.

| Check | Result |
|---|---|
| Grid start and BPM kept exactly as supplied | 10 / 10 |
| Rekordbox analysed the tracks without replacing the grid | 10 / 10 |
| Hot cue A on the first kick, with name | 10 / 10 |
| Hot cue B as a 4-bar loop, with name | 10 / 10 |
| "Last kick" memory cue | 10 / 10 |
| "First kick" memory cue (same position as hot cue A) | 0 / 10, dropped |

Findings:
- For tracks new to the collection, XML is a reliable way to deliver grids,
  hot cues and loops. The assumption the spec leaned on holds.
- The memory cue sharing a position with hot cue A did not arrive. Not yet
  known whether Rekordbox drops a memory cue that coincides with a hot cue, or
  whether element order matters. Needs one more test.
- Because Rekordbox adopted our grids, this says nothing about whether they
  are *right*. A second playlist, **RBAssist compare (no grid)**, 40 tracks
  with no grid supplied, is in the same XML so Rekordbox analyses them itself
  and the two grids can be compared.
- Still to check by eye in Rekordbox: do grid lines sit on the kicks, and do
  the two MP3s (Echoing Glade, Focal) line up as well as the FLACs.
- Not tested: re-importing tracks already in the collection.

## 4. Fitter vs Rekordbox's own analysis — tempo agrees, beat position does not

Rekordbox analysed 37 of the 40 no-grid tracks itself (3 could not be matched
by path). Its grids were read from the analysis files and compared, and 12
disagreements were plotted against the waveform.

**Tempo.** Same as Rekordbox on 26 of 27 high-confidence tracks. The
exception is "Run": Rekordbox 142.95, fitter 143.00 with no drift against the
kicks, so Rekordbox is probably wrong there.

**Beat position.** Four versions of the position step were tried. None is
reliable:

| Version | Idea | Within 10 ms of Rekordbox |
|---|---|---|
| 1 | Sharpest rise near the fitted kick | 2 / 10 (sat 20–60 ms late, on the kick body) |
| 2 | First rise out of the quiet before the kick | 14 / 26 |
| 3 | Start of the steepest low-end burst in the beat | 0 / 25 (caught off-beat bass) |
| 4 | Version 3 limited to just before the kick, then the click | 7 / 26 (16 / 26 within 25 ms) |

- The "0.15 ms spread" in section 1 measures how consistently kicks repeat,
  not whether the grid is on them. It hid version 1's error.
- From the plots, Rekordbox is on the start of the kick in roughly 9 of the
  12 disagreements. It is clearly wrong on one ("Beyond The Veil", about half
  a beat out) and unclear on two.
- So on beat position Rekordbox is currently the better source, and the
  fitter is not good enough to overwrite it.
- All three MP3s disagree by 23–48 ms even in the best version. Not
  investigated.
- Bar 1 matches Rekordbox on 16–20 of 26 depending on version.

**The 10 grids imported in section 3 came from version 1 and are 20–60 ms
late.** They have not been replaced, because no version is trustworthy enough
to replace them with. The test XML is back to what was imported.

## 5. Direct writes into the real test library — Rekordbox accepts them

With Rekordbox closed and the library backed up (`spike/backups/`), three
tracks in **RBAssist compare (no grid)** were changed directly. The database
passes its integrity check and the changes read back correctly.

| Track | Change | To check in Rekordbox |
|---|---|---|
| ADSR | Hot cue A, a 4-bar loop on B and a memory cue, all at 0:26.9; a second memory cue at 0:53.5 | Do all four show, named "Direct ..."? Does the memory cue at A's position survive? |
| Run | Grid set to 143.00 in the DAT file and the database; the EXT file's extended grid left at 142.95 | Does it show 143.00, and does the grid stay on the kicks to the end? |
| Phasma | Grid moved +100 ms in both DAT and EXT; the EXT checksum left stale | Is the grid visibly late (about a tenth of a second after each kick)? |

Run and Phasma together show which file Rekordbox reads the grid from and
whether it minds a stale checksum. rbox does not recompute that checksum, so
if Rekordbox needs it, grid writes need more work.

**Outcome (2026-11-12): all three accepted**, as checked by eye in Rekordbox
7.2.18 from the collection's Playlists section.

| Track | Seen in Rekordbox | What it shows |
|---|---|---|
| ADSR | Hot cue A, loop on B and the memory cues, with their names | Cue rows inserted by pyrekordbox are accepted. A memory cue can share a position with a hot cue when written directly (XML import dropped it). |
| Run | 143.00, grid on the kicks | A grid rewritten in the DAT file plus the database BPM is enough; the EXT extended grid was left at 142.95. |
| Phasma | Grid visibly late | Grid edits take effect, and a stale EXT checksum is tolerated. |

Limits: one check on three FLAC tracks, on one Rekordbox version. Not tested:
USB export or CDJ playback of directly written tracks, Cloud Library Sync,
and which of DAT and EXT the waveform view actually draws from (Phasma moved
both; Run moved only DAT).

An earlier report that tracks would not load was a false alarm: the playlist
was being opened from the *rekordbox xml* section of the sidebar, where a
double-click offers to import instead of loading. The library was restored
and the writes re-applied in between; backups of each state are in
`spike/backups/`. The three test changes are still in the library.

Side finding: double-clicking a track in the XML view that is already in the
collection brings up "Do you want to load information in the tag of the
library being imported?", with grid and key skipped for locked tracks. That
is a per-track route for re-importing existing tracks. What it overwrites is
untested.

## Effect on the spec

- XML delivery of grids, hot cues and loops works for new tracks.
- Direct mode needs a cue-insert path that rbox does not provide.
- Rekordbox accepts directly written cues and grids, so pass 2 can go direct.
- **Change of approach for grids.** Let Rekordbox analyse first, then have
  RBAssist audit its grid: exact tempo (whole-BPM snap, drift), half-beat
  errors, bar 1. Corrections go to a review queue instead of being applied.
- No hand-corrected grids exist or will, so position and bar 1 are left to
  Rekordbox; the audit keeps to checks verifiable from the audio.
- Bar-1 detection is not ready.
- MP3 timing needs separate investigation.
- Non-four-on-the-floor tracks stay with Rekordbox's analysis.

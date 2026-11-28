# RBAssist — spec (draft 0.5, 2026-11-19)

A Mac companion app that takes playlists from Crate into Rekordbox, audits the
beatgrid Rekordbox gives each track, and adds a load point, hot cues, loops
and memory cues suited to techno.

Status: draft. Changed from 0.1 after the M0 spike (`spike/RESULTS.md`):
RBAssist no longer generates beatgrids. Rekordbox analyses; RBAssist audits.
Items marked **[spike]** are unproven.

## 1. Decisions so far

| Question | Answer |
|---|---|
| Which Rekordbox library | The one on **another Mac**. This Mac's stale Rekordbox 7.2.18 library is the test bed. |
| Write mode | **Hybrid**: XML import and direct database writes, the latter behind backups, dry-run and undo. |
| Playback | **Both** CDJ/XDJ via USB and controller, so prep stays inside CDJ limits: 8 hot cues, 10 memory cues. |
| Start point | **First kick**: the track loads where the first proper kick lands. |
| Grids | **Audit, don't generate.** Rekordbox's grid is the starting point; RBAssist checks it and proposes corrections for review. |
| Hand-corrected grids | **None exist and none will be made.** The audit is limited to checks that can be verified from the audio alone. |

## 2. What is known

### Crate
- Keeps `exports/crate.xml`, a Rekordbox XML of every playlist, rewritten
  within 2 s of any change, with BPM, key and energy. No grids or cues.
- Paths are for the machine Crate runs on and need remapping on another Mac.

### Rekordbox 7
- No official API. Three surfaces: XML import (supported, manual clicks),
  `master.db` (encrypted SQLite, unofficial), ANLZ analysis files (grids and
  waveforms, unofficial).
- Its window cannot be driven from outside without macOS Accessibility
  permission, so XML import always needs the user's clicks.

### Proven in the spike
- **XML delivers to new tracks.** Grids, hot cues, loops and named memory
  cues arrived intact on 10 of 10 tracks. A memory cue at the same position as
  a hot cue was dropped.
- **Direct writes work on a copy.** Playlists and grids via `rbox`; cue rows
  via `pyrekordbox` only, since rbox has no cue-write method.
- **Rekordbox accepts direct writes.** On three tracks in the test library it
  showed directly written hot cues, a loop, named memory cues (including one
  at a hot cue's position) and rewritten grids. USB export, CDJ playback and
  Cloud Library Sync of such tracks are untested.
- **Tempo detection is solid.** Matches Rekordbox on 26 of 27 confident
  tracks, and catches at least one Rekordbox error (142.95 for a 143.00 track).
- **Beat position detection is not.** Four attempts, best 14 of 26 within
  10 ms of Rekordbox. Rekordbox was on the kick in roughly 9 of 12 plotted
  disagreements, and clearly wrong on one (half a beat out).
- **Bar 1** matched Rekordbox on 16–20 of 26. Not ready.
- **MP3s** disagreed by 23–48 ms on all three tested. Cause unknown.
- Drum & bass and broken beat are correctly flagged as outside the fitter's
  range.

## 3. Workflow

1. **Bring in.** Tick Crate playlists in RBAssist. It produces the playlist
   for Rekordbox with no grid, and Rekordbox analyses the tracks as usual.
2. **Audit.** RBAssist reads Rekordbox's grids and checks each one. Most pass
   silently.
3. **Review.** Flagged tracks show the waveform, Rekordbox's grid, the
   proposed correction and a click preview. Accept, adjust or dismiss.
4. **Cue.** RBAssist places the load point, hot cues, loops and memory cues on
   the audited grid.
5. **Write back.** Corrections and cues go into Rekordbox.

Steps 2–5 need Rekordbox to have analysed first, so prep is two passes. How
few clicks that takes depends on the write path (section 4.5).

## 4. Features

### 4.1 Crate intake
- Source: `crate.xml`, reaching the other Mac through a Dropbox-synced
  exports folder (recommended) or Crate's API over the tunnel.
- Path remap to the local library root, verifying every file exists.
- Dropbox online-only files are detected, and downloads asked for first.
- Playlist order, names, folders, energy and Camelot key carry through.

### 4.2 Grid audit

For each track Rekordbox has analysed:

| Check | How | Action |
|---|---|---|
| Tempo exact | Fit one constant tempo to every kick; compare with Rekordbox | Propose the whole-BPM value when the fit supports it |
| Tempo drift | Does Rekordbox's grid slide against the kicks across the track | Propose corrected tempo |
| Half-beat error | Is the low-end burst on the beat or between beats | Propose a half-beat shift |
| Double / half tempo | 84 vs 168 and similar | Propose the multiple matching Crate's BPM |
| Dynamic grid on steady track | Rekordbox used several tempos where one fits | Propose a single tempo |
| Not four-on-the-floor | Sparse or irregular kicks | Leave to Rekordbox, mark unaudited |

- Rekordbox's beat position and bar 1 are taken as they are. The spike could
  not beat Rekordbox on either, and with no hand-corrected grids there is
  nothing to prove an improvement against. Half-beat errors are the exception:
  they are large enough to verify from the audio.
- Every proposal goes to review, with a click preview, as accept or dismiss.
  A check may graduate to automatic once its proposals are being accepted
  consistently.
- MP3s are marked lower-trust until the timing difference is understood.

### 4.3 Load point (first kick)
- First downbeat of the first sustained kick section (default: kick present
  for 8 bars), snapped to the audited grid.
- Written as hot cue A and a memory cue at the same position. Rekordbox keeps
  both when written directly; XML import drops the memory cue.

### 4.4 Cue template (default "Techno", editable, per-playlist override)

| Slot | Point | Type |
|---|---|---|
| A | First kick, the load point | Hot cue |
| B | Intro loop: 4 bars from first kick | Hot cue loop |
| C | Main breakdown start | Hot cue |
| D | Drop: kick returns after the main breakdown | Hot cue |
| E | Second breakdown, if there is one | Hot cue |
| F | Second drop, if there is one | Hot cue |
| G | Mix-out: where the outro starts stripping back | Hot cue |
| H | Outro loop: 8 bars of drums-only | Hot cue loop |

- Memory cues (up to 10) on major phrase changes, with bar-countdown comments.
- Sections come from kick, sub, hat and mid energy entering and leaving, on
  the audited grid. All cue positions snap to that grid.
- Existing cues are never overwritten without an explicit choice.

### 4.5 Getting it into Rekordbox

Two writes are needed: the playlist before analysis, then corrections and
cues after.

| Write | XML route | Direct route |
|---|---|---|
| Playlist in (pass 1) | Proven. Reload and Import Playlist. | Proven on a copy. **[spike]** tracks added this way then need analysing. |
| Corrections + cues (pass 2) | **[spike]** tracks are already in the collection. Rekordbox offers a per-track "load information from the imported library" prompt; what it overwrites is untested. | Proven in Rekordbox on three tracks: cues via pyrekordbox, grids via rbox. **[spike]** USB export and CDJ playback. |

- Direct route guard rails: refuse while Rekordbox is running; timestamped
  backup before every write; dry-run diff; undo; version allow-list.
- Pass 2 goes direct by default: no clicks, and it keeps the memory cue at the
  load point. XML re-import stays as the fallback if it proves workable.

### 4.6 Beyond the brief

Recommended for v1:
- **Mix-in / mix-out lengths** in the comment (`In 32 · Out 64` bars).
- **USB preflight** per playlist.
- **Grid lock** on audited tracks.
- **Audit the existing collection**, now a natural fit: the same checks run
  over tracks already in Rekordbox.

Later:
- Transition-aware cues from Crate's running order.
- Audited grids and play history back to Crate.
- Train Rekordbox's CUE Analysis from prepped tracks.
- Watch folder for new arrivals.
- Relink helper for moved libraries.

## 5. Architecture (recommended)

- **Tauri 2** app, React + TypeScript UI, Rust core.
- `rbox` for master.db, ANLZ and XML; our own `DjmdCue` insert, since rbox
  lacks one; `symphonia` for decoding; the tempo fitter and audit checks as
  plain DSP, ported from `spike/gridfit.py`.
- Local SQLite cache of analysis keyed by content hash, plus the backup
  folder. Nothing is written to the music library.
- Fallback: a bundled pyrekordbox sidecar if the Rust cue insert proves
  fragile.
- rbox is GPL-3: fine for personal use, binding if distributed.
- Unsigned `.dmg` for personal use; signing only if shared.

## 6. Milestones

- **M0 Spike** — done for XML to new tracks, direct writes on a copy, and the
  fitter. Remaining:
  1. Forced XML re-import of tracks already in the collection (fallback only).
  2. USB export and CDJ playback of directly written cues and grids.
  3. Why MP3 timing differs.
- **M1 Audit**: read Rekordbox grids, run the checks, review queue with click
  preview. Read-only against Rekordbox, so safe to build first.
  - Engine done as a Python prototype, `engine/audit.py`. First run on the 50
    test tracks: 4 flagged, 8 unaudited, 38 pass, about a minute uncached.
    It finds the known half-beat error and Rekordbox's 142.95 on "Run".
  - Double/half is flagged only when Rekordbox's tempo is outside 95–190 and
    Crate has the multiple inside it. Crate itself is inconsistent on drum &
    bass (87 and 174), so it is not treated as the authority.
  - Half-beat needs the offset to exceed 38% of a beat; smaller offsets are
    within the fitter's own position error.
  - Review app built in `app/` (Tauri 2 + React): pick Rekordbox playlists,
    run the audit, review flagged tracks with a waveform of both grids, click
    previews, and accept or dismiss. Decisions are saved for M3 to write back.
    Run with `pnpm --dir app tauri dev`. Until the Rust port it needs `uv` and
    `ffmpeg` on the Mac.
  - The half-beat check cannot tell a grid on the off-beat from a track with
    off-beat bass. Its finding says so, and the waveform shows the kick band
    and the highs separately.
  - To do: the dynamic-grid check has had no real case yet; port the engine
    to Rust before packaging.
- **M2 Cues**: section detection and the cue template on audited grids.
  - Done 2026-11-19: checked by ear in the native app, cues land right.
  - Engine done, `engine/cues.py`; the review app has a Cues view (whole-track
    waveform with sections, A–H and memory cues, play from 4 bars before any
    cue, accept or dismiss). On the 50 test tracks: cues on 40, all eight hot
    cues on most; drum & bass, broken beat and tracks with a wrong-multiple
    tempo are skipped.
  - Kick detection compares each beat's low-end envelope with the track's own
    median groove shape. A plain onset test failed on rolling and off-beat
    bass.
  - Sections snap to 4-bar phrases counted from Rekordbox's bar 1. The load
    point snaps only onto a bar that has a kick.
  - Memory cues name each change and count bars to the next
    ("Drop · 28 to breakdown"), so open question 4 has a default.
  - Existing Rekordbox cues are shown and kept unless "Replace" is chosen.
  - C/D is the longest breakdown and E/F the next, so E can come before C in
    time (open question 3).
  - Cues follow the grid decision: on the correction if accepted, otherwise
    on Rekordbox's grid.
- **M3 Write back**: direct writes for pass 2, with backups and undo.
  - Built 2026-11-19: `engine/writeback.py` (check, write, undo, status) and
    the app's Write view. Tested on a scratch copy of the library: grid,
    replace, keep-existing, refusal and undo all read back right, integrity
    check passes.
  - First real write 2026-11-19 (backup `backups/20261119-200521`): Bird In A
    Cage grid 141.01 → 141.00 plus cues on it, ADSR cues replaced (the four
    "Direct" test cues removed), Run cues added. Rekordbox 7.2.18 opened the
    library normally; its track list shows Bird In A Cage at 141.00 and hot
    cue markers on exactly those three tracks, in the written order (ADSR's
    E/F before C/D, old 0:27 cue gone). Loaded on a deck, all three show
    every hot cue with its name, colour (green, orange, blue, red, yellow
    as intended) and loop marker on B and H, and every memory cue with its
    bar-count comment. Bird In A Cage reads 141.00 and its grid is on the
    kicks at the start and at 4:38. **Direct write-back is proven in
    Rekordbox 7.2.18, including removing cues.**
  - Rekordbox 7.2.20 is out; re-check this route before updating.
  - The app sends only which tracks to write; grid beats and cue positions
    come from the saved audit (`audit.json`, full beat lists in `grids.json`).
  - Refuses while Rekordbox is open. Skips a track whose Rekordbox grid is no
    longer the one audited, and cues placed on a grid correction unless that
    grid is written in the same run.
  - Each write backs up master.db (with -wal/-shm) and every ANLZ file it
    changes to `backups/<time>/` in the app data folder, with a manifest.
    Undo restores the last write; if Rekordbox has saved since, it asks first,
    because the whole library goes back. The state before the undo is kept
    too.
  - Grid: DAT beat grid plus database tempo, EXT left as it is (the spike's
    proven route). Cues: DjmdCue rows. Replace removes the track's existing
    cue rows outright (new; check in Rekordbox). Keep fills only free hot cue
    slots and does not double a memory cue at the same place.
  - Hot cue colours use Rekordbox's colour table: green 22, orange 36, blue 1,
    red 42, yellow 33.
  - Written tracks are remembered so they are not written twice; audit again
    after a write before changing the same tracks' grids.
- **M4 Intake + extras**: Crate intake and remap, preflight, mix lengths,
  grid lock.
  - Crate intake built 2026-11-19: `engine/intake.py` (scan, build,
    download) and the app's Crate view. Reads `crate.xml` (197 tracks, 23
    playlists here), remaps each path to this Mac, and marks every track as
    on this Mac, missing, or in Dropbox only (macOS dataless flag), and
    whether Rekordbox already has it.
  - Remap: "Crate's library folder on this Mac" replaces Crate's common
    library folder (here `…/DJ Library/TUNES TO PREP`). Left empty, a path is
    used as it is, or with the home folder swapped.
  - The import file holds the chosen playlists under a "Crate" folder in
    Crate's order, with no grid, tempo or key, so Rekordbox analyses them;
    Crate's "Energy · Camelot" comment carries over. Missing files are left
    out and listed. Download reads online-only files so Dropbox fetches them.
  - Pass 1 stays on the XML route (proven in the spike); the app lists the
    Rekordbox clicks. The native window's Crate view is untested (the engine
    is tested from the command line, the view in the browser).
  - Real import 2026-11-23, Rekordbox 7.2.18, "Rolling In The Dark" (4
    tracks, 3 already in the collection): only the new track was added
    (2048 → 2049, matched by path), the playlist kept Crate's order, and the
    new track was analysed (grid 141.00) with Crate's comment. Existing
    tracks kept their own comments. Import Playlist puts the playlist at the
    top level, not in a "Crate" folder. Rekordbox showed a FLAC notice first.
    The new track got no key: Rekordbox's key analysis is off here
    (`enableKeyAnalysis` 0 in `rekordbox3.settings`), so auto analysis on
    import finds tempo and grid only. The scan now reports that setting and
    auto analysis, and the Crate view warns before importing.
  - USB preflight built 2026-11-25: `engine/preflight.py` (check, usb) and
    the app's USB view, over the playlists ticked in the sidebar. Read-only,
    from a copy of master.db. Per track: file present (not Dropbox-only, not
    streaming), analysed with a PQTZ grid, a key, format (FLAC flagged
    unless "My players play FLAC" is ticked; >48 kHz and >24-bit flagged),
    cues within 8 hot and 10 memory, and prepped (any cue; a memory cue for
    the load point). The view adds the audit's view of the grid (flagged and
    not reviewed, or accepted and not written). Per stick: filesystem
    (FAT32 ok; exFAT and HFS+ newer players only; APFS and NTFS no), free
    space against the playlists' total size, and whether it has an export.
    Tested on three playlists (58 tracks, under 3 s).
  - Stick test 2026-11-25 on the user's gig stick (128 GB, HFS+ on MBR,
    CDJ-3000 firmware file on it). The stick check read it right. Fixed:
    Rekordbox's folder on Mac-formatted sticks is `.PIONEER`, not `PIONEER`.
    The check now also reads the stick's OneLibrary (`exportLibrary.db`,
    from a temp copy, via `pyrekordbox.onelibrary`): tracks, playlists, how
    many came from another Rekordbox library (`masterDbId` vs this library's
    `DBID`), and duplicates (same title, artist and length: 479 of 1,727).
  - Export proven (M0 item 2, Mac side): ADSR and Run, with RBAssist-written
    grids and cues, exported with Rekordbox 7.2.18 (track list › Export
    Tracks › the stick). On the stick the grid is identical (707 and 682
    beats, same times) and every cue matches the library: 8 hot (slots, loop
    ends, names) and 9 or 7 memory cues with names, in the `.EXT` PCO2 tags.
    The legacy `.DAT` PCOB tag holds hot cues A–C only, as Rekordbox always
    writes it for old players. OneLibrary's own cue table stays empty; cues
    live in the ANLZ files. Rekordbox also writes `export.pdb`.
  - Learned: a stick exported from another library gets a second copy of a
    track it already has ("Name-2.flac"), not an update. The stick already
    had copies from three libraries. The USB view now warns about this.
  - Still unproven: CDJ playback of the exported cues and grids.
  - Mix lengths built 2026-11-26. `cues.py` adds `lengths` {in, out} and
    `comment` ("In 24 · Out 32") to each cue set. In: bars from the first
    kick to the first 4-bar phrase whose mids and highs come within 1.5 dB
    of the 16 bars after the main drop (the body up to mix-out when there is
    no breakdown), capped at the first breakdown. Out: Mix out (G) to the
    last kick. The Cues view shows them. Write puts them in the comment
    (`DjmdContent.Commnt`) when cues are accepted, replacing an earlier
    "In N · Out M" and keeping the rest (Crate's "Energy 5 · 3A" stays);
    a Write option turns it off. Covered by the same backup and undo.
  - Tested on 31 four-on-the-floor tracks from two playlists: In 12–48,
    Out 12–48 bars. Out isn't a multiple of 4 when the last kick falls
    mid-phrase (17, 34). Bevel gives In 4: its mids and highs are flat from
    the first kick. First version measured "full" after the last drop,
    which is the outro on some tracks (Sirens In 4, Voice Of The Soul
    In 152); fixed. Write tested on a copy of master.db: comments read back
    right, and a second check finds nothing to write. Not yet checked by
    ear or written to the real library.
  - To do: grid lock.
- **M5 Packaging**: `.dmg`, auto-update.

## 7. Open questions

1. Rekordbox version and plan on the other Mac; is Cloud Library Sync on?
2. Transport: Dropbox-synced `crate.xml`, or Crate's API over the tunnel?
3. Does the A–H cue layout match how you play? Preferred loop lengths?
4. Memory-cue comments: bar countdowns, section names, or both?
5. Personal use only, or might you share it?

# RBAssist

A Mac companion app for Rekordbox 7, built for preparing techno tracks. It
brings playlists in from Crate, checks the beatgrid Rekordbox gives each
track, and adds a load point, hot cues, loops and memory cues before writing
them back into Rekordbox.

RBAssist doesn't make its own beatgrids. Rekordbox analyses each track as
usual, and RBAssist checks the result against the audio. It only proposes a
fix when the audio clearly backs one up, and every fix goes to you for review
before anything is written.

> **Status:** personal project, early. Every stage works end to end on a test
> library with Rekordbox 7.2.18. The engine is still a set of Python
> prototypes that the app runs. Packaging hasn't started. See
> [SPEC.md](SPEC.md) for the full design, decisions and milestone notes.

## Workflow

1. **Bring in.** Pick Crate playlists. RBAssist remaps the file paths to this
   Mac, flags files that are missing or still online-only in Dropbox, and
   writes a Rekordbox XML with no grid data so Rekordbox analyses the tracks
   itself. You import it in Rekordbox; the app lists the clicks.
2. **Audit.** RBAssist reads Rekordbox's grids and checks each one: exact
   tempo, tempo drift, half-beat offset, double/half tempo, and a dynamic grid
   on a track with a steady tempo. Most tracks pass silently.
3. **Review.** Flagged tracks show the waveform with both grids and a click
   preview. Accept or dismiss each proposal.
4. **Cue.** Section detection places cues on the audited grid using an
   editable "Techno" template:

   | Slot | Point |
   |---|---|
   | A | First kick (load point) |
   | B | 4-bar intro loop |
   | C / D | Main breakdown / drop |
   | E / F | Second breakdown / drop |
   | G | Mix-out |
   | H | 8-bar outro loop |

   Up to 10 memory cues mark phrase changes, each with a bar countdown
   (`Drop · 28 to breakdown`). Existing cues are kept unless you choose
   Replace. The layout stays within CDJ limits (8 hot cues, 10 memory cues).
5. **Write back.** Accepted grids and cues go straight into Rekordbox's
   database (`master.db` plus ANLZ files). Before writing, RBAssist backs up
   everything it's about to change, and the last write can be undone.

## Safety

Writing back edits Rekordbox's database directly. Pioneer doesn't support
this, so the write step has guard rails:

- It won't write while Rekordbox is running.
- Before every write it saves a timestamped backup of `master.db` (with
  `-wal`/`-shm`) and every ANLZ file it changes.
- Undo restores the last write. If Rekordbox has saved since, it asks first,
  because the whole library goes back.
- It skips any track whose grid has changed since the audit.
- It has only been tested against Rekordbox **7.2.18**. Check again before
  using it with a newer version.

The audit and cue steps only read the library: they work on a temp copy of
the database, so they're safe to run while Rekordbox is open.

## Repository layout

| Path | What's there |
|---|---|
| `app/` | Tauri 2 desktop app (React + TypeScript UI, Rust shell): Crate, Audit, Cues and Write views |
| `engine/` | Python engine the app calls: `intake.py`, `audit.py`, `cues.py`, `writeback.py` |
| `spike/` | M0 experiments and the tempo fitter (`gridfit.py`); findings in [spike/RESULTS.md](spike/RESULTS.md) |
| `SPEC.md` | Design, decisions, milestones and open questions |

## Running it

Requirements (macOS):

- [Rust](https://rustup.rs) and the [Tauri 2 prerequisites](https://v2.tauri.app/start/prerequisites/)
- Node with [pnpm](https://pnpm.io)
- [uv](https://docs.astral.sh/uv/) (it installs the engine's Python dependencies on first run)
- `ffmpeg`
- Rekordbox 7, with its library at `~/Library/Pioneer/rekordbox`

```bash
pnpm --dir app install
```

```bash
pnpm --dir app tauri dev
```

You can also run the engine scripts on their own. Each one has its usage in
its header, for example:

```bash
uv run engine/audit.py --playlist "My Playlist"
```

## Roadmap

- **Done:** M0 spike, M1 audit, M2 cues, M3 direct write-back, M4 Crate intake
- **Next:** USB preflight, mix-in/out lengths in comments, grid lock, port
  the engine to Rust
- **Later:** packaging as a `.dmg`; USB export and CDJ playback of directly
  written cues still need checking

## Credits

Reading and writing Rekordbox's files relies on
[pyrekordbox](https://github.com/dylanljones/pyrekordbox) and
[rbox](https://github.com/dylanljones/rbox). rbox is GPL-3 licensed. Neither
project is affiliated with AlphaTheta / Pioneer DJ, and nor is this one.

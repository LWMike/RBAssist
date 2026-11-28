import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export type Finding = { check: string; severity: "major" | "minor"; text: string };
export type Proposal = { bpm: number; first_beat_ms: number; first_beat_number: number; n_beats: number };
export type Beat = [number, number]; // seconds, beat-in-bar
export type Previews = {
  rekordbox: string; proposed: string;
  rekordbox_beats: Beat[]; proposed_beats: Beat[];
  start_s: number; length_s: number; wave: number[];
  zoom_start_s: number; zoom_length_s: number; zoom_wave: number[]; wave_hi?: number[]; zoom_wave_hi?: number[];
};
export type Track = {
  content_id: string; playlist: string; title: string; artist: string; path: string;
  status: "flagged" | "note" | "pass" | "unaudited" | "error";
  reason?: string; findings: Finding[]; proposal?: Proposal | null; previews?: Previews;
  rb_bpm?: number; rb_tempos?: number; rb_first_beat_ms?: number; fit_bpm?: number; crate_bpm?: number | null;
  confidence?: number; drift_ms?: number; kick_span_s?: number;
  cues?: { rekordbox: CueSet; proposed?: CueSet }; overview?: Overview; existing_cues?: ExistingCue[];
};
export type HotCue = { slot: string; bar: number; time: number; name: string; color: string; end_bar?: number; end?: number };
export type MemoryCue = { bar: number; time: number; name: string };
export type Section = { kind: "intro" | "body" | "breakdown" | "outro"; start: number; end: number; t0: number; t1: number };
export type CueSet =
  | { ok: true; confidence: "normal" | "low"; hot: HotCue[]; memory: MemoryCue[]; sections: Section[]; notes: string[]; bars: number;
      lengths?: { in: number; out: number }; comment?: string }
  | { ok: false; reason: string };
export type ExistingCue = { slot: string | null; time: number; end: number | null; name: string };
export type Overview = { low: number[]; high: number[]; duration: number };
export type CueDecision = { decision: "accepted" | "dismissed"; grid: "rekordbox" | "proposed"; replace: boolean; sig: string; at: string };
export type Playlist = { id: string; name: string; folder: string; count: number; missing?: number };
export type Decision = { decision: "accepted" | "dismissed"; bpm: number; first_beat_ms: number; at: string };
export type Written = { grid?: number; cues?: string; lengths?: string; at: string };
export type State = {
  decisions?: Record<string, Decision>; cueDecisions?: Record<string, CueDecision>; crateXml?: string; selected?: string[];
  written?: Record<string, Written>;
  crateRoot?: string; cratePicked?: string[];
  flacOk?: boolean; usbMount?: string; writeLengths?: boolean;
};
export type CrateTrack = { id: string; title: string; artist: string; path: string; state: "found" | "missing" | "online-only"; in_rekordbox: boolean; comment: string; bpm: number };
export type CratePlaylist = { name: string; folder: string; count: number; found: number; missing: number; online_only: number; in_rekordbox: number; tracks: CrateTrack[] };
export type CrateScan = { crate_root: string; root: string; remapped: boolean; rekordbox_read: boolean;
  rekordbox_settings?: { auto_analysis: boolean | null; key_analysis: boolean | null }; tracks: number; playlists: CratePlaylist[] };
export type CrateBuild = { out: string; playlists: string[]; tracks: number; left_out: { title: string; artist: string; path: string; playlist: string }[]; online_only: number };
export type PlanEntry = { content_id: string; grid: boolean; cues: { grid: "rekordbox" | "proposed"; replace: boolean } | null;
  lengths?: "rekordbox" | "proposed" | null };
export type CheckedItem = {
  content_id: string; title: string; artist: string; ok: boolean; skip: string | null;
  grid: { old_bpm: number; bpm: number; beats: number; old_beats: number; shift_ms?: number } | null;
  cues: { replace: boolean; remove: number; hot: HotCue[]; memory: MemoryCue[]; kept_slots: string[]; on: string; nothing?: boolean } | null;
  comment?: { old: string; new: string; text: string } | null;
};
export type LastWrite = {
  dir: string; created: string; changed_since: boolean;
  tracks: { content_id: string; title: string; grid: boolean; hot: number; memory: number; removed: number }[];
};
export type WriteStatus = { rekordbox_running: boolean; last: LastWrite | null };
export type PreflightIssue = { level: "error" | "warn" | "info"; code: string; text: string };
export type PreflightTrack = {
  content_id: string; title: string; artist: string; path: string; size: number; bpm: number; key: string;
  hot: number; memory: number; beats: number | null; issues: PreflightIssue[]; duplicate: boolean;
};
export type PreflightPlaylist = { id: string; name: string; tracks: PreflightTrack[]; errors: number; warnings: number };
export type Preflight = { playlists: PreflightPlaylist[]; tracks: number; size: number; flac_ok: boolean };
export type UsbVolume = { name: string; mount: string; filesystem: string; verdict: "ok" | "warn" | "error"; note: string; free: number; total: number; exported: boolean;
  library?: { tracks: number; playlists: number; from_this_mac: number | null; from_elsewhere: number | null; duplicates: number } | null };
type Refusal = { refused: string };
export type Progress = { done: number; total: number; title: string };

// Outside the Tauri window (plain browser, for UI work) the app runs on sample data in /dev.
const inTauri = "__TAURI_INTERNALS__" in window;
const dev = async (f: string) => (await fetch(`/dev/${f}`)).json();

export const api = {
  inTauri,
  playlists: (): Promise<Playlist[]> => (inTauri ? invoke("list_playlists") : dev("playlists.json")),
  lastAudit: (): Promise<Track[] | null> => (inTauri ? invoke("last_audit") : dev("audit.json")),
  runAudit: (playlistIds: string[], crateXml: string): Promise<Track[]> =>
    inTauri ? invoke("run_audit", { playlistIds, crateXml }) : dev("audit.json"),
  onProgress: (cb: (p: Progress) => void): Promise<() => void> =>
    inTauri ? listen<Progress>("audit-progress", (e) => cb(e.payload)) : Promise.resolve(() => {}),
  preview: async (path: string): Promise<Blob> => {
    if (!inTauri) return (await fetch(`/dev/previews/${path.split("/").pop()}`)).blob();
    const buf = await invoke<ArrayBuffer>("read_preview", { path });
    return new Blob([buf], { type: "audio/mp4" });
  },
  audio: async (t: Track): Promise<Blob> => {
    const type = t.path.toLowerCase().endsWith(".mp3") ? "audio/mpeg" : t.path.toLowerCase().endsWith(".flac") ? "audio/flac" : "";
    if (!inTauri) return (await fetch(`/dev/audio/${t.content_id}.m4a`)).blob();
    const buf = await invoke<ArrayBuffer>("read_audio", { path: t.path });
    return new Blob([buf], type ? { type } : {});
  },
  writeCheck: (plan: PlanEntry[]): Promise<{ rekordbox_running: boolean; items: CheckedItem[] } | Refusal> =>
    inTauri ? invoke("write_check", { plan }) : devCheck(plan),
  writeApply: (plan: PlanEntry[]): Promise<{ written: number; items: CheckedItem[]; backup: string | null } | Refusal> =>
    inTauri ? invoke("write_apply", { plan }) : devCheck(plan).then((r) => ({ written: r.items.filter((i) => i.ok).length, items: r.items, backup: "/dev/backups/sample" })),
  writeStatus: (): Promise<WriteStatus> => (inTauri ? invoke("write_status") : Promise.resolve({ rekordbox_running: false, last: null })),
  writeUndo: (force: boolean): Promise<{ undone: string } | Refusal> =>
    inTauri ? invoke("write_undo", { force }) : Promise.resolve({ undone: "/dev/backups/sample" }),
  crateScan: (crateXml: string, root: string): Promise<CrateScan> =>
    inTauri ? invoke("crate_scan", { crateXml, root }) : dev("crate-scan.json"),
  crateBuild: (crateXml: string, root: string, playlists: string[]): Promise<CrateBuild> =>
    inTauri ? invoke("crate_build", { crateXml, root, playlists })
      : Promise.resolve({ out: "/dev/crate-import.xml", playlists, tracks: 9, left_out: [{ title: "Sample missing track", artist: "Someone", path: "/missing.flac", playlist: playlists[0] }], online_only: 0 }),
  crateDownload: (crateXml: string, root: string, playlists: string[]): Promise<{ requested: number; still_online_only: number; failed: { path: string; error: string }[] }> =>
    inTauri ? invoke("crate_download", { crateXml, root, playlists }) : Promise.resolve({ requested: 1, still_online_only: 0, failed: [] }),
  onDownload: (cb: (p: Progress) => void): Promise<() => void> =>
    inTauri ? listen<Progress>("download-progress", (e) => cb(e.payload)) : Promise.resolve(() => {}),
  preflight: (playlists: string[], flacOk: boolean): Promise<Preflight> =>
    inTauri ? invoke("preflight_check", { playlists, flacOk }) : dev("preflight.json"),
  usb: (): Promise<{ volumes: UsbVolume[] }> => (inTauri ? invoke("preflight_usb") : dev("preflight-usb.json")),
  loadState: async (): Promise<State> =>
    inTauri ? invoke("load_state") : JSON.parse(localStorage.getItem("rbassist-state") ?? "{}"),
  saveState: async (state: State): Promise<void> => {
    if (inTauri) await invoke("save_state", { state });
    else localStorage.setItem("rbassist-state", JSON.stringify(state));
  },
};

/** Browser-only stand-in for the engine's check, from the sample audit. */
async function devCheck(plan: PlanEntry[]): Promise<{ rekordbox_running: boolean; items: CheckedItem[] }> {
  const rows: Track[] = await dev("audit.json");
  const items = plan.map((p): CheckedItem => {
    const t = rows.find((r) => r.content_id === p.content_id);
    const set = p.cues ? t?.cues?.[p.cues.grid] : null;
    const taken = new Set(p.cues?.replace ? [] : (t?.existing_cues ?? []).map((c) => c.slot).filter(Boolean));
    return {
      content_id: p.content_id, title: t?.title ?? "", artist: t?.artist ?? "", ok: !!t, skip: t ? null : "not in the latest audit",
      grid: p.grid && t?.proposal ? {
        old_bpm: t.rb_bpm ?? 0, bpm: t.proposal.bpm, beats: t.proposal.n_beats, old_beats: t.proposal.n_beats,
        shift_ms: t.rb_first_beat_ms != null ? (() => { const T = 60000 / t.proposal!.bpm, d = t.proposal!.first_beat_ms - t.rb_first_beat_ms!; return Math.round(((((d / T + 0.5) % 1) + 1) % 1 - 0.5) * T); })() : undefined,
      } : null,
      cues: set && set.ok && p.cues ? {
        replace: p.cues.replace, remove: p.cues.replace ? (t?.existing_cues ?? []).length : 0, on: p.cues.grid,
        hot: set.hot.filter((h) => !taken.has(h.slot)), memory: set.memory, kept_slots: set.hot.filter((h) => taken.has(h.slot)).map((h) => h.slot),
      } : null,
      comment: (() => {
        const ls = p.lengths ? t?.cues?.[p.lengths] : null;
        return ls && ls.ok && ls.comment ? { old: "Energy 5 · 3A", new: "Energy 5 · 3A · " + ls.comment, text: ls.comment } : null;
      })(),
    };
  });
  return { rekordbox_running: false, items };
}

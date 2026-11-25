import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Decision, type Playlist, type Preflight, type PreflightIssue, type PreflightTrack, type State, type Track, type UsbVolume } from "./api";

type Props = {
  chosen: string[]; playlists: Playlist[] | null; tracks: Track[] | null; state: State; update: (p: Partial<State>) => void;
  gridDecision: (t: Track) => Decision | undefined; active: boolean;
};

const gb = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
const LEVEL_ORDER = { error: 0, warn: 1, info: 2 };

/** Step 5: will the ticked playlists play right from a USB stick on CDJs? Read-only. */
export function UsbMode({ chosen, playlists, tracks, state, update, gridDecision, active }: Props) {
  const flacOk = !!state.flacOk;
  const [result, setResult] = useState<Preflight | null>(null);
  const [vols, setVols] = useState<UsbVolume[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onlyProblems, setOnlyProblems] = useState(true);
  const key = chosen.join(",");

  const run = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const [p, u] = await Promise.all([chosen.length ? api.preflight(chosen, flacOk) : Promise.resolve(null), api.usb()]);
      setResult(p); setVols(u.volumes);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, flacOk]);

  useEffect(() => { if (active) run(); }, [active, run]);

  /** The engine's issues plus what the audit knows about the grid. */
  const issuesFor = useCallback((t: PreflightTrack): PreflightIssue[] => {
    const out = [...t.issues];
    const row = tracks?.find((r) => r.content_id === t.content_id);
    if (!row) out.push({ level: "info", code: "unaudited", text: "Grid not audited." });
    else if (row.status === "flagged") {
      const d = gridDecision(row);
      if (!d) out.push({ level: "warn", code: "grid-review", text: "Grid flagged in the audit and not reviewed." });
      else if (d.decision === "accepted" && state.written?.[row.content_id]?.grid === undefined)
        out.push({ level: "warn", code: "grid-unwritten", text: "Grid fix accepted but not written yet." });
    }
    if (t.duplicate) out.push({ level: "info", code: "dup", text: "Also earlier in this playlist." });
    return out.sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level]);
  }, [tracks, gridDecision, state.written]);

  const all = useMemo(() => {
    const m = new Map<string, PreflightIssue[]>();
    for (const p of result?.playlists ?? []) for (const t of p.tracks) if (!m.has(t.content_id)) m.set(t.content_id, issuesFor(t));
    return m;
  }, [result, issuesFor]);
  const count = (lvl: "error" | "warn") => [...all.values()].filter((is) => is.some((i) => i.level === lvl) && (lvl === "error" || !is.some((i) => i.level === "error"))).length;
  const errors = count("error"), warnings = count("warn");
  const clean = all.size - errors - warnings;

  const vol = vols?.find((v) => v.mount === state.usbMount) ?? (vols?.length === 1 ? vols[0] : undefined);
  const names = new Map((playlists ?? []).map((p) => [p.id, p.name]));

  return (
    <main className="detail write usb">
      <div className="pad">
        <h2>Ready for USB?</h2>
        <p className="muted">
          Checks the playlists ticked on the left against what CDJs need: the file, a beat grid, a key, a format the players read,
          and cues within 8 hot and 10 memory. Only reads Rekordbox's library.
        </p>

        <div className="usbopts">
          <label className="check">
            <input type="checkbox" checked={flacOk} onChange={(e) => update({ flacOk: e.target.checked })} />
            My players play FLAC <span className="muted">(CDJ-2000NXS2, CDJ-3000 or a newer XDJ)</span>
          </label>
          <button onClick={run} disabled={busy}>{busy ? "Checking…" : "Check again"}</button>
        </div>

        <section className="sticks">
          <h3>USB stick</h3>
          {vols === null && <p className="muted small">Looking for USB sticks…</p>}
          {vols?.length === 0 && <p className="muted small">No USB stick plugged in. Plug one in and check again to see its format and space.</p>}
          {vols?.map((v) => (
            <label key={v.mount} className={"stick " + v.verdict}>
              {vols.length > 1 && <input type="radio" name="usb" checked={vol?.mount === v.mount} onChange={() => update({ usbMount: v.mount })} />}
              <span className="dot" />
              <span>
                <b>{v.name}</b> · {v.filesystem} · {gb(v.free)} free of {gb(v.total)}
                {v.library ? <span className="muted"> · {v.library.tracks} tracks in {v.library.playlists} playlists</span>
                  : v.exported && <span className="muted"> · has a Rekordbox export</span>}
                <div className={"small " + (v.verdict === "ok" ? "muted" : v.verdict === "error" ? "bad" : "warn")}>{v.note}</div>
                {!!v.library?.from_elsewhere && (
                  <div className="small warn">
                    {v.library.from_elsewhere === v.library.tracks ? "All" : v.library.from_elsewhere} of its tracks came from another Rekordbox
                    library. Exporting a track it already has from this Mac adds a second copy instead of updating it, so export
                    from the Mac that made this stick.
                  </div>
                )}
                {!!v.library?.duplicates && (
                  <div className="small muted">
                    {v.library.duplicates} track{v.library.duplicates === 1 ? " is" : "s are"} on it more than once (same title, artist and length).
                  </div>
                )}
              </span>
            </label>
          ))}
        </section>

        {chosen.length === 0 && <p className="muted">Tick playlists on the left to check them.</p>}
        {error && <p className="error" role="alert">{error}</p>}

        {result && (
          <>
            <section className="summary">
              <span><b>{all.size}</b> tracks · {gb(result.size)}</span>
              {errors > 0 && <span className="bad"><b>{errors}</b> won't play right</span>}
              {warnings > 0 && <span className="warn"><b>{warnings}</b> with warnings</span>}
              <span className="ok"><b>{clean}</b> ready</span>
              {vol && result.size > vol.free && (
                <span className="bad">Up to {gb(result.size)} to copy, {gb(vol.free)} free on {vol.name}</span>
              )}
              <label className="check right">
                <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} /> Only tracks with problems
              </label>
            </section>

            {result.playlists.map((p) => {
              const rows = p.tracks.map((t, i) => ({ t, i, is: all.get(t.content_id) ?? [] }))
                .filter((r) => !onlyProblems || r.is.some((x) => x.level !== "info"));
              return (
                <section key={p.id}>
                  <h3 className="sub">
                    {names.get(p.id) ?? p.name} <span className="muted small">· {p.tracks.length} tracks{rows.length === 0 && p.tracks.length ? " · all ready" : ""}</span>
                  </h3>
                  {rows.length > 0 && (
                    <table className="plan">
                      <thead><tr><th>#</th><th>Track</th><th>Key · BPM</th><th>Cues</th><th>Checks</th></tr></thead>
                      <tbody>
                        {rows.map(({ t, i, is }) => (
                          <tr key={t.content_id + i} className={is.some((x) => x.level === "error") ? "skip" : ""}>
                            <td className="num muted">{i + 1}</td>
                            <td><div className="t">{t.title || "(untitled)"}</div><div className="a">{t.artist}</div></td>
                            <td className="num">{t.key || "—"} · {t.bpm ? t.bpm.toFixed(2) : "—"}</td>
                            <td className="num">{t.hot} hot · {t.memory} mem</td>
                            <td className="checks" title={t.path}>
                              {is.length === 0 ? <span className="ok small">Ready</span> : is.map((x) => (
                                <div key={x.code} className={"small lvl-" + x.level}>{x.text}</div>
                              ))}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </section>
              );
            })}

            <p className="muted small note">
              This checks the library, not the stick: export from Rekordbox as usual (drag the playlists onto the USB device), then
              load a prepped track on a player to confirm the grid and cues came across.
            </p>
          </>
        )}
      </div>
    </main>
  );
}

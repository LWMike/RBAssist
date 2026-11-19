import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Decision, type Playlist, type Progress, type State, type Track } from "./api";
import { Wave } from "./Wave";
import { CueMode } from "./CueMode";
import { WriteMode } from "./WriteMode";
import "./styles.css";

const CHECK: Record<string, string> = {
  "tempo-drift": "Tempo drift",
  "tempo-exact": "Tempo tidy-up",
  "half-beat": "Half a beat out",
  "tempo-multiple": "Double / half tempo",
  "dynamic-grid": "Dynamic grid",
};
type Tab = "review" | "decided" | "unaudited" | "passed";
const TABS: { id: Tab; label: string }[] = [
  { id: "review", label: "To review" },
  { id: "decided", label: "Decided" },
  { id: "unaudited", label: "Not audited" },
  { id: "passed", label: "Passed" },
];

/** A decision counts only while the proposal it was made on is still the proposal. */
function decisionFor(t: Track, d?: Record<string, Decision>): Decision | undefined {
  const x = d?.[t.content_id];
  return x && t.proposal && x.bpm === t.proposal.bpm && x.first_beat_ms === t.proposal.first_beat_ms ? x : undefined;
}
function tabOf(t: Track, d?: Record<string, Decision>): Tab {
  if (t.proposal) return decisionFor(t, d) ? "decided" : "review";
  return t.status === "pass" ? "passed" : "unaudited";
}
const fmtTime = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

function usePlayer() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const urls = useRef(new Map<string, string>());
  const [playing, setPlaying] = useState<string | null>(null);
  const [pos, setPos] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const a = new Audio();
    audio.current = a;
    let raf = 0;
    const tick = () => { setPos(a.currentTime); raf = requestAnimationFrame(tick); };
    a.onplay = () => { raf = requestAnimationFrame(tick); };
    a.onpause = a.onended = () => { cancelAnimationFrame(raf); setPlaying(null); };
    return () => { cancelAnimationFrame(raf); a.pause(); };
  }, []);

  const stop = useCallback(() => audio.current?.pause(), []);
  const play = useCallback(async (path: string, at = 0) => {
    const a = audio.current!;
    try {
      setError(null);
      let url = urls.current.get(path);
      if (!url) {
        url = URL.createObjectURL(await api.preview(path));
        urls.current.set(path, url);
      }
      if (a.src !== url) a.src = url;
      a.currentTime = Math.max(at, 0);
      await a.play();
      setPlaying(path);
    } catch (e) {
      setError(`Could not play the clip: ${e}`);
    }
  }, []);
  return { playing, pos, play, stop, error };
}

export default function App() {
  const [playlists, setPlaylists] = useState<Playlist[] | null>(null);
  const [tracks, setTracks] = useState<Track[] | null>(null);
  const [state, setState] = useState<State>({});
  const [loaded, setLoaded] = useState(false);
  const [mode, setMode] = useState<"grid" | "cues" | "write">("grid");
  const [tab, setTab] = useState<Tab>("review");
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const player = usePlayer();

  useEffect(() => {
    api.loadState().then((s) => { setState(s); setLoaded(true); });
    api.lastAudit().then(setTracks).catch(() => setTracks(null));
    api.playlists().then(setPlaylists).catch((e) => { setPlaylists([]); setError(String(e)); });
  }, []);

  const update = useCallback((patch: Partial<State>) => {
    setState((s) => {
      const next = { ...s, ...patch };
      api.saveState(next).catch((e) => setError(`Could not save: ${e}`));
      return next;
    });
  }, []);

  const chosen = useMemo(() => new Set(state.selected ?? []), [state.selected]);
  const toggle = (id: string) => {
    const n = new Set(chosen);
    n.has(id) ? n.delete(id) : n.add(id);
    update({ selected: [...n] });
  };

  const run = async () => {
    setBusy(true); setError(null); setProgress(null); player.stop();
    const off = await api.onProgress(setProgress);
    try {
      const res = await api.runAudit([...chosen], state.crateXml ?? "");
      setTracks(res);
      // land where there is something to see
      setTab(res.some((t) => t.proposal) ? "review" : res.some((t) => t.status !== "pass") ? "unaudited" : "passed");
      setSel(null);
    } catch (e) {
      setError(String(e));
    } finally {
      off(); setBusy(false);
    }
  };

  const groups = useMemo(() => {
    const g: Record<Tab, Track[]> = { review: [], decided: [], unaudited: [], passed: [] };
    for (const t of tracks ?? []) g[tabOf(t, state.decisions)].push(t);
    g.review.sort((a, b) => Number(b.status === "flagged") - Number(a.status === "flagged"));
    return g;
  }, [tracks, state.decisions]);
  const list = groups[tab];
  const current = list.find((t) => t.content_id === sel) ?? list[0] ?? null;

  const decide = useCallback((t: Track, decision: Decision["decision"] | null) => {
    if (!t.proposal) return;
    const d = { ...(state.decisions ?? {}) };
    if (decision) d[t.content_id] = { decision, bpm: t.proposal.bpm, first_beat_ms: t.proposal.first_beat_ms, at: new Date().toISOString() };
    else delete d[t.content_id];
    player.stop();
    if (decision && tab === "review") {
      const i = list.findIndex((x) => x.content_id === t.content_id);
      setSel((list[i + 1] ?? list[i - 1])?.content_id ?? null);
    }
    update({ decisions: d });
  }, [state.decisions, list, tab, update, player]);

  const playClip = useCallback((which: "rekordbox" | "proposed", at?: number) => {
    const p = current?.previews;
    if (!p) return;
    if (player.playing === p[which] && at == null) player.stop();
    else player.play(p[which], at ?? 0);
  }, [current, player]);

  useEffect(() => {
    if (mode !== "grid") return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
      const i = current ? list.indexOf(current) : -1;
      if (e.key === "ArrowDown" && list[i + 1]) { setSel(list[i + 1].content_id); player.stop(); }
      else if (e.key === "ArrowUp" && list[i - 1]) { setSel(list[i - 1].content_id); player.stop(); }
      else if (e.key === "1") playClip("rekordbox");
      else if (e.key === "2") playClip("proposed");
      else if (e.key === " ") player.stop();
      else if (current?.proposal && e.key.toLowerCase() === "a") decide(current, "accepted");
      else if (current?.proposal && e.key.toLowerCase() === "d") decide(current, "dismissed");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, list, current, decide, playClip, player]);
  const gridDecision = useCallback((t: Track) => decisionFor(t, state.decisions), [state.decisions]);

  const skipped = useMemo(() => {
    const m = new Map<string, number>();
    for (const t of tracks ?? []) {
      if (t.reason && !t.reason.startsWith("not four-on-the-floor")) m.set(t.reason, (m.get(t.reason) ?? 0) + 1);
    }
    return [...m.entries()];
  }, [tracks]);
  const accepted = groups.decided.filter((t) => decisionFor(t, state.decisions)?.decision === "accepted").length;

  return (
    <div className="app">
      <header>
        <h1>RBAssist</h1>
        <div className="modes" role="tablist">
          <button role="tab" aria-selected={mode === "grid"} className={mode === "grid" ? "on" : ""} onClick={() => { setMode("grid"); }}>
            Grids {groups.review.length > 0 && <b>{groups.review.length}</b>}
          </button>
          <button role="tab" aria-selected={mode === "cues"} className={mode === "cues" ? "on" : ""} onClick={() => { setMode("cues"); player.stop(); }}>
            Cues
          </button>
          <button role="tab" aria-selected={mode === "write"} className={mode === "write" ? "on" : ""} onClick={() => { setMode("write"); player.stop(); }}>
            Write
          </button>
        </div>
        <span className="spacer" />
        <span className="ro" title="Audits and reviews only read from Rekordbox. Changes are written from the Write view, with a backup first.">
          {mode === "write" ? "Writes need Rekordbox closed" : "Nothing is written until you use Write"}
        </span>
      </header>

      <aside className="side">
        <h2>Rekordbox playlists</h2>
        <div className="plist">
          {playlists === null && <p className="muted">Reading the library…</p>}
          {playlists?.length === 0 && <p className="muted">No playlists found.</p>}
          {playlists?.map((p) => (
            <label key={p.id} className={chosen.has(p.id) ? "on" : ""}>
              <input type="checkbox" checked={chosen.has(p.id)} onChange={() => toggle(p.id)} disabled={busy} />
              <span className="name">
                {p.folder && <span className="folder">{p.folder} / </span>}
                {p.name}
              </span>
              {p.missing ? (
                <span className="count warn" title={`${p.missing} of ${p.count} audio files are not on this Mac and cannot be audited`}>
                  {p.missing === p.count ? "files missing" : `${p.missing} of ${p.count} missing`}
                </span>
              ) : (
                <span className="count">{p.count}</span>
              )}
            </label>
          ))}
        </div>
        <label className="field">
          <span>Crate XML <em>(optional, for double/half tempo)</em></span>
          <input
            type="text" spellCheck={false} placeholder="/path/to/crate/exports/crate.xml"
            defaultValue={state.crateXml ?? ""} key={loaded ? "l" : "u"}
            onBlur={(e) => e.target.value !== (state.crateXml ?? "") && update({ crateXml: e.target.value })}
          />
        </label>
        <button className="primary" onClick={run} disabled={busy || chosen.size === 0}>
          {busy ? "Auditing…" : chosen.size ? `Audit ${chosen.size} playlist${chosen.size > 1 ? "s" : ""}` : "Choose playlists to audit"}
        </button>
        {busy && (
          <div className="progress" role="status">
            <div className="bar"><div style={{ width: `${progress ? (progress.done / progress.total) * 100 : 2}%` }} /></div>
            <span>{progress ? `${progress.done} of ${progress.total} · ${progress.title}` : "Starting the engine…"}</span>
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}
      </aside>

      {mode === "write" && <WriteMode tracks={tracks} state={state} update={update} gridDecision={gridDecision} active={mode === "write"} />}
      {mode === "cues" && <CueMode tracks={tracks} state={state} update={update} gridDecision={gridDecision} active={mode === "cues"} />}
      {mode === "grid" && <>
      <section className="queue">
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => { setTab(t.id); setSel(null); player.stop(); }}>
              {t.label} <b>{groups[t.id].length}</b>
            </button>
          ))}
        </nav>
        <ul>
          {tracks === null && <li className="empty">No audit yet. Choose playlists on the left and run one.</li>}
          {tracks !== null && list.length === 0 && (
            <li className="empty">
              {tab === "review" ? (groups.decided.length ? "All reviewed." : "Nothing flagged.") : "Nothing here."}
            </li>
          )}
          {list.map((t) => {
            const d = decisionFor(t, state.decisions);
            return (
              <li key={t.content_id} className={t === current ? "on" : ""} onClick={() => { setSel(t.content_id); player.stop(); }}>
                <div className="t">{t.title || "(untitled)"}</div>
                <div className="a">{t.artist || "Unknown artist"}</div>
                <div className="tags">
                  {t.findings.map((f) => (
                    <span key={f.check} className={"tag " + f.severity}>{CHECK[f.check] ?? f.check}</span>
                  ))}
                  {d && <span className={"tag " + d.decision}>{d.decision === "accepted" ? "Accepted" : "Dismissed"}</span>}
                  {!t.proposal && t.status === "pass" && <span className="tag ok">{t.rb_bpm?.toFixed(2)}</span>}
                </div>
              </li>
            );
          })}
        </ul>
        {skipped.length > 0 && (
          <p className="notice" role="status">
            {skipped.map(([reason, n]) => `${n} track${n > 1 ? "s" : ""} skipped: ${reason}`).join(" · ")}
          </p>
        )}
        {tracks !== null && (
          <footer>
            {accepted} accepted · {groups.decided.length - accepted} dismissed · {groups.review.length} to review
          </footer>
        )}
      </section>

      <main className="detail">
        {!current && <p className="muted pad">{tracks ? "Select a track." : ""}</p>}
        {current && <Detail t={current} decision={decisionFor(current, state.decisions)} player={player} playClip={playClip} decide={decide} />}
      </main>
      </>}
    </div>
  );
}

function Detail({ t, decision, player, playClip, decide }: {
  t: Track; decision?: Decision; player: ReturnType<typeof usePlayer>;
  playClip: (w: "rekordbox" | "proposed", at?: number) => void;
  decide: (t: Track, d: Decision["decision"] | null) => void;
}) {
  const p = t.previews, pr = t.proposal;
  const which = p && player.playing === p.rekordbox ? "rekordbox" : p && player.playing === p.proposed ? "proposed" : null;
  const head = p && which ? p.start_s + player.pos : null;
  const rbFirst = p?.rekordbox_beats[0]?.[0];

  return (
    <div className="pad">
      <h2>{t.title || "(untitled)"}</h2>
      <p className="artist">{t.artist || "Unknown artist"} · <span className="muted">{t.playlist}</span></p>

      {t.findings.map((f) => (
        <div key={f.check} className={"finding " + f.severity}>
          <strong>{CHECK[f.check] ?? f.check}</strong>
          <p>{f.text}</p>
        </div>
      ))}
      {t.reason && <div className="finding none"><strong>Not audited</strong><p>{t.reason}. Rekordbox's grid is left as it is.</p></div>}
      {t.status === "pass" && (
        <div className="finding pass">
          <strong>Passed</strong>
          <p>
            Rekordbox's {t.rb_bpm?.toFixed(2)} BPM holds against the kicks
            {t.kick_span_s ? ` (${Math.abs(t.drift_ms ?? 0).toFixed(0)} ms slide over ${(t.kick_span_s / 60).toFixed(1)} min)` : ""}.
            Beat position and bar 1 are taken from Rekordbox and not checked.
          </p>
        </div>
      )}

      {pr && (
        <table className="cmp">
          <thead><tr><th /><th><i className="dot rb" />Rekordbox</th><th><i className="dot prop" />Proposed</th></tr></thead>
          <tbody>
            <tr><th>Tempo</th><td>{t.rb_bpm?.toFixed(2)}{(t.rb_tempos ?? 1) > 1 ? ` (${t.rb_tempos} tempos)` : ""}</td><td>{pr.bpm.toFixed(2)}</td></tr>
            <tr><th>First beat</th><td>{t.rb_first_beat_ms != null ? `${t.rb_first_beat_ms.toFixed(0)} ms` : "—"}</td><td>{pr.first_beat_ms.toFixed(0)} ms</td></tr>
            <tr><th>Fitter confidence</th><td colSpan={2}>{t.confidence?.toFixed(2)}{t.crate_bpm ? ` · Crate has ${t.crate_bpm}` : ""}</td></tr>
          </tbody>
        </table>
      )}

      {p && pr && (
        <>
          <div className="wavehead">
            <span>{fmtTime(p.start_s)} – {fmtTime(p.start_s + p.length_s)}, where the grids differ most</span>
            <span className="legend"><i className="sw lo" />kick band <i className="sw hi" />highs · <i className="dot rb" />top <i className="dot prop" />bottom</span>
          </div>
          <Wave
            wave={p.wave} hi={p.wave_hi} start={p.start_s} length={p.length_s} rb={p.rekordbox_beats} proposed={p.proposed_beats}
            playhead={head} height={120} label="Waveform with both grids"
            onSeek={(s) => playClip(which ?? "proposed", s - p.start_s)}
          />
          <div className="wavehead"><span>Close-up: one second at {fmtTime(p.zoom_start_s)}</span></div>
          <Wave
            wave={p.zoom_wave} hi={p.zoom_wave_hi} start={p.zoom_start_s} length={p.zoom_length_s} rb={p.rekordbox_beats} proposed={p.proposed_beats}
            playhead={head} height={150} label="One-second close-up with both grids"
          />
          <div className="play">
            <button className={which === "rekordbox" ? "rb on" : "rb"} onClick={() => playClip("rekordbox")}>
              {which === "rekordbox" ? "■ Stop" : "▶ Rekordbox's grid"} <kbd>1</kbd>
            </button>
            <button className={which === "proposed" ? "prop on" : "prop"} onClick={() => playClip("proposed")}>
              {which === "proposed" ? "■ Stop" : "▶ Proposed grid"} <kbd>2</kbd>
            </button>
            <span className="muted">Clicks mark each beat; the higher click is bar 1. Click the waveform to jump.</span>
          </div>
          {player.error && <p className="error">{player.error}</p>}
          {rbFirst == null && <p className="muted">Rekordbox has no beats in this stretch.</p>}
        </>
      )}

      {pr && (
        <div className="actions">
          {decision ? (
            <>
              <span className={"verdict " + decision.decision}>
                {decision.decision === "accepted" ? "Accepted. Queued for write-back." : "Dismissed. Rekordbox's grid stays."}
              </span>
              <button onClick={() => decide(t, null)}>Undo</button>
            </>
          ) : (
            <>
              <button className="accept" onClick={() => decide(t, "accepted")}>Accept correction <kbd>A</kbd></button>
              <button onClick={() => decide(t, "dismissed")}>Dismiss <kbd>D</kbd></button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

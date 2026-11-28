import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CueDecision, type CueSet, type Decision, type HotCue, type State, type Track } from "./api";
import { Overview } from "./Overview";

type Tab = "review" | "decided" | "none";
const TABS: { id: Tab; label: string }[] = [
  { id: "review", label: "To review" },
  { id: "decided", label: "Decided" },
  { id: "none", label: "No cues" },
];
const SECTION_LABEL = { intro: "Intro", body: "Main", breakdown: "Breakdown", outro: "Outro" };
const fmt = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

/** Which grid the cues sit on: the correction if it was accepted, else Rekordbox's. */
export function cueSetFor(t: Track, gridDecision?: Decision): { set: CueSet | null; grid: "rekordbox" | "proposed" } {
  if (gridDecision?.decision === "accepted" && t.cues?.proposed) return { set: t.cues.proposed, grid: "proposed" };
  return { set: t.cues?.rekordbox ?? null, grid: "rekordbox" };
}
const sigOf = (s: CueSet, grid: string) =>
  s.ok ? grid + ":" + s.hot.map((h) => `${h.slot}${h.bar}`).join(",") + "|" + s.memory.map((m) => m.bar).join(",") : "";
/** The cue decision for a track, if it was made on the cues it has now. */
export function cueDecisionFor(t: Track, all: Record<string, CueDecision> | undefined, gridDecision?: Decision): CueDecision | undefined {
  const d = all?.[t.content_id];
  const { set, grid } = cueSetFor(t, gridDecision);
  return d && set && d.sig === sigOf(set, grid) ? d : undefined;
}
export const cueSigFor = (t: Track, gridDecision?: Decision) => {
  const { set, grid } = cueSetFor(t, gridDecision);
  return set ? sigOf(set, grid) : "";
};
const hasExistingHot = (t: Track) => (t.existing_cues ?? []).some((c) => c.slot);

function useTrackPlayer() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const cache = useRef<{ id: string; url: string }[]>([]);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [pos, setPos] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const a = new Audio();
    audio.current = a;
    let raf = 0;
    const tick = () => { setPos(a.currentTime); raf = requestAnimationFrame(tick); };
    a.onplay = () => { raf = requestAnimationFrame(tick); };
    a.onpause = a.onended = () => { cancelAnimationFrame(raf); setPlayingId(null); };
    a.onerror = () => setError("This audio format would not play here.");
    return () => { cancelAnimationFrame(raf); a.pause(); cache.current.forEach((c) => URL.revokeObjectURL(c.url)); };
  }, []);

  const stop = useCallback(() => audio.current?.pause(), []);
  const play = useCallback(async (t: Track, at: number) => {
    const a = audio.current!;
    setError(null);
    try {
      let hit = cache.current.find((c) => c.id === t.content_id);
      if (!hit) {
        setLoading(true);
        const url = URL.createObjectURL(await api.audio(t));
        hit = { id: t.content_id, url };
        cache.current.unshift(hit);
        for (const old of cache.current.splice(3)) URL.revokeObjectURL(old.url);
      }
      if (a.src !== hit.url) {
        a.src = hit.url;
        await new Promise<void>((res, rej) => { a.onloadedmetadata = () => res(); a.onerror = () => rej(new Error("unplayable")); });
      }
      a.currentTime = Math.max(at, 0);
      await a.play();
      setPlayingId(t.content_id);
    } catch (e) {
      setError(`Could not play the track: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  }, []);
  return { playingId, pos, play, stop, loading, error };
}

type Props = {
  tracks: Track[] | null; state: State; update: (p: Partial<State>) => void;
  gridDecision: (t: Track) => Decision | undefined; active: boolean;
};

export function CueMode({ tracks, state, update, gridDecision, active }: Props) {
  const [tab, setTab] = useState<Tab>("review");
  const [sel, setSel] = useState<string | null>(null);
  const player = useTrackPlayer();

  const decided = useCallback((t: Track): CueDecision | undefined => cueDecisionFor(t, state.cueDecisions, gridDecision(t)),
    [state.cueDecisions, gridDecision]);

  const groups = useMemo(() => {
    const g: Record<Tab, Track[]> = { review: [], decided: [], none: [] };
    for (const t of tracks ?? []) {
      const { set } = cueSetFor(t, gridDecision(t));
      if (!set?.ok) g.none.push(t);
      else g[decided(t) ? "decided" : "review"].push(t);
    }
    return g;
  }, [tracks, decided, gridDecision]);
  const list = groups[tab];
  const current = list.find((t) => t.content_id === sel) ?? list[0] ?? null;

  const decide = useCallback((t: Track, decision: CueDecision["decision"] | null, replace = false) => {
    const { set, grid } = cueSetFor(t, gridDecision(t));
    const d = { ...(state.cueDecisions ?? {}) };
    if (decision && set) d[t.content_id] = { decision, grid, replace, sig: sigOf(set, grid), at: new Date().toISOString() };
    else delete d[t.content_id];
    player.stop();
    if (decision && tab === "review") {
      const i = list.findIndex((x) => x.content_id === t.content_id);
      setSel((list[i + 1] ?? list[i - 1])?.content_id ?? null);
    }
    update({ cueDecisions: d });
  }, [state.cueDecisions, gridDecision, list, tab, update, player]);

  // play from a little before a cue, so the change is heard coming in
  const playFrom = useCallback((t: Track, time: number, lead = 4) => {
    const set = cueSetFor(t, gridDecision(t)).set;
    const barLen = set?.ok && set.hot.length ? (() => {
      const a = set.hot[0], g = set.hot.find((h) => h.slot === "G") ?? set.hot[set.hot.length - 1];
      return g.bar > a.bar ? (g.time - a.time) / (g.bar - a.bar) : 1.8;
    })() : 1.8;
    player.play(t, Math.max(time - lead * barLen, 0));
  }, [player, gridDecision]);

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
      const i = current ? list.indexOf(current) : -1;
      const set = current ? cueSetFor(current, gridDecision(current)).set : null;
      if (e.key === "ArrowDown" && list[i + 1]) { setSel(list[i + 1].content_id); player.stop(); }
      else if (e.key === "ArrowUp" && list[i - 1]) { setSel(list[i - 1].content_id); player.stop(); }
      else if (e.key === " ") player.stop();
      else if (/^[1-8]$/.test(e.key) && current && set?.ok) {
        const c = set.hot.find((h) => h.slot === "ABCDEFGH"[Number(e.key) - 1]);
        if (c) playFrom(current, c.time);
      } else if (current && set?.ok && !decided(current) && e.key.toLowerCase() === "a") decide(current, "accepted", hasExistingHot(current));
      else if (current && set?.ok && !decided(current) && e.key.toLowerCase() === "d") decide(current, "dismissed");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, list, current, decide, decided, player, playFrom, gridDecision]);

  useEffect(() => { if (!active) player.stop(); }, [active, player]);

  const accepted = groups.decided.filter((t) => decided(t)?.decision === "accepted").length;
  const noCuesYet = tracks !== null && tracks.length > 0 && tracks.every((t) => !t.cues);

  return (
    <>
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
          {noCuesYet && <li className="empty">This audit was run before cue placement existed. Run it again.</li>}
          {tracks !== null && !noCuesYet && list.length === 0 && (
            <li className="empty">{tab === "review" ? (groups.decided.length ? "All reviewed." : "Nothing to review.") : "Nothing here."}</li>
          )}
          {!noCuesYet && list.map((t) => {
            const { set } = cueSetFor(t, gridDecision(t));
            const d = decided(t);
            return (
              <li key={t.content_id} className={t === current ? "on" : ""} onClick={() => { setSel(t.content_id); player.stop(); }}>
                <div className="t">{t.title || "(untitled)"}</div>
                <div className="a">{t.artist || "Unknown artist"}</div>
                <div className="tags">
                  {set?.ok && <span className="tag">{set.hot.length} hot · {set.memory.length} memory</span>}
                  {set?.ok && set.confidence === "low" && <span className="tag major">Check by ear</span>}
                  {hasExistingHot(t) && <span className="tag major">Has cues</span>}
                  {d && <span className={"tag " + d.decision}>{d.decision === "accepted" ? (d.replace ? "Replace" : "Accepted") : "Dismissed"}</span>}
                </div>
              </li>
            );
          })}
        </ul>
        {tracks !== null && !noCuesYet && (
          <footer>{accepted} accepted · {groups.decided.length - accepted} dismissed · {groups.review.length} to review</footer>
        )}
      </section>

      <main className="detail">
        {!current && <p className="muted pad">{tracks ? "Select a track." : ""}</p>}
        {current && (
          <CueDetail
            t={current} gridDecision={gridDecision(current)} decision={decided(current)}
            player={player} playFrom={playFrom} decide={decide}
          />
        )}
      </main>
    </>
  );
}

function CueDetail({ t, gridDecision, decision, player, playFrom, decide }: {
  t: Track; gridDecision?: Decision; decision?: CueDecision;
  player: ReturnType<typeof useTrackPlayer>;
  playFrom: (t: Track, time: number, lead?: number) => void;
  decide: (t: Track, d: CueDecision["decision"] | null, replace?: boolean) => void;
}) {
  const { set, grid } = cueSetFor(t, gridDecision);
  const [selSlot, setSelSlot] = useState<string | null>(null);
  const playing = player.playingId === t.content_id;
  const existingHot = (t.existing_cues ?? []).filter((c) => c.slot);
  const existingMem = (t.existing_cues ?? []).filter((c) => !c.slot);
  useEffect(() => setSelSlot(null), [t.content_id]);

  if (!set) return <div className="pad"><h2>{t.title}</h2><p className="muted">No cue data for this track.</p></div>;

  const gridNote = grid === "proposed"
    ? "Placed on the grid correction you accepted."
    : t.proposal
      ? (gridDecision?.decision === "dismissed" ? "Placed on Rekordbox's grid (you dismissed the correction)." : "Placed on Rekordbox's grid. Accepting the grid correction re-places them on that.")
      : "Placed on Rekordbox's grid.";
  const onCue = (c: HotCue) => { setSelSlot(c.slot); playFrom(t, c.time); };

  return (
    <div className="pad">
      <h2>{t.title || "(untitled)"}</h2>
      <p className="artist">{t.artist || "Unknown artist"} · <span className="muted">{t.playlist}</span></p>

      {!set.ok && <div className="finding none"><strong>No cues placed</strong><p>{set.reason}.</p></div>}

      {set.ok && (
        <>
          <p className="muted gridnote">{gridNote}{set.lengths && <> Mix in over <b>{set.lengths.in}</b> bars, mix out over <b>{set.lengths.out}</b>.</>}{set.notes.length ? " " + set.notes.map((n) => n[0].toUpperCase() + n.slice(1)).join(". ") + "." : ""}</p>
          {t.overview && (
            <>
              <div className="wavehead">
                <span>{playing && player.pos != null ? fmt(player.pos) : "Click anywhere to play from there"}{player.loading ? " · loading audio…" : ""}</span>
                <span className="legend">
                  <i className="sw sec-intro" />intro <i className="sw sec-body" />main <i className="sw sec-break" />breakdown <i className="sw sec-outro" />outro
                  {existingHot.length > 0 && <> <i className="sw dashed" />existing</>}
                </span>
              </div>
              <Overview
                ov={t.overview} sections={set.sections} hot={set.hot} memory={set.memory} existing={t.existing_cues}
                playhead={playing ? player.pos : null} selected={selSlot} height={150}
                onSeek={(s) => player.play(t, s)}
              />
            </>
          )}
          <div className="play">
            <button onClick={() => (playing ? player.stop() : playFrom(t, set.hot[0]?.time ?? 0, 0))}>
              {playing ? "■ Stop" : "▶ Play from A"} <kbd>space</kbd>
            </button>
            <span className="muted">Click a cue to hear it come in (starts 4 bars before). Keys 1–8 play A–H.</span>
          </div>
          {player.error && <p className="error">{player.error}</p>}

          <div className="cols">
            <table className="cues">
              <thead><tr><th colSpan={2}>Hot cue</th><th>Bar</th><th>Time</th></tr></thead>
              <tbody>
                {"ABCDEFGH".split("").map((slot) => {
                  const c = set.hot.find((h) => h.slot === slot);
                  return (
                    <tr key={slot} className={(c ? "click" : "emptyrow") + (selSlot === slot ? " on" : "")} onClick={() => c && onCue(c)}>
                      <td><span className={"pad-letter " + (c ? "c-" + c.color : "")}>{slot}</span></td>
                      <td>{c ? c.name : <span className="muted">empty</span>}{c?.end_bar != null && <span className="muted"> · loop</span>}</td>
                      <td className="num">{c ? c.bar + 1 : ""}</td>
                      <td className="num">{c ? fmt(c.time) : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <table className="cues">
              <thead><tr><th>Memory cue</th><th>Bar</th><th>Time</th></tr></thead>
              <tbody>
                {set.memory.map((m) => (
                  <tr key={m.bar} className="click" onClick={() => playFrom(t, m.time)}>
                    <td>▲ {m.name}</td><td className="num">{m.bar + 1}</td><td className="num">{fmt(m.time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="sections">
            {set.sections.map((s, i) => (
              <span key={i} className={"secpill sec-" + s.kind}>{SECTION_LABEL[s.kind]} {s.end - s.start} bars</span>
            ))}
          </div>

          {existingHot.length + existingMem.length > 0 && (
            <div className="finding major">
              <strong>Rekordbox already has cues on this track</strong>
              <p>
                {existingHot.map((c) => `${c.slot} ${c.name || "(unnamed)"} at ${fmt(c.time)}`).join(" · ")}
                {existingMem.length ? `${existingHot.length ? " · " : ""}${existingMem.length} memory cue${existingMem.length > 1 ? "s" : ""}` : ""}.
                {" "}They stay unless you choose to replace them.
              </p>
            </div>
          )}

          <div className="actions">
            {decision ? (
              <>
                <span className={"verdict " + decision.decision}>
                  {decision.decision === "accepted"
                    ? decision.replace ? "Accepted, replacing the existing cues. Queued for write-back." : "Accepted. Queued for write-back."
                    : "Dismissed. No cues will be written."}
                </span>
                <button onClick={() => decide(t, null)}>Undo</button>
              </>
            ) : existingHot.length ? (
              <>
                <button className="accept" onClick={() => decide(t, "accepted", true)}>Replace existing cues <kbd>A</kbd></button>
                <button onClick={() => decide(t, "dismissed")}>Keep existing <kbd>D</kbd></button>
              </>
            ) : (
              <>
                <button className="accept" onClick={() => decide(t, "accepted")}>Accept cues <kbd>A</kbd></button>
                <button onClick={() => decide(t, "dismissed")}>Dismiss <kbd>D</kbd></button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

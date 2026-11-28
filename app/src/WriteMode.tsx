import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type CheckedItem, type Decision, type PlanEntry, type State, type Track, type WriteStatus, type Written } from "./api";
import { cueDecisionFor, cueSetFor, cueSigFor } from "./CueMode";

type Props = {
  tracks: Track[] | null; state: State; update: (p: Partial<State>) => void;
  gridDecision: (t: Track) => Decision | undefined; active: boolean;
};

/** Accepted grids and cues that have not been written yet. */
function buildPlan(tracks: Track[], state: State, gridDecision: Props["gridDecision"]): PlanEntry[] {
  const plan: PlanEntry[] = [];
  for (const t of tracks) {
    const gd = gridDecision(t);
    const cd = cueDecisionFor(t, state.cueDecisions, gd);
    const w = state.written?.[t.content_id];
    const grid = gd?.decision === "accepted" && !!t.proposal && w?.grid !== t.proposal.bpm;
    const cuesWanted = cd?.decision === "accepted" && w?.cues !== cd.sig;
    // the lengths come with accepted cues, from the same placement
    const { set } = cueSetFor(t, gd);
    const text = set?.ok ? set.comment : undefined;
    const lengthsWanted = state.writeLengths !== false && cd?.decision === "accepted" && !!text && w?.lengths !== text;
    // cues and lengths on the corrected grid go only together with that grid
    if ((cuesWanted || lengthsWanted) && cd!.grid === "proposed" && !grid && w?.grid !== t.proposal?.bpm) continue;
    if (grid || cuesWanted || lengthsWanted)
      plan.push({
        content_id: t.content_id, grid, cues: cuesWanted ? { grid: cd!.grid, replace: cd!.replace } : null,
        lengths: lengthsWanted ? cd!.grid : null,
      });
  }
  return plan;
}

const refused = (r: object): r is { refused: string } => "refused" in r;

export function WriteMode({ tracks, state, update, gridDecision, active }: Props) {
  const plan = useMemo(() => (tracks ? buildPlan(tracks, state, gridDecision) : []), [tracks, state, gridDecision]);
  const [status, setStatus] = useState<WriteStatus | null>(null);
  const [items, setItems] = useState<CheckedItem[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [confirmUndo, setConfirmUndo] = useState(false);

  const refresh = useCallback(async () => {
    setBusy("Checking against the library…");
    try {
      const [s, c] = await Promise.all([api.writeStatus(), plan.length ? api.writeCheck(plan) : Promise.resolve(null)]);
      setStatus(s);
      if (c && refused(c)) setMessage({ kind: "error", text: c.refused });
      else setItems(c ? c.items : []);
    } catch (e) {
      setMessage({ kind: "error", text: String(e) });
    } finally {
      setBusy(null);
    }
  }, [plan]);

  useEffect(() => { if (active) refresh(); }, [active, refresh]);

  const ready = (items ?? []).filter((i) => i.ok);
  const open = status?.rekordbox_running ?? false;

  const write = async () => {
    setBusy("Backing up and writing…"); setMessage(null); setConfirmUndo(false);
    try {
      const r = await api.writeApply(plan);
      if (refused(r)) { setMessage({ kind: "error", text: r.refused }); return; }
      const done = r.items.filter((i) => i.ok);
      const written: Record<string, Written> = { ...(state.written ?? {}) };
      const at = new Date().toISOString();
      for (const i of done) {
        const t = tracks!.find((x) => x.content_id === i.content_id)!;
        const prev = written[i.content_id] ?? { at };
        written[i.content_id] = {
          ...prev, at,
          ...(i.grid ? { grid: t.proposal!.bpm } : {}),
          ...(i.cues ? { cues: cueSigFor(t, gridDecision(t)) } : {}),
          ...(i.comment ? { lengths: i.comment.text } : {}),
        };
      }
      update({ written });
      setMessage({ kind: "ok", text: `Wrote ${r.written} track${r.written === 1 ? "" : "s"} into Rekordbox. Backup: ${r.backup}` });
    } catch (e) {
      setMessage({ kind: "error", text: String(e) });
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const undo = async () => {
    const force = !!status?.last?.changed_since;
    if (force && !confirmUndo) { setConfirmUndo(true); return; }
    setBusy("Restoring the backup…"); setMessage(null); setConfirmUndo(false);
    try {
      const r = await api.writeUndo(force);
      if (refused(r)) { setMessage({ kind: "error", text: r.refused }); return; }
      const written = { ...(state.written ?? {}) };
      for (const t of status?.last?.tracks ?? []) delete written[t.content_id];
      update({ written });
      setMessage({ kind: "ok", text: "Undone. Rekordbox's library is back to how it was before that write." });
    } catch (e) {
      setMessage({ kind: "error", text: String(e) });
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const writtenCount = Object.keys(state.written ?? {}).length;

  return (
    <main className="detail write">
      <div className="pad">
        <h2>Write to Rekordbox</h2>
        <p className="muted">
          Accepted grid corrections, cues and mix lengths go straight into Rekordbox's library. Rekordbox has to be closed; every write
          backs up the library first and can be undone.
        </p>
        <label className="check">
          <input type="checkbox" checked={state.writeLengths !== false} onChange={(e) => update({ writeLengths: e.target.checked })} />
          Put the mix-in and mix-out lengths in the comment <span className="muted">(“In 32 · Out 64”, in bars; the rest of the comment is kept)</span>
        </label>

        <div className={"rbstate " + (open ? "open" : "closed")} role="status">
          <span className="dot" />
          {status === null ? "Checking Rekordbox…" : open ? "Rekordbox is open. Quit it to write." : "Rekordbox is closed."}
          <button onClick={refresh} disabled={!!busy}>Check again</button>
        </div>

        {tracks === null && <p className="muted">No audit yet.</p>}
        {tracks !== null && plan.length === 0 && (
          <p className="muted">
            Nothing to write. Accept grid corrections in Grids and cues in Cues first.
            {writtenCount > 0 && ` ${writtenCount} track${writtenCount === 1 ? " has" : "s have"} already been written.`}
          </p>
        )}

        {plan.length > 0 && (
          <table className="plan">
            <thead>
              <tr><th>Track</th><th>Grid</th><th>Cues</th><th>Comment</th><th /></tr>
            </thead>
            <tbody>
              {(items ?? plan.map((p) => ({ content_id: p.content_id } as CheckedItem))).map((i) => {
                const t = tracks?.find((x) => x.content_id === i.content_id);
                return (
                  <tr key={i.content_id} className={i.ok === false ? "skip" : ""}>
                    <td>
                      <div className="t">{t?.title || i.title || "(untitled)"}</div>
                      <div className="a">{t?.artist || i.artist}</div>
                    </td>
                    <td className="num">{i.grid ? <GridChange g={i.grid} /> : "—"}</td>
                    <td>
                      {i.cues ? (
                        <>
                          {i.cues.hot.length} hot · {i.cues.memory.length} memory
                          {i.cues.on === "proposed" && <div className="a">on the corrected grid</div>}
                          {i.cues.replace && i.cues.remove > 0 && <div className="a warn">replaces {i.cues.remove} existing</div>}
                          {i.cues.kept_slots.length > 0 && <div className="a">keeps existing {i.cues.kept_slots.join(", ")}</div>}
                        </>
                      ) : "—"}
                    </td>
                    <td>
                      {i.comment ? (
                        <>
                          {i.comment.text}
                          {i.comment.new !== i.comment.text && <div className="a" title={i.comment.new}>after “{i.comment.new.slice(0, -i.comment.text.length - 3)}”</div>}
                        </>
                      ) : "—"}
                    </td>
                    <td className="res">
                      {items === null ? <span className="muted">…</span> : i.ok ? <span className="tag accepted">Ready</span> : <span className="skipwhy">{i.skip}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <div className="actions">
          {plan.length > 0 && (
            <button className="primary" onClick={write} disabled={!!busy || open || ready.length === 0}>
              {ready.length ? `Back up and write ${ready.length} track${ready.length === 1 ? "" : "s"}` : "Nothing ready to write"}
            </button>
          )}
          {busy && <span className="muted">{busy}</span>}
        </div>
        {message && <p className={message.kind === "error" ? "error" : "okmsg"} role="alert">{message.text}</p>}

        {status?.last && (
          <section className="last">
            <h3>Last write</h3>
            <p>
              {new Date(status.last.created).toLocaleString()} · {status.last.tracks.length} track{status.last.tracks.length === 1 ? "" : "s"}:{" "}
              {status.last.tracks.map((t) => t.title).join(", ")}
            </p>
            {status.last.changed_since && (
              <p className="warn">Rekordbox has saved changes since. Undoing puts back the whole library as it was before the write, so those go too.</p>
            )}
            <button onClick={undo} disabled={!!busy || open} className={confirmUndo ? "danger" : ""}>
              {confirmUndo ? "Undo anyway" : "Undo this write"}
            </button>
            {confirmUndo && <button onClick={() => setConfirmUndo(false)}>Cancel</button>}
          </section>
        )}

        {writtenCount > 0 && (
          <p className="muted small">
            After writing, audit again before writing more grid changes to the same tracks: the audit checks against the grid it saw.
          </p>
        )}
      </div>
    </main>
  );
}

function GridChange({ g }: { g: NonNullable<CheckedItem["grid"]> }) {
  const sameTempo = Math.abs(g.bpm - g.old_bpm) < 0.005;
  const shift = g.shift_ms != null && Math.abs(g.shift_ms) >= 1
    ? `moved ${Math.abs(Math.round(g.shift_ms))} ms ${g.shift_ms > 0 ? "later" : "earlier"}` : null;
  return (
    <>
      {sameTempo ? <b>{g.bpm.toFixed(2)}</b> : <>{g.old_bpm.toFixed(2)} → <b>{g.bpm.toFixed(2)}</b></>}
      {shift && <div className="a">{shift}</div>}
      {g.beats !== g.old_beats && <div className="a">{g.old_beats} → {g.beats} beats</div>}
    </>
  );
}

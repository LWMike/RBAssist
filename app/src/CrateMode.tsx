import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type CrateBuild, type CratePlaylist, type CrateScan, type Progress, type State } from "./api";

type Props = { state: State; update: (p: Partial<State>) => void; active: boolean; loaded: boolean };

const STATE_LABEL = { found: "On this Mac", missing: "Missing", "online-only": "In Dropbox only" };

/** Step 1: Crate playlists into Rekordbox, through an XML Rekordbox imports. */
export function CrateMode({ state, update, active, loaded }: Props) {
  const xml = state.crateXml ?? "";
  const root = state.crateRoot ?? "";
  const picked = useMemo(() => new Set(state.cratePicked ?? []), [state.cratePicked]);
  const [scan, setScan] = useState<CrateScan | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [built, setBuilt] = useState<CrateBuild | null>(null);

  const rescan = useCallback(async () => {
    if (!xml.trim()) { setScan(null); return; }
    setBusy("Reading Crate's playlists…"); setError(null);
    try {
      setScan(await api.crateScan(xml, root));
    } catch (e) {
      setScan(null); setError(String(e));
    } finally {
      setBusy(null);
    }
  }, [xml, root]);

  useEffect(() => { if (active && loaded) rescan(); }, [active, loaded, rescan]);

  const lists = (scan?.playlists ?? []).filter((p) => p.count > 0);
  const current = lists.find((p) => p.name === sel) ?? lists.find((p) => picked.has(p.name)) ?? lists[0] ?? null;
  const chosen = lists.filter((p) => picked.has(p.name));
  const uniq = (f: (t: CratePlaylist["tracks"][number]) => boolean) =>
    new Set(chosen.flatMap((p) => p.tracks.filter(f).map((t) => t.id))).size;
  const total = uniq(() => true), missing = uniq((t) => t.state === "missing"), online = uniq((t) => t.state === "online-only");
  const already = uniq((t) => t.in_rekordbox);

  const toggle = (name: string) => {
    const n = new Set(picked);
    n.has(name) ? n.delete(name) : n.add(name);
    update({ cratePicked: [...n] });
    setBuilt(null);
  };

  const download = async () => {
    setBusy("Fetching from Dropbox…"); setError(null); setProgress(null);
    const off = await api.onDownload(setProgress);
    try {
      const r = await api.crateDownload(xml, root, chosen.map((p) => p.name));
      if (r.failed.length || r.still_online_only) setError(`${r.still_online_only} file${r.still_online_only === 1 ? " is" : "s are"} still only in Dropbox${r.failed.length ? `; ${r.failed[0].error}` : ""}.`);
    } catch (e) {
      setError(String(e));
    } finally {
      off(); setBusy(null); setProgress(null);
      rescan();
    }
  };

  const build = async () => {
    setBusy("Writing the import file…"); setError(null);
    try {
      setBuilt(await api.crateBuild(xml, root, chosen.map((p) => p.name)));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <section className="queue">
        <nav><span className="navtitle">Crate playlists</span></nav>
        <ul>
          {!xml.trim() && <li className="empty">Set the path to Crate's crate.xml on the right.</li>}
          {xml.trim() && !scan && !busy && !error && <li className="empty">Not read yet.</li>}
          {lists.map((p) => (
            <li key={p.name} className={p === current ? "on" : ""} onClick={() => setSel(p.name)}>
              <label className="pick" onClick={(e) => e.stopPropagation()}>
                <input type="checkbox" checked={picked.has(p.name)} onChange={() => toggle(p.name)} disabled={!!busy} />
              </label>
              <div className="t">{p.name}</div>
              <div className="tags">
                <span className="tag">{p.count} tracks</span>
                {p.missing > 0 && <span className="tag major">{p.missing} missing</span>}
                {p.online_only > 0 && <span className="tag major">{p.online_only} in Dropbox only</span>}
                {p.in_rekordbox > 0 && <span className="tag">{p.in_rekordbox === p.count ? "all" : p.in_rekordbox} in Rekordbox</span>}
              </div>
            </li>
          ))}
        </ul>
        {scan && <footer>{chosen.length} chosen · {total} tracks{already ? ` · ${already} already in Rekordbox` : ""}</footer>}
      </section>

      <main className="detail crate">
        <div className="pad">
          <h2>Bring in from Crate</h2>
          <div className="fields">
            <label className="field">
              <span>Crate's crate.xml</span>
              <input
                type="text" spellCheck={false} placeholder="/path/to/crate/exports/crate.xml" defaultValue={xml} key={"x" + loaded}
                onBlur={(e) => e.target.value !== xml && update({ crateXml: e.target.value })}
              />
            </label>
            <label className="field">
              <span>
                Crate's library folder on this Mac <em>(only if the paths differ)</em>
                {scan && <> · Crate's is <code>{scan.crate_root}</code></>}
              </span>
              <input
                type="text" spellCheck={false} placeholder={scan?.crate_root ?? "Same as Crate's"} defaultValue={root} key={"r" + loaded}
                onBlur={(e) => e.target.value !== root && (update({ crateRoot: e.target.value }), setBuilt(null))}
              />
            </label>
          </div>
          {scan && !scan.rekordbox_read && <p className="warn small">Could not read Rekordbox's collection, so tracks it already has are not marked.</p>}

          {current && (
            <>
              <h3 className="sub">{current.name}</h3>
              <table className="plan">
                <thead><tr><th>#</th><th>Track</th><th>Crate</th><th>File</th></tr></thead>
                <tbody>
                  {current.tracks.map((t, i) => (
                    <tr key={t.id + i} className={t.state === "missing" ? "skip" : ""}>
                      <td className="num muted">{i + 1}</td>
                      <td>
                        <div className="t">{t.title}</div>
                        <div className="a">{t.artist}</div>
                      </td>
                      <td className="a">{t.comment}{t.bpm ? ` · ${t.bpm.toFixed(0)}` : ""}</td>
                      <td className="res" title={t.path}>
                        <span className={t.state === "found" ? "muted small" : "skipwhy"}>{STATE_LABEL[t.state]}</span>
                        {t.in_rekordbox && <div className="a">already in Rekordbox</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {chosen.length > 0 && (
            <section className="last">
              <h3>{chosen.length} playlist{chosen.length === 1 ? "" : "s"}, {total} tracks</h3>
              {missing > 0 && <p className="warn small">{missing} file{missing === 1 ? " is" : "s are"} not on this Mac and will be left out.</p>}
              {online > 0 && (
                <p className="small">
                  {online} file{online === 1 ? " is" : "s are"} only in Dropbox. Rekordbox can't analyse them until they are downloaded.{" "}
                  <button onClick={download} disabled={!!busy}>Download {online} from Dropbox</button>
                </p>
              )}
              <div className="actions">
                <button className="primary" onClick={build} disabled={!!busy || total - missing === 0}>Make the Rekordbox import file</button>
                {busy && <span className="muted">{progress ? `${progress.done} of ${progress.total} · ${progress.title}` : busy}</span>}
              </div>
            </section>
          )}
          {error && <p className="error" role="alert">{error}</p>}

          {built && (
            <section className="last">
              <p className="okmsg">
                Import file ready: {built.tracks} tracks in {built.playlists.length} playlist{built.playlists.length === 1 ? "" : "s"}
                {built.left_out.length ? `, ${built.left_out.length} left out (missing)` : ""}.
              </p>
              <p className="small"><code className="path">{built.out}</code>{" "}
                <button onClick={() => navigator.clipboard?.writeText(built.out)}>Copy path</button>
              </p>
              <h3>In Rekordbox</h3>
              <ol className="steps">
                <li>First time only: Preferences › Advanced › Database › rekordbox xml › Imported Library, choose the file above.</li>
                <li>In the browser's <b>rekordbox xml</b> section, click refresh, open the <b>Crate</b> folder.</li>
                <li>Right-click each playlist › <b>Import Playlist</b> (OK on the FLAC notice if it shows). Rekordbox adds the new tracks and analyses them; the playlist appears at the top of Playlists.</li>
                <li>Tracks Rekordbox already has are not added twice and keep their own comment; only new tracks get Crate's.</li>
                <li>Once analysis has finished, audit those playlists here (Grids), then Cues and Write.</li>
              </ol>
              {built.left_out.length > 0 && (
                <details>
                  <summary className="small">Left out</summary>
                  <ul className="small">{built.left_out.map((t) => <li key={t.path}>{t.artist} – {t.title} <span className="muted">({t.playlist})</span></li>)}</ul>
                </details>
              )}
            </section>
          )}
        </div>
      </main>
    </>
  );
}

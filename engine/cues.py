"""M2 cue placement: sections from the audio on an audited grid, then the
"Techno" cue template (SPEC 4.4).

Everything works in bars of the grid it is given, so the same function places
cues on Rekordbox's grid or on a proposed correction.

Hot cues (CDJ limit 8):
  A first kick (load point)   B 4-bar loop from A
  C main breakdown            D drop after it
  E second breakdown          F drop after it
  G mix-out                   H 8-bar outro loop
Memory cues (CDJ limit 10): every section change, named, with the bars to the
next change.
"""
from __future__ import annotations

import numpy as np

import gridfit

SR = gridfit.SR
ENV_SR = gridfit.ENV_SR

MIN_FIRST_RUN = 8     # bars of kick needed for the load point (spec: sustained kick section)
MIN_BREAK = 4         # bars without kick that count as a breakdown
FILL_GAP = 1          # kickless bars shorter than this or equal are fills, not breakdowns
OUTRO_LOOP = 8        # bars
INTRO_LOOP = 4        # bars
MAX_HOT, MAX_MEMORY = 8, 10

COLORS = {"A": "green", "B": "orange", "C": "blue", "D": "red", "E": "blue", "F": "red", "G": "yellow", "H": "orange"}


def _bars(times: np.ndarray, numbers: np.ndarray) -> np.ndarray:
    """Index into `times` of every beat numbered 1, with a complete bar after it."""
    ones = np.flatnonzero(numbers == 1)
    return ones[ones + 4 <= len(times)]


def beat_kicks(x: np.ndarray, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per beat: how closely the low end follows the track's own groove, and
    the low-band level in dB.

    The template is the median shape of the low-band envelope across every beat
    of the track, which in techno is the kick (plus any sidechained bass). A
    beat with the kick correlates near 1; a breakdown beat correlates near 0.
    This copes with rolling and off-beat bass, which a plain onset test does not."""
    low = gridfit._band(x.astype(np.float64), None, 120.0)
    env = gridfit._env(low, win=1024)  # ~23 ms: long enough to smooth out the bass waveform itself
    T = float(np.median(np.diff(times))) if len(times) > 1 else 0.5
    n = int(T * ENV_SR)
    starts = np.round(times * ENV_SR).astype(int) - n // 4
    ok = (starts >= 0) & (starts + n <= len(env))
    score = np.zeros(len(times))
    level = np.full(len(times), -120.0)
    if not ok.any():
        return score, level
    E = np.stack([env[s0:s0 + n] for s0 in starts[ok]]).astype(np.float64)
    Z = E / (E.mean(1, keepdims=True) + 1e-12)
    Z -= Z.mean(1, keepdims=True)
    tpl = np.median(Z, 0)
    tpl -= tpl.mean()
    score[ok] = (Z @ tpl) / (np.linalg.norm(Z, axis=1) * np.linalg.norm(tpl) + 1e-12)
    level[ok] = 20 * np.log10(E.mean(1) + 1e-12)
    return score, level


def bar_features(x: np.ndarray, times: np.ndarray, bars: np.ndarray) -> np.ndarray:
    """Per bar mean level in dB for low (<150), mid (150 Hz–2 kHz) and high (>4 kHz)."""
    bands = [(None, 150.0), (150.0, 2000.0), (4000.0, None)]
    edges = np.append(times[bars], times[bars[-1] + 4] if bars[-1] + 4 < len(times) else times[-1])
    idx = np.round(edges * ENV_SR).astype(int)
    out = np.zeros((len(bars), len(bands)))
    for j, bd in enumerate(bands):
        e = gridfit._env(gridfit._band(x.astype(np.float64), *bd)).astype(np.float64) ** 2
        cs = np.concatenate([[0.0], np.cumsum(e)])
        i0, i1 = idx[:-1].clip(0, len(e)), idx[1:].clip(0, len(e))
        out[:, j] = 10 * np.log10((cs[i1] - cs[i0]) / np.maximum(i1 - i0, 1) + 1e-12)
    return out


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _snap(bar: int, anchor: int, limit: int, step: int = 4, tol: int = 1) -> int:
    """Move a section boundary onto a 4-bar phrase (counted from `anchor`) when
    it is within one bar of one."""
    r = (bar - anchor) % step
    cand = bar - r if r <= tol else (bar + step - r if step - r <= tol else bar)
    return int(min(max(cand, 0), limit))


def place_cues(x: np.ndarray, times: np.ndarray, numbers: np.ndarray) -> dict:
    times = np.asarray(times, float)
    numbers = np.asarray(numbers, int)
    bars = _bars(times, numbers)
    if len(bars) < 32:
        return dict(ok=False, reason="the grid has fewer than 32 bars")
    score, level = beat_kicks(x, times)

    # a beat is a kick when it has a sharp low onset and the low end is not silent
    loud = level > (np.percentile(level[level > -119], 90) - 24.0)
    typical = float(np.percentile(score[loud], 75)) if loud.any() else 0.0
    if typical < 0.5:
        return dict(ok=False, reason="no repeating kick pattern found; cues left to you")
    kick_beat = (score > max(0.4, 0.65 * typical)) & loud
    bar_kick = np.array([kick_beat[b:b + 4].sum() >= 3 for b in bars])
    raw = bar_kick.copy()
    # short gaps are fills, and lone kick bars inside a break are stray hits
    for a, b in _runs(~bar_kick):
        if 0 < a and b < len(bar_kick) and b - a <= FILL_GAP:
            bar_kick[a:b] = True
    for a, b in _runs(bar_kick):
        if b - a <= 1 and 0 < a and b < len(bar_kick):
            bar_kick[a:b] = False
    if bar_kick.mean() < 0.3:
        return dict(ok=False, reason="no steady kick found on this grid; cues left to you")

    runs = _runs(bar_kick)
    nb = len(bars)
    # load point: the first kick bar after which the kick is there for most of
    # the next 16 bars (an intro that drops the kick for a bar or two still counts)
    first = next((i for i in range(nb) if raw[i] and raw[i:i + 16].mean() >= 0.7 and raw[i:i + MIN_FIRST_RUN].sum() >= MIN_FIRST_RUN - 2),
                 runs[0][0])
    # onto the 4-bar phrase if within a bar of it, but never onto a bar without a kick
    snapped = _snap(first, 0, nb)
    if raw[snapped]:
        first = snapped
    last_end = runs[-1][1]  # bar after the last kick bar
    feats = bar_features(x, times, bars)

    # breakdowns: kickless stretches between the first sustained kick and the last kick
    breaks = []
    for a, b in _runs(~bar_kick):
        if a > first and b < last_end and b - a >= MIN_BREAK:
            a2, b2 = _snap(a, 0, nb), _snap(b, 0, nb)
            if b2 - a2 >= MIN_BREAK:
                breaks.append((a2, b2))
    # fallback for breakdowns that keep a filtered kick: a 6 dB dip in the low end for 8+ bars
    if not breaks:
        lowdb = feats[:, 0]
        ref = np.median(lowdb[first:last_end])
        dip = np.zeros(nb, bool)
        dip[first:last_end] = lowdb[first:last_end] < ref - 6.0
        for a, b in _runs(dip):
            if b - a >= 8 and a > first + 8:
                breaks.append((_snap(a, 0, nb), _snap(b, 0, nb)))
    by_len = sorted(breaks, key=lambda r: (-(r[1] - r[0]), r[0]))
    main = by_len[0] if by_len else None
    second = by_len[1] if len(by_len) > 1 else None

    # mix-out: from the last drop, the first 4-bar phrase where mids and highs
    # fall away and stay lower to the end; otherwise 32 bars before the last kick
    body_start = max((b for _, b in breaks), default=first)
    energy = feats[:, 1] + 0.5 * feats[:, 2]
    mix_out = None
    if last_end - body_start >= 24:
        ref = np.median(energy[body_start:min(body_start + 16, last_end)])
        for k in range(body_start + 16 - body_start % 4, last_end - OUTRO_LOOP, 4):
            k = _snap(k, 0, nb)
            if np.median(energy[k:last_end]) < ref - 2.5 and np.median(energy[k:k + 4]) < ref - 2.0:
                mix_out = k
                break
    if mix_out is None:
        mix_out = max(last_end - 32, body_start)
        mix_out = mix_out // 4 * 4 if mix_out // 4 * 4 >= body_start else -(-mix_out // 4) * 4
    # outro loop: the 8-bar phrase after mix-out with the least mid and high energy
    cand = [k for k in range(mix_out - mix_out % 4, last_end - OUTRO_LOOP + 1, 4) if k >= mix_out] or [max(last_end - OUTRO_LOOP, first)]
    outro = min(cand, key=lambda k: (energy[k:k + OUTRO_LOOP].mean(), -k))

    def at(bar: int) -> float:
        return float(times[bars[min(bar, nb - 1)]])

    def end_of(bar: int) -> float:
        i = bars[bar] if bar < nb else len(times) - 1
        return float(times[min(i, len(times) - 1)])

    hot = [dict(slot="A", bar=first, name="First kick")]
    hot.append(dict(slot="B", bar=first, end_bar=first + INTRO_LOOP, name=f"Intro loop {INTRO_LOOP}"))
    if main:
        hot += [dict(slot="C", bar=main[0], name=f"Breakdown {main[1] - main[0]}"), dict(slot="D", bar=main[1], name="Drop")]
    if second:
        hot += [dict(slot="E", bar=second[0], name=f"Breakdown {second[1] - second[0]}"), dict(slot="F", bar=second[1], name="Drop 2")]
    hot.append(dict(slot="G", bar=mix_out, name="Mix out"))
    hot.append(dict(slot="H", bar=outro, end_bar=outro + OUTRO_LOOP, name=f"Outro loop {OUTRO_LOOP}"))
    for h in hot:
        h.update(time=at(h["bar"]), color=COLORS[h["slot"]])
        if "end_bar" in h:
            h["end"] = end_of(h["end_bar"])

    # memory cues: section changes in time order, each with the bars to the next
    events = [(first, "Kick in")]
    for a, b in sorted(breaks):
        events += [(a, "Breakdown"), (b, "Drop")]
    events += [(mix_out, "Mix out"), (last_end, "Kick out")]
    events = sorted({bar: name for bar, name in sorted(events, key=lambda e: e[0])}.items())
    events = [(b, n) for b, n in events if b < nb]
    if len(events) > MAX_MEMORY:  # keep the outer ones and the longest breakdowns
        keep = {events[0][0], events[-1][0], events[-2][0]}
        for a, b in by_len:
            if len(keep) + 2 > MAX_MEMORY:
                break
            keep |= {a, b}
        events = [e for e in events if e[0] in keep]
    memory = []
    for i, (bar, name) in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        comment = f"{name} · {nxt[0] - bar} to {nxt[1].lower()}" if nxt else name
        memory.append(dict(bar=bar, time=at(bar), name=comment))

    sections = [dict(kind="intro", start=0, end=first)]
    cur = first
    for a, b in sorted(breaks):
        sections += [dict(kind="body", start=cur, end=a), dict(kind="breakdown", start=a, end=b)]
        cur = b
    sections += [dict(kind="body", start=cur, end=mix_out), dict(kind="outro", start=mix_out, end=nb)]
    for s in sections:
        s.update(t0=at(s["start"]), t1=end_of(s["end"]))
    sections = [s for s in sections if s["end"] > s["start"]]

    notes = []
    toggles = int(np.abs(np.diff(bar_kick[first:last_end].astype(int))).sum())
    patchy = toggles > max(10, (last_end - first) / 12)
    if patchy:
        notes.append("the kick comes and goes a lot in this track; check the sections by ear")
    if first > 0 and not raw[:first].any():
        notes.append(f"{first} bars of intro before the first kick")
    if not main:
        notes.append("no breakdown found; C–F left empty")
    return dict(ok=True, confidence="low" if patchy else "normal", hot=hot[:MAX_HOT], memory=memory, sections=sections, notes=notes,
                bars=nb, kick_bars=[int(v) for v in bar_kick])


def overview(x: np.ndarray, bins: int = 1400) -> dict:
    """Whole-track peaks for drawing: kick band and highs."""
    out = {}
    for name, band in (("low", (None, 150.0)), ("high", (1000.0, None))):
        y = np.abs(gridfit._band(x.astype(np.float64), *band))
        y = y[:len(y) // bins * bins].reshape(bins, -1).max(1)
        out[name] = [round(float(v), 3) for v in y / max(float(y.max()), 1e-6)]
    out["duration"] = round(len(x) / SR, 3)
    return out

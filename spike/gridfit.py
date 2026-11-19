"""Fixed-tempo beatgrid fitter for 4/4 kick-driven music (spike prototype).

Pipeline: decode -> band envelopes -> tempo from the spectrum of the kick onset
train -> robust regression of fine kick onsets onto beat numbers -> integer-BPM
snap test -> kick sections, first kick -> bar phase from structural changes.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field, asdict

import numpy as np
from scipy import signal

SR = 44100
HOP = 64  # envelope hop: 1.45 ms
ENV_SR = SR / HOP
BPM_MIN, BPM_MAX = 100.0, 180.0


def decode(path: str) -> np.ndarray:
    """Mono float32 at 44.1 kHz via ffmpeg."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32)


def _band(x: np.ndarray, lo: float | None, hi: float | None) -> np.ndarray:
    if lo is None:
        sos = signal.butter(4, hi, "lowpass", fs=SR, output="sos")
    elif hi is None:
        sos = signal.butter(4, lo, "highpass", fs=SR, output="sos")
    else:
        sos = signal.butter(4, [lo, hi], "bandpass", fs=SR, output="sos")
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def _env(x: np.ndarray, hop: int = HOP, win: int = 256) -> np.ndarray:
    """RMS envelope sampled every `hop` samples, window centred on the sample."""
    p = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    centres = np.arange(0, len(x), hop)
    a = np.clip(centres - win // 2, 0, len(x))
    b = np.clip(centres + win // 2, 0, len(x))
    return np.sqrt((p[b] - p[a]) / np.maximum(b - a, 1)).astype(np.float32)


def _flux(env: np.ndarray) -> np.ndarray:
    d = np.diff(env, prepend=env[:1])
    return np.maximum(d, 0.0)


@dataclass
class Grid:
    bpm: float                 # tempo written to the grid
    bpm_fit: float             # unsnapped regression tempo
    snapped: bool
    beat0: float               # seconds: time of beat index 0 (any beat on the grid)
    bar_phase: int             # beat index mod 4 that is a bar "1"
    anchor: float              # seconds: first bar-1 beat at or after 0
    first_kick: float | None   # seconds: load point
    first_kick_beat: int | None
    last_kick: float | None
    duration: float
    n_kicks: int               # beats with a matched kick onset
    kick_coverage: float       # matched kicks / beats in track
    resid_mad_ms: float        # spread of kick onsets about the grid
    drift_ms: float            # residual trend, first quarter vs last quarter of kicks
    snap_drift_ms: float       # drift the integer snap would cost across the kick span
    wander_ms: float           # worst local phase offset (30 s windows)
    bar_conf: float            # share of structural evidence on the chosen bar phase
    confidence: float
    flags: list[str] = field(default_factory=list)
    sections: list[tuple[int, int]] = field(default_factory=list)  # kick runs, beat indices
    changes: list[int] = field(default_factory=list)               # structural change beats

    def beat_time(self, k: int | np.ndarray) -> float | np.ndarray:
        return self.beat0 + np.asarray(k) * 60.0 / self.bpm

    def to_dict(self) -> dict:
        return asdict(self)


def _tempo_spectrum(onset: np.ndarray, lo: float = BPM_MIN, hi: float = BPM_MAX) -> tuple[float, float]:
    """Tempo (BPM) and phase (seconds of a beat) from the onset train's spectrum."""
    n = len(onset)
    o = onset - onset.mean()
    nfft = 1 << int(np.ceil(np.log2(n)) + 2)
    spec = np.fft.rfft(o * np.hanning(n), nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / ENV_SR)
    mag = np.abs(spec)

    def at(f):
        return np.interp(f, freqs, mag)

    cand = np.arange(lo, hi, 0.02) / 60.0
    score = at(cand) + 0.5 * at(2 * cand) + 0.25 * at(4 * cand)
    f0 = cand[int(np.argmax(score))]
    # refine by direct evaluation around the peak
    t = np.arange(n) / ENV_SR
    fine = f0 + np.linspace(-0.3, 0.3, 601) / 60.0
    best_f, best_v, best_ph = f0, -1.0, 0.0
    w = o * np.hanning(n)
    for f in fine:
        z = np.dot(w, np.exp(-2j * np.pi * f * t))
        if abs(z) > best_v:
            best_f, best_v, best_ph = f, abs(z), np.angle(z)
    period = 1.0 / best_f
    phase = (-best_ph / (2 * np.pi)) * period % period
    return 60.0 * best_f, phase


def _fine_onsets(x_env_fine: np.ndarray, fine_sr: float, pred: np.ndarray, half_win: float) -> tuple[np.ndarray, np.ndarray]:
    """For each predicted beat time, the time of the steepest rise nearby and its strength."""
    d = np.maximum(np.diff(x_env_fine, prepend=x_env_fine[:1]), 0.0)
    # light smoothing so a single-sample spike does not win
    d = np.convolve(d, np.ones(3) / 3.0, mode="same")
    hw = int(round(half_win * fine_sr))
    idx = np.round(pred * fine_sr).astype(int)
    times = np.full(len(pred), np.nan)
    strength = np.zeros(len(pred))
    for i, c in enumerate(idx):
        a, b = c - hw, c + hw + 1
        if a < 0 or b > len(d):
            continue
        seg = d[a:b]
        j = int(np.argmax(seg))
        times[i] = (a + j) / fine_sr
        strength[i] = seg[j]
    return times, strength


def _smooth(v: np.ndarray, ms: float) -> np.ndarray:
    n = max(int(SR * ms / 1000.0), 1)
    return np.convolve(v, np.ones(n) / n, mode="same")


def _attack_offset(x: np.ndarray, low: np.ndarray, beat_times: np.ndarray, T: float) -> float:
    """Seconds from the given grid to the start of the stacked kick.

    The kick is the biggest burst of low-end in the beat, so its start is taken
    from the low band alone: find the steepest rise of the stacked low-band
    envelope and follow that slope back down to the level before it. Anything
    quieter that happens earlier (bass tails, percussion, pre-delay) is ignored.
    """
    half = int(0.5 * T * SR)
    cs = np.round(beat_times * SR).astype(int)
    cs = cs[(cs - half > 0) & (cs + half < len(x))]
    if len(cs) < 8:
        return 0.0
    if len(cs) > 400:
        cs = cs[np.linspace(0, len(cs) - 1, 400).astype(int)]
    idx = cs[:, None] + np.arange(-half, half)[None, :]
    # analytic envelope per kick: no rectification ripple from the sub itself
    env = np.abs(signal.hilbert(low[idx].astype(np.float64), axis=1))
    E = np.median(env, axis=0)
    n = len(E)
    SM = 6.0
    Ee = _smooth(np.concatenate([E, E, E]), SM)  # tiled: a kick on the window edge stays whole
    d = np.diff(Ee)
    # The incoming grid already sits on the kick's body (it was fitted to the
    # kick template), so only look just before it. This keeps an off-beat bass
    # note, which can rise faster than the kick, from being mistaken for it.
    c = n + half
    w0, w1 = c - int(0.110 * SR), c + int(0.040 * SR)
    i = w0 + int(np.argmax(d[w0:w1]))
    slope = d[i]
    base = Ee[i - int(0.060 * SR): i + 1].min()
    t_low = i - (Ee[i] - base) / max(slope, 1e-12) + (SM / 2000.0) * SR
    # A kick's click leads its sub. Take the sharpest full-band rise in the
    # 50 ms up to the low-end burst as the beat.
    F = np.median(np.abs(x[idx]), axis=0)
    Fe = _smooth(np.concatenate([F, F, F]), 1.0)
    a0, a1 = int(t_low) - int(0.050 * SR), int(t_low) + int(0.008 * SR)
    df = np.diff(Fe[a0:a1])
    onset = a0 + int(np.argmax(df))
    off = (onset - n - half) / SR
    return float((off + T / 2) % T - T / 2)


def _robust_line(k: np.ndarray, t: np.ndarray, iters: int = 4) -> tuple[float, float, np.ndarray]:
    keep = np.ones(len(k), bool)
    slope = intercept = 0.0
    for _ in range(iters):
        slope, intercept = np.polyfit(k[keep], t[keep], 1)
        r = t - (intercept + slope * k)
        mad = np.median(np.abs(r[keep] - np.median(r[keep]))) + 1e-6
        keep = np.abs(r - np.median(r[keep])) < 4.0 * 1.4826 * mad
    return slope, intercept, keep


def _runs(mask: np.ndarray, max_gap: int, min_len: int) -> list[tuple[int, int]]:
    """Runs of True as (start, end_exclusive), bridging gaps up to max_gap."""
    runs, start, gap = [], None, 0
    for i, v in enumerate(mask):
        if v:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                end = i - gap + 1
                if end - start >= min_len:
                    runs.append((start, end))
                start, gap = None, 0
    if start is not None:
        end = len(mask) - gap
        if end - start >= min_len:
            runs.append((start, end))
    return runs


def fit(x: np.ndarray, hint_bpm: float | None = None) -> Grid:
    """Fit a grid. With a tempo hint (e.g. Crate's BPM), a second fit restricted
    to the hint is tried when the free fit lands elsewhere; the more confident wins."""
    g = _fit(x, BPM_MIN, BPM_MAX)
    if hint_bpm:
        h = hint_bpm * 2 if hint_bpm < 95 else hint_bpm
        if abs(g.bpm - h) > 0.6:
            g2 = _fit(x, h * 0.98, h * 1.02)
            g2.flags.append("tempo-from-hint")
            g.flags.append("tempo-disagrees-with-hint")
            if g2.confidence >= g.confidence:
                return g2
    return g


def _fit(x: np.ndarray, bpm_lo: float, bpm_hi: float) -> Grid:
    dur = len(x) / SR
    low = _band(x, None, 150.0)
    low_env = _env(low, HOP, 512)
    onset = _flux(low_env)

    bpm0, phase0 = _tempo_spectrum(onset, bpm_lo, bpm_hi)
    T0 = 60.0 / bpm0

    # Fine timing from the full-band envelope: a kick's click is broadband and
    # rises faster than its sub, so it pins the transient better than the low band.
    FINE_HOP = 16
    fine_sr = SR / FINE_HOP
    fine_env = _env(x, FINE_HOP, 64)

    n_beats = int(dur / T0) + 2
    k_all = np.arange(-1, n_beats)
    pred = phase0 + k_all * T0
    ok = (pred > 0.05) & (pred < dur - 0.05)
    k_all, pred = k_all[ok], pred[ok]

    # Which beats carry a kick: low-band flux peak close to the predicted beat.
    hw = int(round(0.035 * ENV_SR))
    centres = np.round(pred * ENV_SR).astype(int)
    kick_strength = np.array([
        onset[max(c - hw, 0): c + hw + 1].max() if c + hw < len(onset) else 0.0 for c in centres
    ])
    ref = np.percentile(kick_strength, 75) if len(kick_strength) else 0.0
    is_kick = kick_strength > 0.25 * ref

    # Pass 1: steepest full-band rise near each predicted kick. Noisy per beat,
    # but over hundreds of beats it pins the tempo well enough to stack kicks.
    slope, intercept = T0, phase0
    keep = np.zeros(len(k_all), bool)
    t_fine = np.full(len(k_all), np.nan)
    p = intercept + slope * k_all
    t1, _ = _fine_onsets(fine_env, fine_sr, p, 0.030)
    valid = is_kick & ~np.isnan(t1)
    if valid.sum() >= 16:
        slope, intercept, _ = _robust_line(k_all[valid].astype(float), t1[valid])

    # Pass 2: stack every kick into a median template of the low-band envelope,
    # then time each kick by cross-correlating against it. Hats and percussion
    # fall out of the median, so the lag is the kick's and nothing else's.
    low_fine = _env(low, FINE_HOP, 256)
    pre, post, maxlag = int(0.012 * fine_sr), int(0.060 * fine_sr), int(0.018 * fine_sr)
    W = pre + post
    corr_q = np.zeros(len(k_all))
    for _ in range(2):
        p = intercept + slope * k_all
        c0 = np.round(p * fine_sr).astype(int)
        inb = (c0 - pre - maxlag >= 0) & (c0 + post + maxlag < len(low_fine))
        cand = np.flatnonzero(is_kick & inb)
        if len(cand) < 16:
            break
        offs = np.arange(-pre - maxlag, post + maxlag)
        segs = low_fine[c0[cand, None] + offs[None, :]].astype(np.float64)
        tmpl = np.median(segs[:, maxlag:maxlag + W], axis=0)
        tz = tmpl - tmpl.mean()
        tz /= np.linalg.norm(tz) + 1e-12
        cc = np.empty((len(cand), 2 * maxlag + 1))
        for li in range(2 * maxlag + 1):
            w = segs[:, li:li + W]
            wz = w - w.mean(axis=1, keepdims=True)
            cc[:, li] = (wz @ tz) / (np.linalg.norm(wz, axis=1) + 1e-12)
        j = cc.argmax(axis=1)
        jj = np.clip(j, 1, 2 * maxlag - 1)
        y0, y1, y2 = cc[np.arange(len(cand)), jj - 1], cc[np.arange(len(cand)), jj], cc[np.arange(len(cand)), jj + 1]
        frac = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 - 1e-12)
        lag = (jj + np.clip(frac, -1, 1) - maxlag) / fine_sr
        q = cc.max(axis=1)
        good = (q > 0.6) & (j > 0) & (j < 2 * maxlag)
        if good.sum() < 16:
            break
        t_fine[:] = np.nan
        t_fine[cand[good]] = p[cand[good]] + lag[good]
        corr_q[:] = 0
        corr_q[cand] = q
        s, i0, kp = _robust_line(k_all[cand[good]].astype(float), t_fine[cand[good]])
        slope, intercept = s, i0
        keep = np.zeros(len(k_all), bool)
        keep[cand[good][kp]] = True

    # Where on the stacked kick is "the beat"? The transient: the steepest rise
    # of the full-band envelope, stacked the same way on the fitted grid.
    onset_shift = 0.0
    if keep.sum() >= 16:
        c1 = np.round(t_fine[keep] * fine_sr).astype(int)
        r = int(0.030 * fine_sr)
        c1 = c1[(c1 - r >= 0) & (c1 + r < len(fine_env))]
        stack = np.median(fine_env[c1[:, None] + np.arange(-r, r)[None, :]], axis=0)
        d = np.convolve(np.maximum(np.diff(stack, prepend=stack[:1]), 0), np.ones(3) / 3, mode="same")
        onset_shift = (int(np.argmax(d)) - r) / fine_sr
        t_fine = t_fine + onset_shift
        intercept += onset_shift

    flags: list[str] = []
    if keep.sum() < 16:
        flags.append("too-few-kicks")
    bpm_fit = 60.0 / slope
    kk = k_all[keep].astype(float)
    tt = t_fine[keep] if keep.any() else np.array([])
    span_beats = (kk.max() - kk.min()) if len(kk) else 0.0

    # Integer snap: accept when the cost across the kick span is inaudible.
    bpm_int = round(bpm_fit)
    snap_drift_ms = abs(60.0 / bpm_int - slope) * span_beats * 1000.0
    snapped = snap_drift_ms < 8.0
    if snapped:
        bpm = float(bpm_int)
        T = 60.0 / bpm
        beat0 = float(np.median(tt - kk * T)) if len(kk) else intercept
    else:
        bpm = bpm_fit
        T = slope
        beat0 = intercept

    # Absolute position: put the beat on the START of the kick's attack. Stack
    # every kick over a full beat on the fitted grid, find the quietest moment
    # before the low-band peak, and take the first rise out of it.
    if len(kk) >= 16:
        delta = _attack_offset(x, low, beat0 + kk * T, T)
        beat0 += delta
        tt = tt + delta

    resid = (tt - (beat0 + kk * T)) * 1000.0 if len(kk) else np.array([0.0])
    resid_mad = float(1.4826 * np.median(np.abs(resid - np.median(resid))))
    q = max(len(resid) // 4, 1)
    drift = float(np.median(resid[-q:]) - np.median(resid[:q]))
    # local wander: median residual per ~30 s window
    wander = 0.0
    if len(kk) > 64:
        w_beats = max(int(30.0 / T), 16)
        bins = ((kk - kk.min()) // w_beats).astype(int)
        meds = [np.median(resid[bins == b]) for b in np.unique(bins) if (bins == b).sum() >= 8]
        if meds:
            wander = float(np.max(np.abs(meds)))
    if wander > 12.0:
        flags.append("tempo-wander")

    # --- structure on the beat grid -------------------------------------
    nb = int((dur - beat0) / T)
    k0 = int(np.ceil(-beat0 / T))  # first beat index at or after 0 s
    beats = np.arange(k0, nb)
    bt = beat0 + beats * T
    c = np.round(bt * ENV_SR).astype(int)
    hw2 = int(round(0.035 * ENV_SR))
    ks = np.array([onset[max(i - hw2, 0): i + hw2 + 1].max() if i + hw2 < len(onset) else 0.0 for i in c])
    kick_mask = ks > 0.25 * (np.percentile(ks, 75) if len(ks) else 0.0)
    sections_rel = _runs(kick_mask, max_gap=2, min_len=16)
    long_runs = [r for r in sections_rel if r[1] - r[0] >= 32] or sections_rel
    first_kick_beat = int(beats[long_runs[0][0]]) if long_runs else None
    last_kick_beat = int(beats[sections_rel[-1][1] - 1]) if sections_rel else None

    # per-beat band energies for novelty
    bands = [low_env, _env(_band(x, 150.0, 800.0), HOP, 512), _env(_band(x, 800.0, 5000.0), HOP, 512), _env(_band(x, 5000.0, None), HOP, 512)]
    edges = np.round((beat0 + np.arange(k0, nb + 1) * T) * ENV_SR).astype(int).clip(0, len(low_env) - 1)
    feats = np.zeros((len(beats), len(bands)))
    for bi, e in enumerate(bands):
        cs = np.concatenate([[0.0], np.cumsum(e.astype(np.float64) ** 2)])
        seg = (cs[edges[1:]] - cs[edges[:-1]]) / np.maximum(edges[1:] - edges[:-1], 1)
        feats[:, bi] = 10 * np.log10(seg + 1e-9)
    L = 8
    nov = np.zeros(len(beats))
    for i in range(L, len(beats) - L):
        nov[i] = np.abs(feats[i:i + L].mean(0) - feats[i - L:i].mean(0)).sum()
    peaks, props = signal.find_peaks(nov, height=max(np.percentile(nov, 90), 6.0), distance=8)
    changes = [(int(beats[p]), float(nov[p])) for p in peaks]

    # bar phase: kick section boundaries are exact; novelty peaks are +-1 beat
    votes = np.zeros(4)
    for a, b in sections_rel:
        votes[int(beats[a]) % 4] += 3.0
        votes[int(beats[b - 1] + 1) % 4] += 1.5
    for kbeat, h in changes:
        wgt = min(h / 12.0, 2.0)
        votes[kbeat % 4] += wgt
        votes[(kbeat - 1) % 4] += 0.25 * wgt
        votes[(kbeat + 1) % 4] += 0.25 * wgt
    bar_phase = int(np.argmax(votes)) if votes.sum() > 0 else (first_kick_beat or 0) % 4
    bar_conf = float(votes[bar_phase] / votes.sum()) if votes.sum() > 0 else 0.0
    if bar_conf < 0.5:
        flags.append("bar-phase-unsure")
    if first_kick_beat is not None and first_kick_beat % 4 != bar_phase:
        flags.append("first-kick-off-bar")

    ka = k0 + ((bar_phase - k0) % 4)
    anchor = beat0 + ka * T

    coverage = float(keep.sum() / max(len(k_all), 1))
    if coverage < 0.35:
        flags.append("sparse-kicks")
    if resid_mad > 4.0:
        flags.append("loose-onsets")
    if not snapped and abs(bpm_fit - round(bpm_fit)) > 0.02:
        flags.append("non-integer-bpm")

    conf = 1.0
    conf *= np.clip(1.0 - (resid_mad - 1.5) / 8.0, 0.2, 1.0)
    conf *= np.clip(coverage / 0.5, 0.3, 1.0)
    conf *= np.clip(1.0 - (wander - 4.0) / 20.0, 0.2, 1.0)
    conf *= 0.6 + 0.4 * min(bar_conf / 0.6, 1.0)

    return Grid(
        bpm=round(bpm, 2) if snapped else bpm, bpm_fit=float(bpm_fit), snapped=bool(snapped),
        beat0=float(beat0), bar_phase=bar_phase, anchor=float(anchor),
        first_kick=float(beat0 + first_kick_beat * T) if first_kick_beat is not None else None,
        first_kick_beat=first_kick_beat,
        last_kick=float(beat0 + last_kick_beat * T) if last_kick_beat is not None else None,
        duration=float(dur), n_kicks=int(keep.sum()), kick_coverage=coverage,
        resid_mad_ms=resid_mad, drift_ms=drift, snap_drift_ms=float(snap_drift_ms),
        wander_ms=wander, bar_conf=bar_conf, confidence=float(conf), flags=flags,
        sections=[(int(beats[a]), int(beats[b - 1]) + 1) for a, b in sections_rel],
        changes=[c for c, _ in changes],
    )


def click_preview(x: np.ndarray, g: Grid, start: float, length: float = 20.0) -> np.ndarray:
    """Excerpt with a click on every beat; bar 1 is higher pitched."""
    a = max(int(start * SR), 0)
    b = min(a + int(length * SR), len(x))
    y = x[a:b].astype(np.float32) * 0.7
    T = 60.0 / g.bpm
    k_lo = int(np.ceil((a / SR - g.beat0) / T))
    k_hi = int(np.floor((b / SR - g.beat0) / T))
    n = int(0.012 * SR)
    env = np.exp(-np.arange(n) / (0.003 * SR))
    for k in range(k_lo, k_hi + 1):
        pos = int(round((g.beat0 + k * T) * SR)) - a
        if pos < 0 or pos + n > len(y):
            continue
        f = 2400.0 if (k - g.bar_phase) % 4 == 0 else 1500.0
        y[pos:pos + n] += (0.6 * np.sin(2 * np.pi * f * np.arange(n) / SR) * env).astype(np.float32)
    return np.clip(y, -1.0, 1.0)

import { useEffect, useRef } from "react";
import type { HotCue, MemoryCue, Overview as Ov, Section, ExistingCue } from "./api";

type Props = {
  ov: Ov; sections: Section[]; hot: HotCue[]; memory: MemoryCue[]; existing?: ExistingCue[];
  playhead: number | null; selected: string | null; height: number; onSeek: (t: number) => void;
};

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const SECTION_VAR: Record<Section["kind"], string> = {
  intro: "--sec-intro", body: "--sec-body", breakdown: "--sec-break", outro: "--sec-outro",
};

/** Whole track: sections shaded, hot cues flagged along the top, memory cues along the bottom. */
export function Overview({ ov, sections, hot, memory, existing, playhead, selected, height, onSeek }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const draw = () => {
      const w = cv.clientWidth, h = height, dpr = window.devicePixelRatio || 1;
      cv.width = w * dpr; cv.height = h * dpr;
      const g = cv.getContext("2d")!;
      g.scale(dpr, dpr);
      g.clearRect(0, 0, w, h);
      const top = 22, bot = h - 16, mid = (top + bot) / 2, half = (bot - top) / 2;
      const x = (t: number) => (t / ov.duration) * w;

      for (const s of sections) {
        g.fillStyle = css(SECTION_VAR[s.kind]);
        g.fillRect(x(s.t0), top, Math.max(x(s.t1) - x(s.t0), 1), bot - top);
      }
      const bars = (v: number[], color: string, scale: number) => {
        g.fillStyle = color;
        const n = v.length;
        for (let i = 0; i < n; i++) {
          const a = Math.max(v[i] * half * scale, 0.5);
          g.fillRect((i / n) * w, mid - a, Math.max(w / n, 1), a * 2);
        }
      };
      bars(ov.low, css("--wave"), 0.95);
      bars(ov.high, css("--wave-hi"), 0.5);

      // loops as bands, then cue lines and flags
      for (const c of hot) {
        if (c.end == null) continue;
        g.fillStyle = css("--loop");
        g.fillRect(x(c.time), top, Math.max(x(c.end) - x(c.time), 2), bot - top);
      }
      g.font = "600 11px -apple-system, system-ui, sans-serif";
      g.textBaseline = "middle";
      // flags for cues at the same time stack sideways
      const used: number[] = [];
      for (const c of hot) {
        const px = Math.round(x(c.time));
        let fx = px;
        while (used.some((u) => Math.abs(u - fx) < 17)) fx += 17;
        used.push(fx);
        const on = selected === c.slot;
        g.fillStyle = css(`--cue-${c.color}`) || css("--prop");
        g.globalAlpha = on ? 1 : 0.85;
        g.fillRect(px - (on ? 1 : 0.5), top, on ? 2 : 1, bot - top);
        g.fillRect(fx - 1, 3, 16, 16);
        g.globalAlpha = 1;
        g.fillStyle = "#111";
        g.fillText(c.slot, fx + 3, 11.5);
      }
      g.fillStyle = css("--text");
      for (const m of memory) {
        const px = x(m.time);
        g.beginPath();
        g.moveTo(px, bot + 2); g.lineTo(px - 5, h - 2); g.lineTo(px + 5, h - 2); g.closePath();
        g.fill();
      }
      if (existing?.length) {
        g.strokeStyle = css("--muted");
        g.setLineDash([3, 3]);
        for (const e of existing) {
          const px = Math.round(x(e.time)) + 0.5;
          g.beginPath(); g.moveTo(px, top); g.lineTo(px, bot); g.stroke();
        }
        g.setLineDash([]);
      }
      if (playhead != null) {
        g.fillStyle = css("--text");
        g.fillRect(x(playhead) - 0.75, top - 2, 1.5, bot - top + 4);
      }
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(cv);
    return () => ro.disconnect();
  }, [ov, sections, hot, memory, existing, playhead, selected, height]);

  return (
    <canvas
      ref={ref}
      className="wave seekable"
      style={{ height }}
      role="img"
      aria-label="Whole track with sections and cues"
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        onSeek(((e.clientX - r.left) / r.width) * ov.duration);
      }}
    />
  );
}

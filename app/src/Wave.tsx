import { useEffect, useRef } from "react";
import type { Beat } from "./api";

type Props = {
  wave: number[]; hi?: number[]; start: number; length: number;
  rb: Beat[]; proposed: Beat[];
  playhead?: number | null; height: number;
  onSeek?: (t: number) => void; label: string;
};

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Waveform with Rekordbox's beats marked in the top half and the proposal's in the bottom half. */
export function Wave({ wave, hi, start, length, rb, proposed, playhead, height, onSeek, label }: Props) {
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
      const mid = h / 2, x = (t: number) => ((t - start) / length) * w;
      const bars = (v: number[], color: string) => {
        g.fillStyle = color;
        const n = v.length;
        for (let i = 0; i < n; i++) {
          const a = Math.max(v[i] * (mid - 12), 0.5);
          g.fillRect((i / n) * w, mid - a, Math.max(w / n, 1), a * 2);
        }
      };
      bars(wave, css("--wave"));
      if (hi?.length) bars(hi.map((v) => v * 0.55), css("--wave-hi"));
      const ticks = (beats: Beat[], color: string, y0: number, y1: number) => {
        for (const [t, num] of beats) {
          const px = x(t);
          if (px < 0 || px > w) continue;
          g.fillStyle = color;
          g.globalAlpha = num === 1 ? 1 : 0.75;
          g.fillRect(Math.round(px) - (num === 1 ? 1 : 0.5), y0, num === 1 ? 2 : 1, y1 - y0);
        }
        g.globalAlpha = 1;
      };
      ticks(rb, css("--rb"), 0, mid - 1);
      ticks(proposed, css("--prop"), mid + 1, h);
      if (playhead != null && playhead >= start && playhead <= start + length) {
        g.fillStyle = css("--text");
        g.fillRect(x(playhead) - 0.5, 0, 1.5, h);
      }
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(cv);
    return () => ro.disconnect();
  }, [wave, hi, start, length, rb, proposed, playhead, height]);

  return (
    <canvas
      ref={ref}
      className={"wave" + (onSeek ? " seekable" : "")}
      style={{ height }}
      role="img"
      aria-label={label}
      onClick={(e) => {
        if (!onSeek) return;
        const r = e.currentTarget.getBoundingClientRect();
        onSeek(start + ((e.clientX - r.left) / r.width) * length);
      }}
    />
  );
}

# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy"]
# ///
"""Rewrite grids and cues for the tracks of one playlist in the test XML from
the current results, and sanity-check each new grid against the audio."""
import json, os, sys
import xml.etree.ElementTree as ET
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import gridfit

xml_path, results_json, playlist = sys.argv[1:4]
res = {r["id"]: r for r in json.load(open(results_json)) if "error" not in r}
tree = ET.parse(xml_path); root = tree.getroot()
node = next(n for n in root.iter("NODE") if n.get("Name") == playlist)
ids = [t.get("Key") for t in node.findall("TRACK")]
tracks = {t.get("TrackID"): t for t in root.find("COLLECTION")}
SR = gridfit.SR
print(f"{'bpm':>7} {'old':>8} {'new':>8} {'shift':>7} {'before':>7} {'after':>6}  track   (levels: stacked loudness 10 ms before / after the beat, % of kick peak)")
for i in ids:
    t, r = tracks[i], res[i]
    old = float(t.find("TEMPO").get("Inizio"))
    for el in list(t):
        t.remove(el)
    T = 60.0 / r["bpm"]
    t.set("AverageBpm", f"{r['bpm']:.2f}")
    ET.SubElement(t, "TEMPO", Inizio=f"{r['anchor']:.3f}", Bpm=f"{r['bpm']:.2f}", Metro="4/4", Battito="1")
    fk = r["first_kick"]
    ET.SubElement(t, "POSITION_MARK", Name="First kick", Type="0", Start=f"{fk:.3f}", Num="0", Red="40", Green="226", Blue="20")
    ET.SubElement(t, "POSITION_MARK", Name="Intro loop 4 bars", Type="4", Start=f"{fk:.3f}", End=f"{fk + 16 * T:.3f}", Num="1", Red="255", Green="140", Blue="0")
    ET.SubElement(t, "POSITION_MARK", Name="Load", Type="0", Start=f"{fk:.3f}", Num="-1")
    if r["last_kick"]:
        ET.SubElement(t, "POSITION_MARK", Name="Last kick", Type="0", Start=f"{r['last_kick']:.3f}", Num="-1")
    # independent look: stack the raw waveform on the new grid
    x = np.abs(gridfit.decode(r["path"]))
    ks = np.array([k for a, b in r["sections"] for k in range(a, b)])
    half = int(0.5 * T * SR)
    cs = np.round((r["beat0"] + ks * T) * SR).astype(int); cs = cs[(cs - half > 0) & (cs + half < len(x))]
    st = np.median(x[cs[:, None] + np.arange(-half, half)[None, :]], axis=0)
    n10 = int(0.010 * SR); peak = np.convolve(st, np.ones(n10) / n10, "same").max()
    before, after = st[half - n10:half].mean() / peak * 100, st[half:half + n10].mean() / peak * 100
    shift = ((r["anchor"] - old + T / 2) % T - T / 2) * 1000
    print(f"{r['bpm']:7.2f} {old:8.3f} {r['anchor']:8.3f} {shift:7.1f} {before:6.0f}% {after:5.0f}%  {r['artist'][:16]} - {r['name'][:28]} [{r['path'][-4:]}] conf {r['confidence']:.2f} {' '.join(r['flags'])}")
ET.indent(root); tree.write(xml_path, encoding="UTF-8", xml_declaration=True)

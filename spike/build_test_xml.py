"""Build a small Rekordbox XML with grids and cues from spike results.

usage: python3 spike/build_test_xml.py <crate.xml> spike/out/results.json spike/out/rbassist-test.xml
"""
import json
import sys
import xml.etree.ElementTree as ET

crate_xml, results_json, out = sys.argv[1:4]
res = {r["id"]: r for r in json.load(open(results_json)) if "error" not in r}
src = {t.get("TrackID"): t for t in ET.parse(crate_xml).getroot().find("COLLECTION")}

hi = [r for r in res.values() if r["confidence"] >= 0.9 and r["first_kick"] is not None and "first-kick-off-bar" not in r["flags"]]
flac = [r for r in hi if r["path"].endswith(".flac")]
late = sorted([r for r in flac if r["first_kick"] > 10], key=lambda r: -r["first_kick"])[:3]   # beatless intros
early = [r for r in flac if r["first_kick"] < 1][:5]
mp3 = [r for r in hi if r["path"].endswith(".mp3")][:2]
picks = early + late + mp3

root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
ET.SubElement(root, "PRODUCT", Name="RBAssist", Version="0.0-spike", Company="")
coll = ET.SubElement(root, "COLLECTION", Entries=str(len(picks)))
for r in picks:
    t = ET.SubElement(coll, "TRACK", dict(src[r["id"]].attrib))
    t.set("AverageBpm", f"{r['bpm']:.2f}")
    T = 60.0 / r["bpm"]
    ET.SubElement(t, "TEMPO", Inizio=f"{r['anchor']:.3f}", Bpm=f"{r['bpm']:.2f}", Metro="4/4", Battito="1")
    fk = r["first_kick"]
    ET.SubElement(t, "POSITION_MARK", Name="First kick", Type="0", Start=f"{fk:.3f}", Num="-1")
    ET.SubElement(t, "POSITION_MARK", Name="First kick", Type="0", Start=f"{fk:.3f}", Num="0", Red="40", Green="226", Blue="20")
    ET.SubElement(t, "POSITION_MARK", Name="Intro loop 4 bars", Type="4", Start=f"{fk:.3f}", End=f"{fk + 16 * T:.3f}", Num="1", Red="255", Green="140", Blue="0")
    if r["last_kick"]:
        ET.SubElement(t, "POSITION_MARK", Name="Last kick", Type="0", Start=f"{r['last_kick']:.3f}", Num="-1")
pl = ET.SubElement(root, "PLAYLISTS")
rootnode = ET.SubElement(pl, "NODE", Type="0", Name="ROOT", Count="1")
node = ET.SubElement(rootnode, "NODE", Name="RBAssist spike test", Type="1", KeyType="0", Entries=str(len(picks)))
for r in picks:
    ET.SubElement(node, "TRACK", Key=r["id"])
ET.indent(root)
ET.ElementTree(root).write(out, encoding="UTF-8", xml_declaration=True)
for r in picks:
    print(f"{r['bpm']:7.2f}  grid@{r['anchor']:7.3f}s  first kick {r['first_kick']:7.3f}s  {r['path'].rsplit('.',1)[1]:4}  {r['artist']} - {r['name']}")

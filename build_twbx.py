#!/usr/bin/env python3
"""Package template/ + your peloton_data.hyper into a Tableau workbook (.twbx).

Usage:  python build_twbx.py [path/to/peloton_data.hyper]
Output: Peloton Ride Story.twbx  (open in Tableau Desktop 2026.4 / build 26.1026+)
"""
import sys, zipfile
from pathlib import Path

here = Path(__file__).resolve().parent
tpl = here / "template"
hyper = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "peloton_data.hyper"
out = here / "Peloton Ride Story.twbx"
if not hyper.exists():
    sys.exit(f"Missing {hyper}. Run peloton_to_hyper.py first.")

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(tpl / "Peloton Ride Story.twb", "Peloton Ride Story.twb")
    z.write(hyper, "Data/PelotonTracking/peloton_data.hyper")
    for f in (tpl / "Packages").rglob("*"):
        if f.is_file():
            z.write(f, f.relative_to(tpl).as_posix())
print(f"Wrote {out.name} ({out.stat().st_size/1024:,.0f} KB)")

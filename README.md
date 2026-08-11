# Infrastructure Map

Static, GitHub Pages-ready visualizer for the 15 shortlisted Indian cities in the
German-language and health-infrastructure analysis.

## What this version shows

- Zoom levels: **Automatic**, **State**, **City**, **Infrastructure** (no district tier).
- Three view modes, raw counts only, no derived/composite scores:
  1. **Language vs Health** (default) — blue = language infrastructure, green = health infrastructure.
  2. **By category pair** — NMC Medical Colleges / NABH Health Facilities / INC Nursing Colleges split out on
     the health side; Formal German Infrastructure vs General Skilling Infrastructure on the language side.
  3. **Fully disaggregated** — all 9 raw source categories, each its own color.
- At State/City zoom, bubbles are colored by a fixed per-location color (not by domain), sized by the total
  of whatever the active view mode is counting.
- At Infrastructure zoom, individual points render from `data/infrastructure-cleaned.json`, colored per the
  active view mode.
- Hover (not click) shows a short detail card after ~550ms: counts at State/City level, or
  name/category/city at Infrastructure level.
- Below the zoom-level buttons: a plain table, one row per state or city, columns = the active view mode's
  raw count categories. No ranking, no computed column.

## Known data gaps — do not paper over these

- Individual **Infrastructure**-zoom points for PDOT/SIIC/IISC collapse into one subtype,
  "General Skilling Infrastructure (PDOT/SIIC/IISC)", because the source sheet doesn't retain which of the
  three a merged row came from. City/state tables still show `pdot_siics` and `iiscs` as separate counts.
- **Private German training organisations** (`private_training`) and **Goethe/TELC exam centres**
  (`exam_centres`) have no individually geocoded rows anywhere in the source sheets — they exist only as
  city/state totals. They will never appear as dots at Infrastructure zoom; that's correct, not a bug.
- There is no **ownership (Govt./Private)** field or per-facility capacity parameter in any source file.
  The Infrastructure-zoom hover card says so explicitly rather than guessing.

## Coordinate cleanup

`prep/normalize.py` never reuses a flagged coordinate as-is: if a researched override isn't present in
`COORDINATE_OVERRIDES`, the row is written with `latitude`/`longitude: null` and
`coordinateStatus: "undefined_flagged"`. The browser filters those out (plus missing-coordinate and `(0,0)`
rows) before clustering and logs the result as
`[infrastructure-layer] renderable=... dropped=... total=...`.

As of the last data pull: **4,681** scoped rows, **4,377** renderable, **304** dropped (303
`undefined_flagged`, 1 other invalid coordinate). The drop is concentrated in **INC Nursing Colleges**
(262 of 506 nursing-college rows — about half) and **General Skilling Infrastructure** (38 of 118 rows);
every other category lost 2 rows or fewer. Re-run `prep/normalize.py` after any source-sheet update to
refresh these counts — `data/*.json` in this repo is a point-in-time snapshot, not regenerated automatically.

## Local preview

Run a static file server from this folder, then open `src/index.html`.

```powershell
python -m http.server 5177 --bind 127.0.0.1
```

Then visit `http://127.0.0.1:5177/src/index.html`.

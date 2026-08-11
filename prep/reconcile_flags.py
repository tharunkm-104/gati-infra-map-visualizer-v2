"""Severity-aware reconciliation of flagged coordinates.

Background
----------
`normalize.py::flagged_keys()` collapses all three flag workbooks into one flat
set of (name, lat, lon) keys and nulls the coordinate for every match. That
discards the `Flag` column, which carries the *severity* of each flag. The
result: 304 of 4,681 in-scope rows were dropped, but only a handful of them
were flagged as actually having a wrong location.

This script re-reads the flag workbooks WITH severity and resolves each class
separately. It rewrites data/infrastructure-cleaned.json in place and emits an
auditable reconciliation workbook. It does not modify the source Geomapped
workbook.

Resolution policy (agreed with GATI before running):
  A. PIN-centroid classes  -> restore coordinate, status "pin_centroid"
                              (renders, but drawn as a hollow dot)
  B. Benign repeat classes -> restore coordinate; first occurrence per
                              (name, city) keeps status "source", later ones
                              become "duplicate_collapsed" (not rendered)
  C. Genuine-error classes -> resolved case by case in RESOLUTIONS below
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SOURCE_DIR = Path("/mnt/user-data/uploads")
FLAGGED_DUPLICATES = SOURCE_DIR / "Flagged_Coordinate_Duplicates.xlsx"
FLAGGED_NMC_INC = SOURCE_DIR / "Flagged_NMC_INC_Coordinates.xlsx"
OUT_WORKBOOK = ROOT / "outputs" / "Flag_Reconciliation.xlsx"

PIN_CENTROID_FLAGS = {
    "different institutions, same PIN code area (likely PIN-code centroid fallback)",
    "same-city, different orgs (likely centroid fallback)",
}
BENIGN_REPEAT_FLAGS = {
    "same city, same/similar institution repeated (probably benign)",
    "same-city, same org repeated (plausibly legitimate)",
    "same city, likely duplicate/renamed listing of same institution (probably benign)",
}
ERROR_FLAGS = {
    "CROSS-CITY DUPLICATE (near-certain error)",
    "different institutions, different PIN codes, identical coordinate (likely error)",
    "DIFFERENT institutions/cities, identical coordinate (likely error)",
}

# ---- C. case-by-case resolutions for the genuine-error class ----------------
# Keyed by a distinctive lowercase fragment of the institution name.
# action: "restore"  -> flag was a false positive, the coordinate is correct
#         "override" -> coordinate replaced with a researched one
#         "keep"     -> could not verify, stays undefined_flagged
RESOLUTIONS = [
    {
        "match": "jagannath gupta institute of medical sciences",
        "action": "restore",
        "note": "Shared coordinate is the Kolkata and Budge Budge listings of the SAME "
                "campus (K.P. Mondal Road, Buita, Budge Budge, 700137). Wikipedia gives "
                "22.4533875, 88.1705799 -- effectively the source coordinate. False positive.",
        "source": "https://en.wikipedia.org/wiki/Jagannath_Gupta_Institute_of_Medical_Sciences_and_Hospital",
    },
    {
        "match": "esic medical college & hospital, basaidarapur",
        "action": "override",
        "lat": 28.6564,
        "lon": 28.6564 and 77.1289,
        "note": "Source coordinate 28.5911,77.3482 is the ESIC Noida campus, which shares "
                "the row. The Delhi campus is on Ring Road, Basaidarapur, 110015, adjacent "
                "to ESI-Basaidarapur metro (28.6583, 77.1274). Override set to the hospital "
                "complex.",
        "source": "https://basaidarapurhospital.esic.gov.in/esichospital-about-us",
    },
    {
        "match": "nirmala school of nursing",
        "action": "restore",
        "note": "Same premises as Shri Maruthi College of Nursing (132/1 Sante Circle, "
                "Chintamani Road, Hoskote). Co-located institutions, coordinate is correct "
                "for Hoskote. NOTE: Hoskote is in Bangalore Rural, not Bengaluru Urban -- "
                "this row should be re-examined by the city-limits check.",
        "source": "address string in source row",
    },
    {
        "match": "shri maruthi college of nursing",
        "action": "restore",
        "note": "See Nirmala School of Nursing -- same premises in Hoskote. Coordinate "
                "correct; city attribution (PIN 560078) is inconsistent with the address "
                "and should be caught by the city-limits check.",
        "source": "address string in source row",
    },
    {
        "match": "council of education and development programmes",
        "action": "restore",
        "note": "Three listings of the same organisation (Mumbai x2, Thane). Coordinate "
                "19.19109, 72.97261 is a real CEDP location but sits in Thane, not Mumbai. "
                "Coordinate restored; city attribution left for the city-limits check.",
        "source": "shared-coordinate analysis of the flag sheet",
    },
    {
        "match": "new directions educational society",
        "action": "restore",
        "note": "Same organisation listed under Hyderabad and Yadadri. Coordinate falls "
                "inside Hyderabad. Duplicate listing, not a wrong location.",
        "source": "shared-coordinate analysis of the flag sheet",
    },
    {
        "match": "sims healthcare",
        "action": "restore",
        "note": "Same organisation listed under Hyderabad and Ranga Reddy. Coordinate falls "
                "inside Hyderabad (Madhapur area). Duplicate listing, not a wrong location.",
        "source": "shared-coordinate analysis of the flag sheet",
    },
    {
        "match": "synchroserve global solutions",
        "action": "restore",
        "note": "Same organisation listed under Hyderabad and Malkajgiri. Coordinate falls "
                "inside Hyderabad. Duplicate listing, not a wrong location.",
        "source": "shared-coordinate analysis of the flag sheet",
    },
    {
        "match": "h m r school of nursing",
        "action": "override",
        "lat": 12.9899522,
        "lon": 77.5023973,
        "note": "One of four distinct Bangalore nursing colleges collapsed onto a single "
                "false point (12.96629, 77.40109). Address is Magadi Main Road, "
                "Sunkadakatte; resolved to the Sunkadakatte locality via OSM Nominatim. "
                "Locality-level precision, not building-level.",
        "source": "OSM Nominatim: Sunkadakatte, Magadi Road, Bengaluru",
    },
    {
        "match": "sri vinayaka college of nursing",
        "action": "override",
        "lat": 13.0065559,
        "lon": 77.4520643,
        "note": "Same false-point cluster. Address is No 81, Machohalli, Magadi Main Road; "
                "resolved to the Machohalli locality via OSM Nominatim. Locality-level "
                "precision, not building-level.",
        "source": "OSM Nominatim: Machohalli, Bangalore North, 560091",
    },
    {
        "match": "brite college of nursing",
        "action": "keep",
        "note": "Same false-point cluster. Address is Chikkagollarahatti, Pipeline Road; "
                "Chikkagollarahatti did not resolve in OSM Nominatim and no authoritative "
                "coordinate was found. Left flagged rather than guessed.",
        "source": None,
    },
    {
        "match": "east west college of nursing",
        "action": "keep",
        "note": "Same false-point cluster. Address is BEL Layout, Off Magadi Road, "
                "Vishwaneedam Post; the only OSM match for 'BEL Layout' is Vidyaranyapura "
                "in north Bengaluru, a different place. Left flagged rather than guessed.",
        "source": None,
    },
]


def slug(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def load_flag_map() -> dict[str, list[tuple[str, object, object]]]:
    """name-slug -> [(flag, latitude, longitude), ...] across all three sheets."""
    flags: dict[str, list[tuple[str, object, object]]] = defaultdict(list)
    sources = [
        (FLAGGED_DUPLICATES, "Flagged Duplicates", "Name"),
        (FLAGGED_NMC_INC, "NMC Flagged", "Institution"),
        (FLAGGED_NMC_INC, "INC Flagged", "Institution"),
    ]
    for path, sheet, name_col in sources:
        df = pd.read_excel(path, sheet_name=sheet)
        for _, row in df.iterrows():
            flags[slug(row[name_col])].append((row["Flag"], row["Latitude"], row["Longitude"]))
    return flags


def numeric(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def resolution_for(name: str) -> dict | None:
    key = slug(name)
    for res in RESOLUTIONS:
        if res["match"] in key:
            return res
    return None


def main() -> None:
    flags = load_flag_map()
    rows = json.loads((DATA_DIR / "infrastructure-cleaned.json").read_text(encoding="utf-8"))

    audit: list[dict] = []
    benign_rows: list[dict] = []
    unmatched = 0

    for row in rows:
        if row.get("coordinateStatus") != "undefined_flagged":
            continue

        entries = flags.get(slug(row.get("name")))
        if not entries:
            unmatched += 1
            continue

        flag_text, lat, lon = entries[0]
        lat, lon = numeric(lat), numeric(lon)
        before = row["coordinateStatus"]
        note = ""
        source_url = None

        if flag_text in PIN_CENTROID_FLAGS:
            flag_class = "pin_centroid"
            if lat is not None and lon is not None:
                row["latitude"], row["longitude"] = lat, lon
                row["coordinateStatus"] = "pin_centroid"
                note = "Coordinate is a PIN-code centroid: city is correct, precise position is not."
            else:
                note = "PIN-centroid class but no usable coordinate in the flag sheet; left flagged."

        elif flag_text in BENIGN_REPEAT_FLAGS:
            # Deliberately provisional. The upstream sheet labels this class
            # "same/similar institution repeated", but inspection shows most of
            # these groups are DIFFERENT institutions sharing one coordinate
            # (e.g. a government nursing school and a private college both
            # pinned to the same hospital campus point). A second pass below
            # separates true repeats from shared-point cases.
            flag_class = "benign_repeat"
            if lat is None or lon is None:
                note = "Benign-repeat class but no usable coordinate; left flagged."
            else:
                row["latitude"], row["longitude"] = lat, lon
                row["coordinateStatus"] = "source"
                note = "Coordinate restored; refined in the shared-point pass."
                benign_rows.append(row)

        elif flag_text in ERROR_FLAGS:
            flag_class = "coordinate_error"
            res = resolution_for(row.get("name") or "")
            if res is None:
                note = "Error class with no entry in RESOLUTIONS; left flagged."
            elif res["action"] == "restore":
                row["latitude"], row["longitude"] = lat, lon
                row["coordinateStatus"] = "source"
                note, source_url = res["note"], res.get("source")
            elif res["action"] == "override":
                row["latitude"], row["longitude"] = res["lat"], res["lon"]
                row["coordinateStatus"] = "researched_override"
                note, source_url = res["note"], res.get("source")
            else:
                note, source_url = res["note"], res.get("source")
        else:
            flag_class = "unclassified"
            note = f"Unrecognised flag text; left flagged: {flag_text}"

        audit.append(
            {
                "Name": row.get("name"),
                "Subtype": row.get("subtype"),
                "City": row.get("city"),
                "District": row.get("district"),
                "Flag (source sheet)": flag_text,
                "Flag class": flag_class,
                "Status before": before,
                "Status after": row["coordinateStatus"],
                "Latitude before": None,
                "Longitude before": None,
                "Latitude after": row["latitude"],
                "Longitude after": row["longitude"],
                "Resolution note": note,
                "Evidence": source_url,
            }
        )

    # ---- second pass over the benign-repeat class ------------------------
    # Group by exact coordinate. Within a group, cluster names by similarity:
    #   * a name-cluster with >1 member is a true repeat  -> keep the first as
    #     "source", collapse the rest to "duplicate_collapsed"
    #   * a coordinate group containing >1 distinct institution means the point
    #     is shared, not precise -> every survivor becomes "pin_centroid"
    from difflib import SequenceMatcher

    def name_core(value: str) -> str:
        return slug(value).split(",")[0][:45]

    coord_groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in benign_rows:
        coord_groups[(round(row["latitude"], 6), round(row["longitude"], 6))].append(row)

    for group in coord_groups.values():
        clusters: list[list[dict]] = []
        for row in group:
            core = name_core(row.get("name") or "")
            for cluster in clusters:
                if SequenceMatcher(None, core, name_core(cluster[0].get("name") or "")).ratio() >= 0.85:
                    cluster.append(row)
                    break
            else:
                clusters.append([row])

        survivors = []
        for cluster in clusters:
            survivors.append(cluster[0])
            for dup in cluster[1:]:
                dup["coordinateStatus"] = "duplicate_collapsed"
                dup["_note"] = "Repeat listing of the same institution at the same coordinate; collapsed."

        if len(clusters) > 1:
            for row in survivors:
                row["coordinateStatus"] = "pin_centroid"
                row["_note"] = (
                    f"Coordinate is shared with {len(clusters) - 1} other distinct institution(s); "
                    "treated as an imprecise shared point, not a verified position."
                )
        else:
            for row in survivors:
                row["_note"] = "Sole institution at this coordinate; coordinate accepted as-is."

    # fold the second-pass outcome back into the audit trail
    by_id = {id(row): row for row in benign_rows}
    for entry, row in zip(
        [e for e in audit if e["Flag class"] == "benign_repeat"],
        [r for r in rows if id(r) in by_id],
    ):
        entry["Status after"] = row["coordinateStatus"]
        entry["Latitude after"] = row["latitude"]
        entry["Longitude after"] = row["longitude"]
        if row.get("_note"):
            entry["Resolution note"] = row.pop("_note")

    for row in rows:
        row.pop("_note", None)

    (DATA_DIR / "infrastructure-cleaned.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- report, mirroring the existing print block in normalize.py ----
    audit_df = pd.DataFrame(audit)
    renderable = [
        r for r in rows
        if r["coordinateStatus"] in {"source", "pin_centroid", "researched_override"}
        and r["latitude"] is not None and r["longitude"] is not None
    ]
    print(f"total rows            : {len(rows)}")
    print(f"renderable after recon: {len(renderable)}")
    print(f"unmatched in flag map : {unmatched}")
    print("\nstatus counts:")
    for status, count in pd.Series([r["coordinateStatus"] for r in rows]).value_counts().items():
        print(f"  {count:>5}  {status}")
    print("\nresolution by flag class:")
    print(audit_df.groupby(["Flag class", "Status after"]).size().to_string())
    print("\nresolution by subtype:")
    print(audit_df.groupby(["Subtype", "Status after"]).size().to_string())
    print("\nresolution by city:")
    print(audit_df.groupby(["City", "Status after"]).size().to_string())

    OUT_WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT_WORKBOOK, engine="openpyxl") as writer:
        summary = (
            audit_df.groupby(["Flag class", "Status after"]).size().reset_index(name="Rows")
        )
        summary.to_excel(writer, sheet_name="Summary", index=False)
        audit_df.to_excel(writer, sheet_name="Row-level resolutions", index=False)
        audit_df[audit_df["Flag class"] == "coordinate_error"].to_excel(
            writer, sheet_name="Researched errors", index=False
        )
    print(f"\nwrote {OUT_WORKBOOK}")


if __name__ == "__main__":
    main()

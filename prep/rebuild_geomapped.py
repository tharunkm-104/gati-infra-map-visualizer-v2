"""Emit a reconciled copy of the Geomapped workbook.

The original `Geomapped - City mapping sheet.xlsx` is NOT modified. This writes
a new workbook whose facility sheets carry the original columns untouched, plus
appended reconciliation columns so every change is auditable against the source:

    final_latitude, final_longitude, coordinate_status, flag_class, resolution_note

The "City Mapping" sheet is copied through verbatim: it is a sectioned,
header-less layout that the row-level join cannot address safely. Its flagged
rows are still resolved in the JSON and appear in the Reconciliation Log sheet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/mnt/user-data/uploads/Geomapped_-_City_mapping_sheet.xlsx")
RECON = ROOT / "outputs" / "Flag_Reconciliation.xlsx"
OUT = ROOT / "outputs" / "Geomapped_-_City_mapping_sheet_RECONCILED.xlsx"

FACILITY_SHEETS = {
    "Copy of NMC- Medical Colleges": "Institution Name & Address",
    "Copy of INC - Nursing Colleges": "Institution Name & Address",
}


def slug(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def main() -> None:
    infra = json.loads((ROOT / "data" / "infrastructure-cleaned.json").read_text(encoding="utf-8"))
    audit = pd.read_excel(RECON, sheet_name="Row-level resolutions")

    resolved = {slug(r.get("name")): r for r in infra}
    notes = {slug(r["Name"]): (r["Flag class"], r["Resolution note"]) for _, r in audit.iterrows()}

    book = pd.read_excel(SOURCE, sheet_name=None, header=None)
    frames: dict[str, pd.DataFrame] = {}

    for sheet, name_col in FACILITY_SHEETS.items():
        df = pd.read_excel(SOURCE, sheet_name=sheet)
        finals = {"final_latitude": [], "final_longitude": [], "coordinate_status": [],
                  "flag_class": [], "resolution_note": []}
        for _, row in df.iterrows():
            key = slug(row.get(name_col))
            match = resolved.get(key)
            flag_class, note = notes.get(key, ("", ""))
            if match is None:
                # row is outside the 15-city scope, so the visualizer never saw it
                finals["final_latitude"].append(row.get("Latitude"))
                finals["final_longitude"].append(row.get("Longitude"))
                finals["coordinate_status"].append("out_of_scope")
                finals["flag_class"].append("")
                finals["resolution_note"].append("Not among the 15 shortlisted cities.")
                continue
            finals["final_latitude"].append(match["latitude"])
            finals["final_longitude"].append(match["longitude"])
            finals["coordinate_status"].append(match["coordinateStatus"])
            finals["flag_class"].append(flag_class)
            finals["resolution_note"].append(note)
        for column, values in finals.items():
            df[column] = values
        frames[sheet] = df

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
        book["City Mapping"].to_excel(writer, sheet_name="City Mapping", index=False, header=False)
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet[:31], index=False)
        audit.to_excel(writer, sheet_name="Reconciliation Log", index=False)

    for sheet, df in frames.items():
        print(sheet, df["coordinate_status"].value_counts().to_dict())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

"""Attach facility type, NABH status, eligibility and inferred ownership to the
NABH rows in data/infrastructure-cleaned.json.

Sources
-------
* `_master_clean.csv` -- the NABH scrape. Supplies Program_Types,
  Underlying_Type_Hint, Status and Eligibility_Flag. The JSON's NABH rows were
  built from this file in order, so the join is positional and asserted below.
* The NABH Program-Type Eligibility matrix (supplied by GATI) -- supplies the
  corridor-eligibility verdict per program type.

Ownership honesty note
----------------------
`_master_clean.csv` has NO ownership column. Ownership here is INFERRED from
operator keywords in the facility name and address (ESIC, Railway, Municipal
Corporation, AIIMS, District Hospital, and so on). Every inferred row records
`ownershipBasis` naming the keyword that triggered it. Rows with no keyword are
"Unknown" and must not be read as "Private" -- most private hospitals simply
carry no operator marker in their name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MASTER = Path("/mnt/user-data/uploads/_master_clean.csv")
TARGET = ROOT / "data" / "infrastructure-cleaned.json"

# ---- eligibility verdicts from the GATI program-type matrix -----------------
ELIGIBILITY_BY_PROGRAM = {
    "Hospitals": "Y", "Entry-Level Hospitals": "Y", "SHCO": "Y",
    "Entry-Level SHCO": "Y", "Entry Level SHCO/SCHO": "Y",
    "Nursing Home / Nursing Excellence": "Y", "Care Home": "Y",
    "Eye Care Organisation": "Y", "Allopathic Clinics": "Y",
    "Allopathic Clinics 2nd Edition": "Y", "Dental Empanelment (CGHS/ECHS)": "Y",
    "Emergency Deptt": "-",
    "CGHS/ECHS Empaneled Hospital": "Case-by-Case",
    "AYUSH Hospitals": "N", "Entry Level Ayush Hospital": "N",
    "Entry Level Ayush Center": "N", "Panchakarma Clinics": "N",
    "Blood Bank": "N", "Medical Imaging Services": "N",
    "Ethics Committee (non-clinical)": "N",
}

# Priority order when a facility carries several program types: the most
# corridor-relevant inpatient type wins as the primary label.
TYPE_PRIORITY = [
    "Hospitals", "CGHS/ECHS Empaneled Hospital", "Entry-Level Hospitals",
    "SHCO", "Entry-Level SHCO", "Nursing Home / Nursing Excellence",
    "Care Home", "AYUSH Hospitals", "Entry Level Ayush Hospital",
    "Eye Care Organisation", "Dental Facilities", "Allopathic Clinics",
    "Medical Laboratory", "Medical Imaging Services", "Blood Bank",
    "Panchakarma Clinics", "Ethics Committee (non-clinical)",
]

# ---- ownership keyword inference -------------------------------------------
# (regex, label, human-readable basis)
GOVERNMENT_PATTERNS = [
    (r"\besic\b|employees?'? state insurance", "Government", "ESIC"),
    (r"\bgovt\.?\b|\bgovernment\b|\bsarkari\b", "Government", "Government in name"),
    (r"\bmunicipal\b|\bcorporation hospital\b|\bbmc\b|\bmcgm\b", "Government", "Municipal body"),
    (r"\bdistrict hospital\b|\bcivil hospital\b|\btaluk hospital\b|\bgeneral hospital\b",
     "Government", "District/civil/general hospital"),
    (r"\brailway\b|\bnorthern railway\b|\bsouth central railway\b", "Government", "Railway"),
    (r"\baiims\b|\bpgimer\b|\bjipmer\b|\bnimhans\b|\bsctimst\b", "Government", "Central institute"),
    (r"\bmilitary\b|\barmy\b|\bnaval\b|\bair force\b|\bcommand hospital\b|\bechs polyclinic\b",
     "Government", "Armed forces"),
    (r"\bprimary health cent|\bcommunity health cent|\bphc\b|\bchc\b", "Government", "PHC/CHC"),
    (r"\bmedical college\b.*\bgovt|\bgovt\b.*\bmedical college\b", "Government", "Government medical college"),
    (r"\bcentral government health scheme\b|\bcghs\b wellness", "Government", "CGHS wellness centre"),
    (r"\bstate\b.*\bhospital\b|\bzilla\b|\bzila\b", "Government", "State/zilla facility"),
]
PRIVATE_PATTERNS = [
    (r"\bpvt\.? ?ltd\b|\bprivate limited\b|\bp\.? ?ltd\b", "Private", "Pvt Ltd in name"),
    (r"\bapollo\b|\bfortis\b|\bmanipal\b|\bmax \b|\bmedanta\b|\bnarayana\b|\bcolumbia asia\b|"
     r"\baster\b|\bkims\b|\byashoda\b|\bruby hall\b|\bjupiter\b|\bwockhardt\b|\bcloudnine\b",
     "Private", "Known private chain"),
    (r"\btrust\b|\bcharitable\b|\bfoundation\b|\bsociety\b|\bmission hospital\b|\bseva\b",
     "Private (not-for-profit)", "Trust/charitable/society"),
]


def infer_ownership(name: str, address: str) -> tuple[str, str]:
    blob = f"{name} {address}".lower()
    for pattern, label, basis in GOVERNMENT_PATTERNS:
        if re.search(pattern, blob):
            return label, f"Inferred from name/address: {basis}"
    for pattern, label, basis in PRIVATE_PATTERNS:
        if re.search(pattern, blob):
            return label, f"Inferred from name/address: {basis}"
    return "Unknown", "No operator keyword in name or address; not determinable from source data"


def split_types(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()]


def primary_type(types: list[str], hint: object) -> str:
    for candidate in TYPE_PRIORITY:
        if candidate in types:
            return candidate
    if types and types[0] not in {"Unspecified / Other", "CGHS/ECHS Empanelment (type undetermined)"}:
        return types[0]
    if isinstance(hint, str) and hint.strip():
        return hint.strip()
    return types[0] if types else "Unspecified / Other"


def main() -> None:
    master = pd.read_csv(MASTER, low_memory=False)
    rows = json.loads(TARGET.read_text(encoding="utf-8"))
    nabh = [row for row in rows if str(row.get("subtype", "")).startswith("NABH")]

    if len(nabh) != len(master):
        raise SystemExit(f"row count mismatch: json={len(nabh)} csv={len(master)}")
    mismatches = sum(
        1 for row, name in zip(nabh, master["HCO Name"])
        if str(row.get("name")).strip() != str(name).strip()
    )
    if mismatches:
        raise SystemExit(f"positional join is not safe: {mismatches} name mismatches")

    counts: dict[str, int] = {}
    for row, (_, src) in zip(nabh, master.iterrows()):
        types = split_types(src.get("Program_Types"))
        hint = src.get("Underlying_Type_Hint")
        primary = primary_type(types, hint)
        ownership, basis = infer_ownership(str(src.get("HCO Name") or ""), str(src.get("Address") or ""))

        row["facilityType"] = primary
        row["facilityTypes"] = types
        row["facilityTypeSource"] = (
            "Program_Types" if types and primary in types else "Underlying_Type_Hint (name-based)"
        )
        row["nabhStatus"] = None if pd.isna(src.get("Status")) else str(src.get("Status"))
        row["corridorEligibility"] = ELIGIBILITY_BY_PROGRAM.get(primary, "Not in matrix")
        row["eligibilityFlag"] = None if pd.isna(src.get("Eligibility_Flag")) else str(src.get("Eligibility_Flag"))
        row["ownership"] = ownership
        row["ownershipBasis"] = basis
        specialties = src.get("Specialties (from site filter)")
        row["specialties"] = None if pd.isna(specialties) else str(specialties)

        counts[ownership] = counts.get(ownership, 0) + 1

    TARGET.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"enriched {len(nabh)} NABH rows")
    print("\nownership (inferred):")
    for label, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>5}  {label}")
    frame = pd.DataFrame([
        {"type": r["facilityType"], "elig": r["corridorEligibility"]} for r in nabh
    ])
    print("\ntop facility types:")
    print(frame["type"].value_counts().head(15).to_string())
    print("\ncorridor eligibility:")
    print(frame["elig"].value_counts().to_string())


if __name__ == "__main__":
    main()

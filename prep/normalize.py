"""Prepare cleaned JSON for the 15-city infrastructure map visualizer.

This script intentionally keeps the visualizer scoped to the shortlisted cities.
Flagged infrastructure coordinates from the companion review workbooks are not
reused: if a researched override is not present in COORDINATE_OVERRIDES, the
output coordinate is set to null and marked undefined_flagged.

v2 note: this script no longer computes any derived/composite scores
(match score, normalization, concentration). It outputs raw counts only,
plus a `domain` ("language" | "health") and `subtype` (exact category name)
for every individual infrastructure point. See SUBTYPE_NAMES /
DOMAIN_FOR_SUBTYPE below.

Known data gaps (flag to the user, do not paper over):
- Individual points for "Private capacity" collapse PDOT/SIIC/IISC into one
  subtype ("General Skilling Infrastructure (PDOT/SIIC/IISC)") because the
  source sheet section-scanner does not retain which of the three a given
  row came from once merged. City/state totals still keep pdot_siics and
  iiscs as separate raw counts.
- Private German training organisations (`private_training`) and Goethe/TELC
  exam centres (`exam_centres`) have no individually geocoded rows at all in
  the source sheets -- they only exist as city/state aggregate counts. Do not
  fabricate points for them.
- No ownership (Govt./Private) field or per-facility capacity parameter
  exists in any source file. Do not invent one; leave it absent and let the
  frontend say so explicitly.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
GEOMAPPED = ROOT / "Geomapped - City mapping sheet.xlsx"
MASTER_CLEAN = ROOT / "_master_clean.csv"
FLAGGED_DUPLICATES = ROOT / "Flagged_Coordinate_Duplicates.xlsx"
FLAGGED_NMC_INC = ROOT / "Flagged_NMC_INC_Coordinates.xlsx"

TOP_CITY_LABELS = {
    "new delhi": "New Delhi",
    "delhi": "New Delhi",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "mumbai": "Mumbai",
    "mumbai city": "Mumbai",
    "pune": "Pune",
    "chennai": "Chennai",
    "hyderabad": "Hyderabad",
    "kolkata": "Kolkata",
    "nagpur": "Nagpur Urban + Rural",
    "nagpur urban": "Nagpur Urban + Rural",
    "nagpur rural": "Nagpur Urban + Rural",
    "coimbatore": "Coimbatore North + South",
    "coimbatore north": "Coimbatore North + South",
    "coimbatore south": "Coimbatore North + South",
    "thiruvananthapuram": "Thiruvananthapuram",
    "trivandrum": "Thiruvananthapuram",
    "thrissur": "Thrissur",
    "ernakulam": "Kochi",
    "kochi": "Kochi",
    "chandigarh": "Chandigarh",
    "kozhikode": "Kozhikode North + South",
    "aluva": "Aluva",
}

COORDINATE_OVERRIDES: dict[str, tuple[float, float, str]] = {
    # Add researched facility-level corrections here as:
    # "normalized institution name": (latitude, longitude, "source URL")
}


def slug(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def minmax(values: list[float]) -> list[float]:
    lo = min(values)
    hi = max(values)
    if math.isclose(lo, hi):
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


SUBTYPE_NAMES = {
    "Goethe/PASCH/Zentrum": "Goethe/PASCH/Zentrum School",
    "HEI German": "HEI Offering German",
    "Private capacity": "General Skilling Infrastructure (PDOT/SIIC/IISC)",
    "Healthcare facility": "NABH Accredited Health Facility",
    "Medical college": "NMC Medical College",
    "Nursing college": "INC Nursing College",
}

DOMAIN_FOR_SUBTYPE = {
    "Goethe/PASCH/Zentrum": "language",
    "HEI German": "language",
    "Private capacity": "language",
    "Healthcare facility": "health",
    "Medical college": "health",
    "Nursing college": "health",
}


def domain_for_source(source_type: str) -> str:
    return DOMAIN_FOR_SUBTYPE.get(source_type, "language")


def subtype_for_source(source_type: str) -> str:
    return SUBTYPE_NAMES.get(source_type, source_type)


def load_city_summary() -> list[dict]:
    rows = json.loads((DATA_DIR / "city-summary.json").read_text(encoding="utf-8"))
    for row in rows:
        row["formal_german_raw"] = row["goethe_schools"] + row["heis_german"] + row["exam_centres"]
        row["general_skilling_raw"] = row["pdot_siics"] + row["iiscs"] + row["private_training"]
        row["language_total"] = row["formal_german_raw"] + row["general_skilling_raw"]
        row["health_total"] = row["nursing_colleges"] + row["medical_colleges"] + row["health_facilities"]
    return rows


def flagged_keys() -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()

    dup = pd.read_excel(FLAGGED_DUPLICATES, sheet_name="Flagged Duplicates")
    for _, row in dup.iterrows():
        keys.add((slug(row.get("Name")), slug(row.get("Latitude")), slug(row.get("Longitude"))))

    for sheet in ["NMC Flagged", "INC Flagged"]:
        df = pd.read_excel(FLAGGED_NMC_INC, sheet_name=sheet)
        for _, row in df.iterrows():
            keys.add((slug(row.get("Institution")), slug(row.get("Latitude")), slug(row.get("Longitude"))))

    return keys


def clean_facility_sheet(sheet_name: str, name_col: str, source_type: str, flags: set[tuple[str, str, str]]) -> list[dict]:
    df = pd.read_excel(GEOMAPPED, sheet_name=sheet_name)
    output: list[dict] = []
    for idx, row in df.iterrows():
        city_label = TOP_CITY_LABELS.get(slug(row.get("City"))) or TOP_CITY_LABELS.get(slug(row.get("District")))
        if not city_label:
            continue

        name = row.get(name_col)
        name_key = slug(name)
        lat = row.get("Latitude")
        lon = row.get("Longitude")
        key = (name_key, slug(lat), slug(lon))
        is_flagged = key in flags
        override = COORDINATE_OVERRIDES.get(name_key)

        coordinate_status = "source"
        source_url = None
        if is_flagged and override:
            lat, lon, source_url = override
            coordinate_status = "researched_override"
        elif is_flagged:
            lat, lon = None, None
            coordinate_status = "undefined_flagged"

        output.append(
            {
                "name": None if pd.isna(name) else str(name),
                "domain": domain_for_source(source_type),
                "subtype": subtype_for_source(source_type),
                "city": city_label,
                "state": None if pd.isna(row.get("State")) else str(row.get("State")),
                "district": None if pd.isna(row.get("District")) else str(row.get("District")),
                "latitude": None if pd.isna(lat) else float(lat),
                "longitude": None if pd.isna(lon) else float(lon),
                "coordinateStatus": coordinate_status,
            }
        )
    return output


def clean_german_city_mapping(flags: set[tuple[str, str, str]]) -> list[dict]:
    df = pd.read_excel(GEOMAPPED, sheet_name="City Mapping", header=None)
    output: list[dict] = []
    section = "Goethe/PASCH/Zentrum"
    for idx, row in df.iterrows():
        first = slug(row.get(0))
        if first in {
            "goethe institut and goethe zentrum",
            "no of pasch schools",
        }:
            section = "Goethe/PASCH/Zentrum"
            continue
        if first == "hei institutes":
            section = "HEI German"
            continue
        if first == "pdot centers":
            section = "Private capacity"
            continue
        if first == "siic centers":
            section = "Private capacity"
            continue
        if first == "iisc centers":
            section = "Private capacity"
            continue

        if section == "Private capacity" and not pd.isna(row.get(8)):
            name = row.get(1)
            state = row.get(2)
            city = row.get(3)
            district = row.get(4)
            lat = row.get(7)
            lon = row.get(8)
        elif section == "Private capacity" and not pd.isna(row.get(7)):
            name = row.get(1)
            state = row.get(5) if not pd.isna(row.get(5)) and first not in {"sn"} else row.get(2)
            city = row.get(3)
            district = row.get(4)
            lat = row.get(6) if first not in {"sn"} else row.get(5)
            lon = row.get(7) if first not in {"sn"} else row.get(6)
        elif first in {"goethe institut", "goethe zentrum"}:
            name = row.get(1)
            state = row.get(2)
            city = row.get(3)
            district = row.get(4)
            lat = row.get(5)
            lon = row.get(6)
            section = "Goethe/PASCH/Zentrum"
        else:
            name = row.get(2) if section == "Goethe/PASCH/Zentrum" else row.get(1)
            state = row.get(0) if section == "Goethe/PASCH/Zentrum" else row.get(2)
            city = row.get(3)
            district = row.get(4)
            lat = row.get(5)
            lon = row.get(6)

        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue

        city_label = TOP_CITY_LABELS.get(slug(city)) or TOP_CITY_LABELS.get(slug(district))
        if not city_label:
            continue

        normalized_type = section
        name_key = slug(name)
        key = (name_key, slug(lat), slug(lon))
        is_flagged = key in flags
        override = COORDINATE_OVERRIDES.get(name_key)

        coordinate_status = "source"
        source_url = None
        if is_flagged and override:
            lat, lon, source_url = override
            coordinate_status = "researched_override"
        elif is_flagged:
            lat, lon = None, None
            coordinate_status = "undefined_flagged"

        output.append(
            {
                "name": None if pd.isna(name) else str(name),
                "domain": domain_for_source(normalized_type),
                "subtype": subtype_for_source(normalized_type),
                "city": city_label,
                "state": None if pd.isna(state) else str(state),
                "district": None if pd.isna(district) else str(district),
                "latitude": None if pd.isna(lat) else lat,
                "longitude": None if pd.isna(lon) else lon,
                "coordinateStatus": coordinate_status,
            }
        )
    return output


def clean_healthcare_facilities() -> list[dict]:
    df = pd.read_csv(MASTER_CLEAN)
    output: list[dict] = []
    for idx, row in df.iterrows():
        city_label = TOP_CITY_LABELS.get(slug(row.get("Assigned_City")))
        if not city_label:
            continue
        lat = row.get("Latitude")
        lon = row.get("Longitude")
        has_coord = not pd.isna(lat) and not pd.isna(lon)
        output.append(
            {
                "name": None if pd.isna(row.get("HCO Name")) else str(row.get("HCO Name")),
                "domain": "health",
                "subtype": subtype_for_source("Healthcare facility"),
                "city": city_label,
                "state": None,
                "district": None,
                "latitude": float(lat) if has_coord else None,
                "longitude": float(lon) if has_coord else None,
                "coordinateStatus": "source" if has_coord else "undefined",
            }
        )
    return output


def aggregate_states(cities: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in cities:
        buckets[row["state"]].append(row)

    states = []
    for state, rows in buckets.items():
        totals = {
            "state": state,
            "city_count": len(rows),
            "latitude": sum(r["latitude"] for r in rows) / len(rows),
            "longitude": sum(r["longitude"] for r in rows) / len(rows),
            "tracked_only": True,
        }
        for key in [
            "goethe_schools",
            "heis_german",
            "exam_centres",
            "pdot_siics",
            "iiscs",
            "private_training",
            "health_facilities",
            "medical_colleges",
            "nursing_colleges",
            "grand_total",
            "formal_german_raw",
            "general_skilling_raw",
            "language_total",
            "health_total",
        ]:
            totals[key] = sum(r[key] for r in rows)
        states.append(totals)

    return sorted(states, key=lambda r: r["state"])


def enrich_infrastructure_counts(cities: list[dict], infrastructure: list[dict]) -> None:
    """Attach plain renderable/dropped counts per city -- no derived scores."""
    by_city: dict[str, list[dict]] = defaultdict(list)
    for point in infrastructure:
        by_city[point["city"]].append(point)

    for city in cities:
        scoped = by_city.get(city["city"], [])
        renderable = [
            p
            for p in scoped
            if p.get("latitude") is not None
            and p.get("longitude") is not None
            and p.get("coordinateStatus") != "undefined_flagged"
        ]
        city["infrastructure_renderable_count"] = len(renderable)
        city["infrastructure_dropped_count"] = len(scoped) - len(renderable)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    cities = load_city_summary()
    flags = flagged_keys()
    infrastructure = []
    infrastructure.extend(clean_german_city_mapping(flags))
    infrastructure.extend(clean_healthcare_facilities())
    infrastructure.extend(clean_facility_sheet("Copy of NMC- Medical Colleges", "Institution Name & Address", "Medical college", flags))
    infrastructure.extend(clean_facility_sheet("Copy of INC - Nursing Colleges", "Institution Name & Address", "Nursing college", flags))
    enrich_infrastructure_counts(cities, infrastructure)

    (DATA_DIR / "cities.json").write_text(json.dumps(cities, indent=2), encoding="utf-8")
    (DATA_DIR / "states.json").write_text(json.dumps(aggregate_states(cities), indent=2), encoding="utf-8")
    (DATA_DIR / "infrastructure-cleaned.json").write_text(json.dumps(infrastructure, indent=2), encoding="utf-8")
    print(f"Wrote {len(cities)} cities and {len(infrastructure)} scoped infrastructure rows")
    dropped = sum(1 for p in infrastructure if p["coordinateStatus"] == "undefined_flagged")
    print(f"Flagged scoped rows set to undefined: {dropped}")
    by_subtype = defaultdict(int)
    for p in infrastructure:
        if p["coordinateStatus"] == "undefined_flagged":
            by_subtype[p["subtype"]] += 1
    for subtype, count in sorted(by_subtype.items(), key=lambda kv: -kv[1]):
        print(f"  dropped [{subtype}]: {count}")


if __name__ == "__main__":
    main()

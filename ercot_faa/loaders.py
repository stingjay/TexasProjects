"""Readers for the ERCOT GIS report (.xlsx) and FAA OE/AAA case lists (.csv)."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

import openpyxl
import pandas as pd

GIS_SHEET_PATTERN = re.compile(r"project details\s*-\s*large gen", re.I)

# Our field name -> header text in the GIS report
GIS_COLUMNS = {
    "inr": "INR",
    "name": "Project Name",
    "phase": "GIM Study Phase",
    "entity": "Interconnecting Entity",
    "poi": "POI Location",
    "county": "County",
    "zone": "CDR Reporting Zone",
    "cod": "Projected COD",
    "fuel": "Fuel",
    "tech": "Technology",
    "mw": "Capacity (MW)",
    "fis_req": "FIS Requested",
    "fis_app": "FIS Approved",
    "ia": "IA Signed",
    "energized": "Approved for Energization",
    "sync": "Approved for Synchronization",
}
GIS_REQUIRED = ["inr", "name", "county", "zone", "fuel", "fis_app"]
GIS_DATES = ["cod", "fis_req", "fis_app", "ia", "energized", "sync"]

# Our field name -> header text in the FAA case list
FAA_COLUMNS = {
    "asn": "STUDY (ASN)",
    "prior": "PRIOR ASN",
    "status": "STATUS",
    "entered": "ENTERED DATE",
    "exp": "EXPIRATION DATE",
    "built": "DATE BUILT",
    "lat": "LATITUDE",
    "lon": "LONGITUDE",
    "sname": "STRUCTURE NAME",
    "city": "STRUCTURE CITY",
    "county": "STRUCTURE COUNTY NAME",
    "state": "STRUCTURE STATE",
    "desc": "PROPOSAL DESCRIPTION",
    "loc": "LOCATION DESCRIPTION",
    "stype": "STRUCTURE TYPE",
    "agl": "AGL HEIGHT PROPOSED",
    "rep": "REPRESENTATIVE NAME",
    "sponsor": "SPONSOR NAME",
}


class ImportError_(ValueError):
    """Raised when a file is not the kind of report we expect."""


def _to_date(v):
    """Excel/CSV value -> datetime.date, treating ERCOT's 1/1/1900 placeholder as blank."""
    if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        d = v.date()
    elif isinstance(v, date):
        d = v
    else:
        try:
            d = pd.to_datetime(str(v).strip(), errors="coerce")
        except Exception:
            return None
        if pd.isna(d):
            return None
        d = d.date()
    return None if d.year <= 1900 else d


def read_gis(source) -> pd.DataFrame:
    """Read the 'Project Details - Large Gen' tab of an ERCOT GIS report.

    `source` is a path or a file-like object (e.g. a Streamlit upload).
    """
    wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    sheet = next((n for n in wb.sheetnames if GIS_SHEET_PATTERN.search(n)), None)
    if sheet is None:
        raise ImportError_(
            'Could not find the "Project Details - Large Gen" sheet. '
            "Use the monthly GIS report workbook from ERCOT."
        )
    rows = list(wb[sheet].iter_rows(values_only=True))
    hdr_i = next((i for i, r in enumerate(rows) if r and str(r[0] or "").strip() == "INR"), None)
    if hdr_i is None:
        raise ImportError_("Found the Large Gen sheet but not its INR header row.")
    header = [str(h or "").strip().lower() for h in rows[hdr_i]]
    idx = {k: (header.index(v.lower()) if v.lower() in header else -1) for k, v in GIS_COLUMNS.items()}
    missing = [GIS_COLUMNS[k] for k in GIS_REQUIRED if idx[k] < 0]
    if missing:
        raise ImportError_("GIS report is missing columns: " + ", ".join(missing))

    recs = []
    for r in rows[hdr_i + 1:]:
        if not r:
            continue
        inr = str(r[0] or "").strip()
        if not re.match(r"^\d{2}INR\d{4}", inr, re.I):
            continue
        rec = {k: (r[i] if i >= 0 and i < len(r) else None) for k, i in idx.items()}
        rec["inr"] = inr
        recs.append(rec)
    if not recs:
        raise ImportError_("No project rows found under the INR header.")

    df = pd.DataFrame(recs)
    for c in ["name", "entity", "poi", "county", "zone", "fuel", "tech", "phase"]:
        df[c] = df[c].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    df["mw"] = pd.to_numeric(df["mw"], errors="coerce").fillna(0.0)
    for c in GIS_DATES:
        df[c] = df[c].map(_to_date)
    return df


def _read_faa_one(raw: bytes) -> pd.DataFrame:
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [h.strip().upper() for h in next(reader)]
    except StopIteration:
        return pd.DataFrame(columns=list(FAA_COLUMNS))
    n = len(header)
    if "STUDY (ASN)" not in header or "LATITUDE" not in header:
        raise ImportError_(
            "This CSV does not look like an FAA OE/AAA case list (no STUDY (ASN) / LATITUDE columns)."
        )
    rows = []
    for r in reader:
        if not r:
            continue
        # A few rows carry unquoted commas in the last (frequency) column; fold them back in.
        if len(r) > n:
            r = r[: n - 1] + [",".join(r[n - 1:])]
        elif len(r) < n:
            r = r + [""] * (n - len(r))
        rows.append(r)
    df = pd.DataFrame(rows, columns=header)
    out = pd.DataFrame({k: df[v] if v in df.columns else "" for k, v in FAA_COLUMNS.items()})
    return out


def read_faa(sources) -> pd.DataFrame:
    """Read one or more FAA WTW case-list CSVs; keep Texas; later files win on duplicate ASNs."""
    frames = []
    for s in sources:
        if hasattr(s, "read"):
            raw = s.read()
            if hasattr(s, "seek"):
                s.seek(0)
        else:
            with open(s, "rb") as fh:
                raw = fh.read()
        frames.append(_read_faa_one(raw))
    if not frames:
        return pd.DataFrame(columns=list(FAA_COLUMNS))
    df = pd.concat(frames, ignore_index=True)
    for c in df.columns:
        df[c] = df[c].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    df = df[df["state"] == "TX"].copy()
    df = df.drop_duplicates("asn", keep="last")
    for c in ["lat", "lon", "agl"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["entered", "exp"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["desc"] = df["desc"].str.slice(0, 200)
    df["loc"] = df["loc"].str.slice(0, 200)
    return df.reset_index(drop=True)

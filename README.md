# TexasProjects – ERCOT Interconnection & FAA Map

A local Streamlit app that joins the ERCOT **Generator Interconnection Status (GIS)** report to **FAA OE/AAA wind-turbine (WTW) filings**. You can filter and map projects by FIS status and FAA status. It answers questions like *"which ERCOT North projects have an approved FIS and FAA Determinations of No Hazard?"*

Everything runs on your machine. The only thing that needs internet is the optional street basemap, which you can turn off.

## Quick start (Windows)

Double-click **`run_app.bat`**. The first run creates a `.venv` and installs the packages. After that it opens the app in your browser at http://localhost:8501.

Or start it by hand:

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Requires Python 3.10 or newer.

## Updating the data

| Data | Where to get it | Where it goes |
| --- | --- | --- |
| ERCOT GIS report (`GIS_Report_<Month><Year>.xlsx`) | ERCOT → Grid Info → Resource Adequacy → *Monthly Generator Interconnection Status Report* | `data/gis/` (the newest file by date modified is used) |
| FAA WTW case lists (`Part77WTW<Year>List.csv`) | FAA OE/AAA → Download Archives → Off Airport, WTW cases by year | `data/faa/` (every CSV is loaded; duplicate ASNs keep the last one) |

You can also upload files from the sidebar. Tick **Save uploads to the data folder** to keep them for next time.

Only Texas FAA cases are kept. The app reads the **Project Details – Large Gen** tab of the GIS report.

## How the matching works

The GIS report has no coordinates, so FAA cases are linked to projects in three ways:

1. **User links.** These are your own *Link / Unlink* clicks in the project detail panel. They are saved to `data/user_links.json`; commit it to keep them.
2. **Reviewed links.** These are ASN lists checked by hand for the ERCOT North wind screen (Nov 2026), stored in `data/reviewed_links.json`.
3. **Auto links.** FAA cases are grouped into filings (same sponsor + same description). Each filing is matched to a **wind** project in the same county by distinctive name words, such as "Priddy" or "Bluebonnet".

A project with no linked filings is placed at its county centroid (faint marker).

### Definitions

- **FIS approved:** the GIS report has an *FIS Approved* date.
- **DNE in effect:** a *Determined – No Hazard* case that has not expired, has no expiration listed, or has a *Date Built* on file.
- **Status:**
  - *FIS + FAA*: FIS approved and at least one DNE.
  - *FAA, FIS pending*: at least one DNE, but no FIS approval yet.
  - *FIS, no FAA*: FIS approved, no DNEs.
  - *Neither*: no FIS approval and no DNEs.
- **Stage:** *Energized* if the GIS report has an *Approved for Energization* date.

FAA expiration dates are as of the export. Confirm extensions in OE/AAA.

## Files

```
app.py                     Streamlit UI
ercot_faa/loaders.py       GIS .xlsx and FAA .csv readers
ercot_faa/matching.py      clustering, linking and status logic
data/gis/                  GIS report(s)
data/faa/                  FAA WTW case lists
data/tx_counties.json      Texas county outlines (from us-atlas, US Census)
data/reviewed_links.json   hand-checked project ↔ ASN links
data/user_links.json       your Link/Unlink edits (created on first edit)
run_app.bat                Windows launcher
```

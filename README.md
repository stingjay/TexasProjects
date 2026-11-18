# TexasProjects – ERCOT Interconnection & FAA Map

A local Streamlit app that joins the ERCOT **Generator Interconnection Status (GIS)** report to **FAA OE/AAA wind-turbine (WTW) filings**. You can filter and map projects by FIS status and FAA status. It answers questions like *"which ERCOT North projects have an approved FIS and FAA Determinations of No Hazard?"*

Everything runs on your machine. The only thing that needs internet is the optional street basemap, which you can turn off.

## Get the code

```bash
git clone https://github.com/stingjay/TexasProjects.git
cd TexasProjects
```

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

### Monthly update checklist

1. Download the new GIS report and save it to `data/gis/`. Older reports can stay; the newest one is used.
2. Download the current year's FAA WTW case list. Replace that year's CSV in `data/faa/`, because the FAA re-exports the whole year.
3. Run the app. Click **North wind screen** and check that the counts look right.
4. Review any new or changed projects in the detail panel, and Link or Unlink filings as needed.
5. Commit the new data and `data/user_links.json`:

   ```bash
   git add data
   git commit -m "Data update: <Month Year> GIS report, FAA lists through <date>"
   git push
   ```

## How the matching works

The GIS report has no coordinates, so FAA cases are linked to projects in three ways:

1. **User links.** These are your own *Link / Unlink* clicks in the project detail panel. They are saved to `data/user_links.json`; commit it to keep them.
2. **Reviewed links.** These are ASN lists checked by hand for the ERCOT North wind screen, stored in `data/reviewed_links.json`.
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

## Example: ERCOT North projects with an FIS and FAA permits

1. Click **North wind screen**. This sets CDR zone = NORTH and Fuel = Wind.
2. Set **Status** to *FIS + FAA* for projects with an approved FIS and FAA Determinations of No Hazard. Add *FAA, FIS pending* to include projects that have DNEs but no FIS approval yet.
3. Download the Excel file to get the projects plus every linked FAA case.

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
requirements.txt           Python packages (streamlit, pandas, openpyxl, pydeck)
.streamlit/config.toml     theme and server settings
```

## Notes on the repo

- The data files add about 22 MB. To keep data out of git, add `data/faa/` and `data/gis/` to `.gitignore` and keep the files locally.
- FAA CSVs for a full year can pass 5 MB. Each file stays well under GitHub's 100 MB limit, so Git LFS isn't needed.
- `.venv/` and `__pycache__/` are ignored. Each machine builds its own environment from `requirements.txt`.

## Data sources

- ERCOT, *Generator Interconnection Status Report* (public, monthly).
- FAA Obstruction Evaluation / Airport Airspace Analysis (OE/AAA), WTW case archives (public).
- Texas county outlines: [us-atlas](https://github.com/topojson/us-atlas), derived from US Census cartographic boundary files.

"""ERCOT Interconnection & FAA Map — Streamlit app.

Run:  streamlit run app.py
Data: the newest .xlsx in data/gis/ and every .csv in data/faa/ load automatically.
      You can also upload newer files from the sidebar.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

from ercot_faa.loaders import ImportError_, read_faa, read_gis
from ercot_faa.matching import (
    STATUS_LABEL,
    STATUS_ORDER,
    Links,
    county_centroids_from_geojson,
    match,
)

ROOT = Path(__file__).parent
DATA = ROOT / "data"
GIS_DIR, FAA_DIR = DATA / "gis", DATA / "faa"
REVIEWED = DATA / "reviewed_links.json"
USER_LINKS = DATA / "user_links.json"
COUNTIES = DATA / "tx_counties.json"

STATUS_RGB = {"both": [46, 125, 79], "faa": [183, 121, 31], "fis": [59, 106, 160], "none": [138, 145, 141]}
CASE_RGB = {"eff": [46, 125, 79], "exp": [150, 155, 152], "pend": [214, 140, 30]}
FUEL_NAMES = {"WIN": "Wind", "SOL": "Solar", "OTH": "Storage", "GAS": "Gas", "NUC": "Nuclear", "HYD": "Hydro",
              "OIL": "Oil", "WAT": "Water", "MWH": "Other (MWH)"}

st.set_page_config(page_title="ERCOT Interconnection & FAA Map", page_icon="🗺️", layout="wide")


# ---------------------------------------------------------------- loading
def _sig(paths):
    return "|".join(f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in paths)


@st.cache_data(show_spinner="Reading GIS report…")
def load_gis_bytes(raw: bytes, _name: str) -> pd.DataFrame:
    return read_gis(io.BytesIO(raw))


@st.cache_data(show_spinner="Reading FAA case lists…")
def load_faa_bytes(raws: tuple[bytes, ...], _names: tuple[str, ...]) -> pd.DataFrame:
    return read_faa([io.BytesIO(r) for r in raws])


@st.cache_data
def load_counties():
    gj = json.loads(COUNTIES.read_text())
    return gj, county_centroids_from_geojson(gj)


@st.cache_data(show_spinner="Linking FAA filings to projects…")
def run_match(_gis, _faa, gis_key: str, faa_key: str, links_key: str, today: str):
    gj, cents = load_counties()
    links = Links.load(REVIEWED, USER_LINKS)
    return match(_gis, _faa, links, cents, today)


def newest_gis_file():
    files = sorted(GIS_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


# ---------------------------------------------------------------- sidebar: data
st.sidebar.header("Data")
up_gis = st.sidebar.file_uploader("GIS report (.xlsx)", type=["xlsx"], help="ERCOT monthly Generator Interconnection Status report")
up_faa = st.sidebar.file_uploader(
    "FAA case lists (.csv)", type=["csv"], accept_multiple_files=True,
    help="OE/AAA WTW case lists. Upload every year you want; uploads replace the FAA folder data.",
)
save_uploads = st.sidebar.checkbox("Save uploads to the data folder", value=False,
                                   help="Copies uploaded files into data/gis and data/faa so they load next time.")

try:
    if up_gis is not None:
        gis_raw, gis_name = up_gis.getvalue(), up_gis.name
        if save_uploads:
            (GIS_DIR / up_gis.name).write_bytes(gis_raw)
    else:
        gf = newest_gis_file()
        if gf is None:
            st.info("Put an ERCOT GIS report (.xlsx) in data/gis/ or upload one in the sidebar.")
            st.stop()
        gis_raw, gis_name = gf.read_bytes(), gf.name
    gis = load_gis_bytes(gis_raw, gis_name)

    if up_faa:
        faa_raws = tuple(f.getvalue() for f in up_faa)
        faa_names = tuple(f.name for f in up_faa)
        if save_uploads:
            for f in up_faa:
                (FAA_DIR / f.name).write_bytes(f.getvalue())
    else:
        fps = sorted(FAA_DIR.glob("*.csv"))
        faa_raws = tuple(p.read_bytes() for p in fps)
        faa_names = tuple(p.name for p in fps)
    faa = load_faa_bytes(faa_raws, faa_names)
except ImportError_ as e:
    st.error(str(e))
    st.stop()

gis_key = hashlib.md5(gis_raw).hexdigest()
faa_key = hashlib.md5(b"".join(faa_raws)).hexdigest()
links = Links.load(REVIEWED, USER_LINKS)
today = date.today()
projects, cases, clusters = run_match(gis, faa, gis_key, faa_key, links.fingerprint(), today.isoformat())

st.sidebar.caption(
    f"**GIS:** {gis_name} · {len(gis):,} projects  \n"
    f"**FAA:** {len(faa_names)} file(s) · {len(faa):,} Texas cases  \n"
    f"DNE status as of {today:%m/%d/%Y}"
)

# ---------------------------------------------------------------- sidebar: filters
# ---- DEFAULT VIEW ----------------------------------------------------------
# Edit this to change what the app shows when it opens (and what "Reset filters" returns to).
#   zones:  CDR zones, e.g. ["NORTH"], ["NORTH", "WEST"], or [] for all zones
#   fuel:   GIS fuel codes, e.g. ["WIN"], ["WIN", "SOL", "OTH"], or [] for all fuels
#           WIN = wind, SOL = solar, OTH = storage, GAS = gas, NUC = nuclear, HYD = hydro
#   fis:    "Any", "Approved", "Not yet approved"
#   faa:    "Any", "Has DNEs", "DNEs in effect", "Pending only", "None found"
#   stage:  "Any", "In development", "Energized"
#   status: "Any", "FIS + FAA", "FAA, FIS pending", "FIS, no FAA", "Neither"
#   q:      search text, "" for none
DEFAULTS = dict(zones=["NORTH"], fuel=["WIN"], fis="Any", faa="Any", stage="Any", status="Any", q="")
# ----------------------------------------------------------------------------
for k, val in DEFAULTS.items():
    st.session_state.setdefault(f"f_{k}", list(val) if isinstance(val, list) else val)
if not isinstance(st.session_state["f_fuel"], list):  # carried over from the older single-select
    st.session_state["f_fuel"] = list(DEFAULTS["fuel"])

st.sidebar.header("Filters")
if st.sidebar.button("Reset filters", type="primary", width="stretch"):
    for k, val in DEFAULTS.items():
        st.session_state[f"f_{k}"] = list(val) if isinstance(val, list) else val
    st.session_state.pop("sel", None)

zones = sorted(z for z in projects["zone"].unique() if z)
fuels = sorted(f for f in projects["fuel"].unique() if f)
st.session_state["f_zones"] = [z for z in st.session_state["f_zones"] if z in zones]
f_zones = st.sidebar.multiselect("CDR zone", zones, key="f_zones")
st.session_state["f_fuel"] = [f for f in st.session_state["f_fuel"] if f in fuels]
f_fuel = st.sidebar.multiselect("Fuel", fuels, key="f_fuel", format_func=lambda x: FUEL_NAMES.get(x, x),
                                help="Pick one or more. Leave empty to show every fuel.")
f_fis = st.sidebar.selectbox("FIS", ["Any", "Approved", "Not yet approved"], key="f_fis")
f_faa = st.sidebar.selectbox("FAA", ["Any", "Has DNEs", "DNEs in effect", "Pending only", "None found"], key="f_faa")
f_stage = st.sidebar.selectbox("Stage", ["Any", "In development", "Energized"], key="f_stage")
f_status = st.sidebar.selectbox("Status", ["Any"] + list(STATUS_LABEL.values()), key="f_status")
f_q = st.sidebar.text_input("Search", key="f_q", placeholder="Project, INR, developer, county")

v = projects
if f_zones:
    v = v[v["zone"].isin(f_zones)]
if f_fuel:
    v = v[v["fuel"].isin(f_fuel)]
if f_fis != "Any":
    v = v[v["fis_ok"] == (f_fis == "Approved")]
if f_faa == "Has DNEs":
    v = v[v["dne"] > 0]
elif f_faa == "DNEs in effect":
    v = v[v["in_effect"] > 0]
elif f_faa == "Pending only":
    v = v[(v["dne"] == 0) & (v["pending"] > 0)]
elif f_faa == "None found":
    v = v[v["faa_cases"] == 0]
if f_stage != "Any":
    v = v[v["stage"] == f_stage]
if f_status != "Any":
    v = v[v["status_label"] == f_status]
if f_q:
    hay = (v["inr"] + " " + v["name"] + " " + v["entity"] + " " + v["county"] + " " + v["poi"]).str.lower()
    v = v[hay.str.contains(f_q.lower(), regex=False)]
v = v.assign(_o=v["status"].map(STATUS_ORDER)).sort_values(["_o", "mw"], ascending=[True, False]).drop(columns="_o")

# ---------------------------------------------------------------- header + tiles
st.title("ERCOT Interconnection & FAA Map")
st.caption("ERCOT GIS report joined to FAA OE/AAA wind filings. Filter in the sidebar, click a table row for details.")


def mw(df):
    return f"{df['mw'].sum():,.0f} MW"


cols = st.columns(5)
both = v[v["status"] == "both"]
tiles = [("Projects shown", v, mw(v)),
         ("FIS + FAA DNEs", both, f"{mw(both)} · {(both['stage'] == 'In development').sum()} in development")]
tiles += [(STATUS_LABEL[s], v[v["status"] == s], mw(v[v["status"] == s])) for s in ["faa", "fis", "none"]]
for c, (label, d, sub) in zip(cols, tiles):
    with c.container(border=True):
        st.metric(label, f"{len(d):,}")
        st.caption(sub)

# ---------------------------------------------------------------- map
sel_inr = st.session_state.get("sel")
gj, cents = load_counties()
county_zone = (
    projects.explode("counties").groupby("counties")["zone"].agg(lambda s: s.value_counts().index[0]).to_dict()
)
for ft in gj["features"]:
    z = county_zone.get(ft["properties"]["name"])
    ft["properties"]["fill"] = [31, 111, 107, 40] if z in (f_zones or []) else [200, 205, 199, 25]
    ft["properties"].update(t1=f"{ft['properties']['name']} County", t2=f"CDR zone: {z or '—'}", t3="", t4="")

mcol, lcol = st.columns([4, 1])
layer_mode = lcol.radio("FAA filings on map", ["Linked to shown", "All filings", "Off"], index=0)
basemap = lcol.toggle("Street basemap", value=True, help="Needs an internet connection. Turn off to work fully offline with county outlines only.")
show_proj_labels = lcol.toggle("Project names", value=True)
show_county_labels = lcol.toggle("County names", value=False)
lcol.markdown(
    "**Projects** (size = MW)  \n"
    + "  \n".join(
        f"<span style='color:rgb({','.join(map(str, STATUS_RGB[s]))})'>●</span> {STATUS_LABEL[s]}" for s in STATUS_RGB
    )
    + "  \nFaint = placed at county centroid  \n\n**FAA filings**  \n"
    + "  \n".join(
        f"<span style='color:rgb({','.join(map(str, CASE_RGB[s]))})'>■</span> {t}"
        for s, t in [("eff", "DNE in effect"), ("exp", "DNE expired"), ("pend", "Pending")]
    ),
    unsafe_allow_html=True,
)

layers = [
    pdk.Layer("GeoJsonLayer", gj, stroked=True, filled=True, get_fill_color="properties.fill",
              get_line_color=[150, 156, 150, 160], line_width_min_pixels=0.6, pickable=True)
]
if layer_mode != "Off":
    cc = cases[cases["lat"].notna()]
    if layer_mode == "Linked to shown":
        cc = cc[cc["owner"].isin(set(v["inr"]))]
    cc = cc.assign(
        color=cc["state"].map(CASE_RGB),
        t1=cc["asn"],
        t2=cc["status"],
        t3=cc["sponsor"].where(cc["sponsor"] != "", "(no sponsor shown)"),
        t4=cc["sname"] + " · " + cc["agl"].fillna(0).astype(int).astype(str) + " ft AGL",
    )[["lon", "lat", "color", "t1", "t2", "t3", "t4"]]
    layers.append(pdk.Layer("ScatterplotLayer", cc, get_position=["lon", "lat"], get_fill_color="color",
                            get_radius=180, radius_min_pixels=1.5, radius_max_pixels=5, opacity=0.75, pickable=True))
pv = v[v["lat"].notna()].copy()
pv["color"] = [STATUS_RGB[s] + [90 if a else 220] for s, a in zip(pv["status"], pv["approx"])]
pv["radius"] = pv["mw"].clip(lower=1).map(math.sqrt) * 260 + 600
pv["t1"] = pv["name"]
pv["t2"] = pv["inr"] + " · " + pv["mw"].round(1).astype(str) + " MW · " + pv["county"]
pv["t3"] = pv["status_label"] + " · " + pv["stage"]
pv["t4"] = "DNEs: " + pv["dne"].astype(str) + " (" + pv["in_effect"].astype(str) + " in effect, " + pv["pending"].astype(str) + " pending)"
TIP = ["t1", "t2", "t3", "t4"]
layers.append(pdk.Layer("ScatterplotLayer", pv[["lon", "lat", "color", "radius"] + TIP],
                        get_position=["lon", "lat"], get_fill_color="color", get_radius="radius",
                        get_line_color=[255, 255, 255, 200], line_width_min_pixels=1,
                        stroked=True, radius_min_pixels=4, pickable=True))
if sel_inr in set(pv["inr"]):
    layers.append(pdk.Layer("ScatterplotLayer", pv[pv["inr"] == sel_inr][["lon", "lat", "radius"] + TIP],
                            get_position=["lon", "lat"], get_radius="radius", filled=False, stroked=True,
                            get_line_color=[20, 20, 20, 255], line_width_min_pixels=3, radius_min_pixels=6))

if show_county_labels:
    cl = pd.DataFrame([{"name": n, "lon": c[0], "lat": c[1]} for n, c in cents.items()])
    layers.append(pdk.Layer("TextLayer", cl, get_position=["lon", "lat"], get_text="name", get_size=11,
                            get_color=[95, 106, 102, 200], get_alignment_baseline="'center'"))
if show_proj_labels and not pv.empty:
    labels = pv[(pv["mw"] >= 20) | (pv["inr"] == sel_inr)] if len(pv) > 150 else pv
    layers.append(pdk.Layer("TextLayer", labels[["lon", "lat", "name"]], get_position=["lon", "lat"], get_text="name",
                            get_size=13, get_color=[29, 35, 33, 255], get_text_anchor="'start'",
                            get_alignment_baseline="'center'", get_pixel_offset=[12, 0]))


def view_for(df):
    if sel_inr is not None and sel_inr in set(df["inr"]):
        r = df[df["inr"] == sel_inr].iloc[0]
        return pdk.ViewState(latitude=r["lat"], longitude=r["lon"], zoom=9)
    if df.empty:
        return pdk.ViewState(latitude=31.2, longitude=-99.3, zoom=5)
    lat0, lat1, lon0, lon1 = df["lat"].min(), df["lat"].max(), df["lon"].min(), df["lon"].max()
    span = max(lat1 - lat0, (lon1 - lon0) * 0.8, 0.3)
    return pdk.ViewState(latitude=(lat0 + lat1) / 2, longitude=(lon0 + lon1) / 2,
                         zoom=max(4.5, min(10, math.log2(360 / span) - 0.6)))


with mcol:
    st.pydeck_chart(
        pdk.Deck(layers=layers, initial_view_state=view_for(pv),
                 map_provider="carto" if basemap else None, map_style="light" if basemap else None,
                 tooltip={"html": "<b>{t1}</b><br/>{t2}<br/>{t3}<br/>{t4}",
                          "style": {"fontSize": "12px", "backgroundColor": "#1d2321", "color": "#f4f5f2",
                                    "padding": "6px 8px", "borderRadius": "4px"}}),
        height=560,
    )

# ---------------------------------------------------------------- table
st.subheader(f"Projects ({len(v)})")
show = v[["status_label", "inr", "name", "entity", "county", "zone", "fuel", "mw", "fis_app", "ia", "cod", "stage",
          "faa_link", "dne", "in_effect", "pending", "last_dne_exp", "max_agl"]].rename(columns={
    "status_label": "Status", "inr": "INR", "name": "Project", "entity": "Interconnecting entity", "county": "County",
    "zone": "Zone", "fuel": "Fuel", "mw": "MW", "fis_app": "FIS approved", "ia": "IA signed", "cod": "Projected COD",
    "stage": "Stage", "faa_link": "FAA link", "dne": "DNEs", "in_effect": "In effect", "pending": "Pending",
    "last_dne_exp": "Latest DNE exp.", "max_agl": "Max AGL ft"})
event = st.dataframe(
    show, hide_index=True, width="stretch", height=420, on_select="rerun", selection_mode="single-row",
    column_config={
        "MW": st.column_config.NumberColumn(format="%.1f"),
        "FIS approved": st.column_config.DateColumn(format="M/D/YYYY"),
        "IA signed": st.column_config.DateColumn(format="M/D/YYYY"),
        "Projected COD": st.column_config.DateColumn(format="M/D/YYYY"),
        "Latest DNE exp.": st.column_config.DateColumn(format="M/D/YYYY"),
        "Max AGL ft": st.column_config.NumberColumn(format="%d"),
    },
    key="tbl",
)
if event.selection.rows:
    new_sel = show.iloc[event.selection.rows[0]]["INR"]
    if new_sel != sel_inr:
        st.session_state["sel"] = new_sel
        st.rerun()


def export_xlsx(df: pd.DataFrame) -> bytes:
    linked = cases[cases["owner"].isin(set(df["inr"]))][
        ["owner", "asn", "status", "state", "sname", "county", "lat", "lon", "agl", "entered", "exp", "built", "sponsor", "rep", "desc"]
    ].rename(columns={"owner": "INR", "state": "in_effect_state"})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        show.to_excel(w, sheet_name="Projects", index=False)
        linked.to_excel(w, sheet_name="Linked FAA cases", index=False)
    return buf.getvalue()


d1, d2, _ = st.columns([1, 1, 4])
d1.download_button("Download CSV", show.to_csv(index=False).encode(), "ercot_faa_projects.csv", "text/csv")
d2.download_button("Download Excel", export_xlsx(v), "ercot_faa_projects.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------------------------------------------------------- detail
st.divider()
if not sel_inr or sel_inr not in set(projects["inr"]):
    st.info("Select a row in the table to see the project's milestones and the FAA filings linked to it.")
    st.stop()

p = projects[projects["inr"] == sel_inr].iloc[0]
def fmt(d):
    if d is None or (not isinstance(d, date) and pd.isna(d)):
        return "—"
    d = pd.Timestamp(d)
    return f"{d.month}/{d.day}/{d.year}"


agl_txt = "—" if pd.isna(p["max_agl"]) else f"{p['max_agl']:,.0f} ft"
st.subheader(f"{p['name']}  ·  {p['inr']}")
st.caption(f"{p['entity']} · {p['status_label']} · {p['stage']}")
a, b, c = st.columns(3)
a.markdown(f"**County / zone:** {p['county']} · {p['zone']}  \n**Fuel / MW:** {p['fuel']} {p['tech']} · {p['mw']:,.1f} MW  \n**POI:** {p['poi']}  \n**Study phase:** {p['phase']}")
b.markdown(f"**FIS requested:** {fmt(p['fis_req'])}  \n**FIS approved:** {fmt(p['fis_app'])}  \n**IA signed:** {fmt(p['ia'])}  \n**Energized:** {fmt(p['energized'])}  \n**Projected COD:** {fmt(p['cod'])}")
c.markdown(
    f"**FAA cases:** {p['faa_cases']} ({p['dne']} DNEs, {p['in_effect']} in effect, {p['built']} built, {p['pending']} pending)  \n"
    f"**Latest DNE exp.:** {fmt(p['last_dne_exp'])}  \n**Max AGL:** {agl_txt}  \n"
    f"**Map location:** {'County centroid (no linked filings)' if p['approx'] else 'Average of linked FAA filings'}"
)


def cluster_box(k, linked: bool):
    with st.container(border=True):
        st.markdown(f"**{k['sponsor']}**  \n{k['desc']}")
        st.caption(
            f"{k['n']} cases · {', '.join(k['counties'])} · {k['years']} · "
            f"{k['eff']} in effect · {k['exp']} expired · {k['pend']} pending"
            + (f" · latest exp. {fmt(k['last_exp'])}" if pd.notna(k["last_exp"]) else "")
            + (f" · {k['how']} link" if linked and k["how"] else "")
            + (f" · now linked to {k['owner']}" if not linked and k["owner"] else "")
        )
        if st.button("Unlink" if linked else "Link to this project", key=("u" if linked else "l") + k["cluster"]):
            lk = Links.load(REVIEWED, USER_LINKS)
            (lk.unlink if linked else lk.link)(sel_inr, k["cluster"])
            lk.save_user(USER_LINKS)
            st.rerun()


left, right = st.columns(2)
with left:
    mine = clusters[clusters["owner"] == sel_inr].sort_values("n", ascending=False)
    st.markdown(f"#### Linked FAA filings ({len(mine)})")
    if mine.empty:
        st.caption("None linked.")
    for _, k in mine.iterrows():
        cluster_box(k, True)
with right:
    near = clusters[(clusters["owner"] != sel_inr) & clusters["counties"].map(lambda cs: any(x in p["counties"] for x in cs))]
    near = near.sort_values("n", ascending=False).head(10)
    st.markdown(f"#### Other filings in {', '.join(p['counties'])}")
    if near.empty:
        st.caption("No other FAA filings in this county.")
    for _, k in near.iterrows():
        cluster_box(k, False)
st.caption("Your Link/Unlink choices are saved to data/user_links.json, so you can commit them with the repo.")

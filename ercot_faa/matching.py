"""Link FAA filings to ERCOT GIS projects and score each project's FIS / FAA status.

How links are made, in priority order:
  1. User links      - your own Link/Unlink choices in the app (data/user_links.json)
  2. Reviewed links  - ASN lists checked by hand (data/reviewed_links.json)
  3. Auto links      - name matching between a wind project's name/entity and a filing
                       cluster's sponsor/description, within the project's county

FAA cases are first grouped into "clusters" (same sponsor + same filing description),
so a whole turbine array links to a project in one step.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

DNE = "Determined - No Hazard"

STOP = set(
    """wind windpower project projects llc l.l.c lp l.p inc energy energies farm farms power renewable
    renewables development developments dev the of and for a an solar storage bess ess repower repowering
    uprate slf phase i ii iii iv v b c co company corp corporation partners holdings generation generating
    station center texas tx north south east west new array met tower towers turbine turbines filing proposed
    located near county counties structure structures additional locations previously determined filed
    associated with on in at file wtw oe asn re refiling relocated replacement greenfield permanent
    temporary rural agricultural pasture land ranch ranchland existing""".split()
)

STATUS_LABEL = {
    "both": "FIS + FAA",
    "faa": "FAA, FIS pending",
    "fis": "FIS, no FAA",
    "none": "Neither",
}
STATUS_ORDER = {"both": 0, "faa": 1, "fis": 2, "none": 3}


def tokens(text: str) -> list[str]:
    words = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split()
    return [w for w in words if len(w) > 2 and w not in STOP and not w.isdigit()]


def case_state(status: str, exp, built: str, today: pd.Timestamp) -> str:
    """eff = DNE in effect (unexpired, no expiration, or built); exp = DNE expired; pend = not determined."""
    if status != DNE:
        return "pend"
    if built or pd.isna(exp) or exp >= today:
        return "eff"
    return "exp"


@dataclass
class Links:
    reviewed: dict = field(default_factory=dict)  # inr -> {"conf","note","asns":[...]}
    user_link: dict = field(default_factory=dict)  # inr -> [cluster keys]
    user_unlink: dict = field(default_factory=dict)  # inr -> [cluster keys]

    @classmethod
    def load(cls, reviewed_path: Path, user_path: Path) -> "Links":
        reviewed = json.loads(reviewed_path.read_text()) if reviewed_path.exists() else {}
        user = json.loads(user_path.read_text()) if user_path.exists() else {}
        return cls(reviewed, user.get("link", {}), user.get("unlink", {}))

    def save_user(self, user_path: Path) -> None:
        user_path.write_text(json.dumps({"link": self.user_link, "unlink": self.user_unlink}, indent=2))

    def link(self, inr: str, key: str) -> None:
        for keys in self.user_link.values():
            if key in keys:
                keys.remove(key)
        self.user_link.setdefault(inr, []).append(key)
        self.user_unlink[inr] = [k for k in self.user_unlink.get(inr, []) if k != key]

    def unlink(self, inr: str, key: str) -> None:
        self.user_link[inr] = [k for k in self.user_link.get(inr, []) if k != key]
        if key not in self.user_unlink.setdefault(inr, []):
            self.user_unlink[inr].append(key)

    def fingerprint(self) -> str:
        return json.dumps([self.user_link, self.user_unlink], sort_keys=True)


def build_clusters(faa: pd.DataFrame, county_names: set[str], today: pd.Timestamp) -> pd.DataFrame:
    """Return the FAA cases with a cluster key and state, plus one row per cluster."""
    f = faa.copy()
    f["state"] = [case_state(s, e, b, today) for s, e, b in zip(f["status"], f["exp"], f["built"])]
    desc_key = f["desc"].str.lower().str.replace(r"[^a-z ]", "", regex=True).str.slice(0, 50)
    sponsor_key = f["sponsor"].where(f["sponsor"] != "", "(none)").str.lower()
    county_suffix = ("|" + f["county"]).where(f["sponsor"] == "", "")
    f["cluster"] = sponsor_key + "|" + desc_key + county_suffix
    f["year"] = f["asn"].str.slice(0, 4)

    rows = []
    for key, g in f.groupby("cluster", sort=False):
        first = g.iloc[0]
        toks = set(t for t in tokens(" ".join([first["sponsor"], first["desc"], first["loc"]])) if t not in county_names)
        for nm in g["sname"].head(20):
            toks.update(t for t in tokens(nm) if t not in county_names)
        rows.append(
            {
                "cluster": key,
                "sponsor": first["sponsor"] or "(no sponsor shown)",
                "desc": first["desc"],
                "rep": first["rep"],
                "counties": sorted(set(g["county"])),
                "tokens": toks,
                "n": len(g),
                "eff": int((g["state"] == "eff").sum()),
                "exp": int((g["state"] == "exp").sum()),
                "pend": int((g["state"] == "pend").sum()),
                "years": ", ".join(sorted(set(g["year"]))),
                "last_exp": g.loc[g["status"] == DNE, "exp"].max(),
                "lat": g["lat"].mean(),
                "lon": g["lon"].mean(),
                "asns": list(g["asn"]),
            }
        )
    return f, pd.DataFrame(rows)


def match(gis: pd.DataFrame, faa: pd.DataFrame, links: Links, county_centroids: dict, today=None):
    """Link clusters to projects and compute per-project FAA metrics and status.

    Returns (projects, cases, clusters). `clusters` has `owner` (INR) and `how` columns.
    """
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    county_names = {c.lower() for c in county_centroids}
    cases, clusters = build_clusters(faa, county_names, today)

    p = gis.copy()
    p["counties"] = p["county"].map(lambda s: [x.strip() for x in re.split(r"[/,&]| and ", s) if x.strip()])
    p["toks"] = (p["name"] + " " + p["entity"]).map(lambda s: sorted(set(t for t in tokens(s) if t not in county_names)))
    df = Counter(t for ts in p["toks"] for t in ts)

    asn_to_inr = {a: inr for inr, v in links.reviewed.items() for a in v.get("asns", [])}
    inrs = set(p["inr"])
    wind = p[p["fuel"] == "WIN"]
    wind_by_county = defaultdict(list)
    for r in wind.itertuples():
        for c in r.counties:
            wind_by_county[c].append(r)

    owners, hows = [], []
    for k in clusters.itertuples():
        owner = how = None
        user = next((inr for inr, keys in links.user_link.items() if k.cluster in keys), None)
        if user in inrs:
            owner, how = user, "User"
        else:
            rv = next((asn_to_inr[a] for a in k.asns if a in asn_to_inr), None)
            if rv in inrs and k.cluster not in links.user_unlink.get(rv, []):
                owner, how = rv, "Reviewed"
            else:
                best, best_s = None, 0.0
                seen = set()
                for c in k.counties:
                    for r in wind_by_county.get(c, []):
                        if r.inr in seen or k.cluster in links.user_unlink.get(r.inr, []):
                            continue
                        seen.add(r.inr)
                        s = sum(1 / df[t] for t in r.toks if t in k.tokens)
                        if s > best_s:
                            best, best_s = r.inr, s
                if best and best_s >= 1:
                    owner, how = best, "Auto"
        owners.append(owner)
        hows.append(how)
    clusters["owner"] = owners
    clusters["how"] = hows

    cmap = clusters.set_index("cluster")[["owner", "how"]]
    cases = cases.join(cmap, on="cluster")

    # per-project FAA metrics
    rank = {"User": 3, "Reviewed": 2, "Auto": 1}
    linked = cases[cases["owner"].notna()]
    agg = linked.groupby("owner").agg(
        faa_cases=("asn", "size"),
        dne=("status", lambda s: int((s == DNE).sum())),
        in_effect=("state", lambda s: int((s == "eff").sum())),
        pending=("state", lambda s: int((s == "pend").sum())),
        built=("built", lambda s: int((s != "").sum())),
        max_agl=("agl", "max"),
        lat=("lat", "mean"),
        lon=("lon", "mean"),
    )
    agg["last_dne_exp"] = linked[linked["status"] == DNE].groupby("owner")["exp"].max()
    agg["faa_link"] = linked.groupby("owner")["how"].agg(lambda s: max(s, key=lambda h: rank.get(h, 0)))
    p = p.join(agg, on="inr")
    for c in ["faa_cases", "dne", "in_effect", "pending", "built"]:
        p[c] = p[c].fillna(0).astype(int)
    p["faa_link"] = p["faa_link"].fillna("None")

    # place unlinked projects at their county centroid (jittered so they don't stack)
    p["approx"] = p["lat"].isna()
    for i in p.index[p["approx"]]:
        cen = county_centroids.get(p.at[i, "counties"][0] if p.at[i, "counties"] else "")
        if cen:
            h = sum(ord(ch) * 31 ** j for j, ch in enumerate(p.at[i, "inr"])) % 10007
            p.at[i, "lon"] = cen[0] + ((h % 100) / 100 - 0.5) * 0.18
            p.at[i, "lat"] = cen[1] + (((h // 100) % 100) / 100 - 0.5) * 0.14

    p["fis_ok"] = p["fis_app"].notna()
    p["faa_ok"] = p["dne"] > 0
    p["stage"] = p["energized"].map(lambda d: "Energized" if d is not None and not pd.isna(d) else "In development")
    p["status"] = [
        "both" if a and b else "faa" if b else "fis" if a else "none" for a, b in zip(p["fis_ok"], p["faa_ok"])
    ]
    p["status_label"] = p["status"].map(STATUS_LABEL)
    p["reviewed_note"] = p["inr"].map(lambda i: links.reviewed.get(i, {}).get("note", ""))
    return p, cases, clusters


def county_centroids_from_geojson(gj: dict) -> dict:
    """Area-weighted centroid of each county polygon (planar; fine at county scale)."""
    out = {}
    for f in gj["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        A = cx = cy = 0.0
        for poly in polys:
            ring = poly[0]
            for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
                cr = x0 * y1 - x1 * y0
                A += cr
                cx += (x0 + x1) * cr
                cy += (y0 + y1) * cr
        if A:
            out[f["properties"]["name"]] = (cx / (3 * A), cy / (3 * A))
    return out

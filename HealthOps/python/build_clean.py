"""
HealthOps - build clean tables from the REAL files (verified against the Kaggle downloads).
Run from the HealthOps/ folder:  python python/build_clean.py
Inputs : data/raw/geocode_health_centre.csv, data/raw/rhs2017/*.csv
Outputs: data/clean/*.csv  (+ data_quality_log.csv, validation_directory_vs_rhs.csv)
Nothing is silently dropped: every excluded / repaired row is written to data_quality_log.csv.
"""
import os, re
import pandas as pd

RAW, RHS, OUT = "data/raw/geocode_health_centre.csv", "data/raw/rhs2017/", "data/clean/"
os.makedirs(OUT, exist_ok=True)
log = []  # (issue, treatment, rows)

# ---------------------------------------------------------------- Census 2011 (state totals, persons)
# Source: Census of India 2011 (Provisional Population Totals/Primary Census Abstract), post-2014 AP/Telangana split.
# AP 49,386,799 + Telangana 35,193,978 = undivided AP 84,580,777 (matches indiastat table). Sum is checked below.
CENSUS = {
 "Uttar Pradesh":199812341,"Maharashtra":112374333,"Bihar":104099452,"West Bengal":91276115,"Madhya Pradesh":72626809,
 "Tamil Nadu":72147030,"Rajasthan":68548437,"Karnataka":61095297,"Gujarat":60439692,"Andhra Pradesh":49386799,
 "Odisha":41974218,"Telangana":35193978,"Kerala":33406061,"Jharkhand":32988134,"Assam":31205576,"Punjab":27743338,
 "Chhattisgarh":25545198,"Haryana":25351462,"Delhi":16787941,"Jammu & Kashmir":12541302,"Uttarakhand":10086292,
 "Himachal Pradesh":6864602,"Tripura":3673917,"Meghalaya":2966889,"Manipur":2855794,"Nagaland":1978502,"Goa":1458545,
 "Arunachal Pradesh":1383727,"Puducherry":1247953,"Mizoram":1097206,"Chandigarh":1055450,"Sikkim":610577,
 "A & N Islands":380581,"Dadra & Nagar Haveli":343709,"Daman & Diu":243247,"Lakshadweep":64473,
}
assert sum(CENSUS.values()) == 1_210_854_977, sum(CENSUS.values())  # India total, Census 2011

def key(s):
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s); s = re.sub(r"\s+", " ", s).strip()
    return {"a and n island": "a and n islands"}.get(s, s)

DISPLAY = {"A & N Islands": "Andaman & Nicobar Islands"}
dim_state = pd.DataFrame({"state_name": [DISPLAY.get(k, k) for k in CENSUS],
                          "state_key": [key(k) for k in CENSUS],
                          "population_total": list(CENSUS.values())}).sort_values("state_name")
dim_state.to_csv(OUT + "dim_state.csv", index=False)

# ---------------------------------------------------------------- dim_facility_type
dim_type = pd.DataFrame({"type_code": ["sub_cen", "phc", "chc", "s_t_h", "dis_h"],
                         "type_label": ["Sub Centre", "PHC", "CHC", "Sub-district / State Hospital", "District Hospital"],
                         "type_order": [1, 2, 3, 4, 5]})
dim_type.to_csv(OUT + "dim_facility_type.csv", index=False)

# ---------------------------------------------------------------- fact_facility
d = pd.read_csv(RAW, dtype=str, keep_default_na=False, na_values=[""])
d.columns = ["state_raw", "district", "subdistrict", "type_code", "facility_name", "facility_address",
             "latitude", "longitude", "active_flag", "notional_physical", "location_type", "ownership", "nin"]
n0 = len(d)
for c in d.columns: d[c] = d[c].str.strip()

# 1) two rows are shifted one column left (Address holds latitude, Latitude holds longitude, Longitude holds 'Y' ...)
shift = d["active_flag"].eq("Physical")
log.append(("Rows with columns shifted one place (address=latitude, latitude=longitude)",
            "Repaired by shifting values back; address set to null", int(shift.sum())))
s = d[shift].copy()
d.loc[shift, "latitude"], d.loc[shift, "longitude"] = s["facility_address"], s["latitude"]
d.loc[shift, "facility_address"] = None
d.loc[shift, "active_flag"], d.loc[shift, "notional_physical"] = "Y", "Physical"
d.loc[shift, "location_type"], d.loc[shift, "ownership"] = "Rural", "Public"
d.loc[shift, "nin"] = None
d["facility_name"] = d["facility_name"].str.replace(r"\s*\?NULL$", "", regex=True)

# 2) "Andhra Pradesh Old" = pre-bifurcation records that duplicate AP + Telangana facilities
d["is_pre_split_ap"] = d["state_raw"].eq("Andhra Pradesh Old")
d[d["is_pre_split_ap"]].to_csv(OUT + "excluded_andhra_pradesh_old.csv", index=False)
log.append(("State = 'Andhra Pradesh Old' (pre-2014 undivided AP; overlaps AP and Telangana rows)",
            "EXCLUDED from model (would double-count); kept in excluded_andhra_pradesh_old.csv", int(d["is_pre_split_ap"].sum())))
d = d[~d["is_pre_split_ap"]].drop(columns="is_pre_split_ap").copy()

# 3) exact duplicates
dup = d.duplicated(keep="first")
d[dup].to_csv(OUT + "rejects_exact_duplicates.csv", index=False)
log.append(("Exact duplicate rows", "First kept, copies written to rejects_exact_duplicates.csv", int(dup.sum())))
d = d[~dup].copy()

# 4) coordinates
d["lat"] = pd.to_numeric(d["latitude"], errors="coerce"); d["lon"] = pd.to_numeric(d["longitude"], errors="coerce")
swapped = d["lat"].between(68, 97.6) & d["lon"].between(6, 37.6)
d.loc[swapped, ["lat", "lon"]] = d.loc[swapped, ["lon", "lat"]].values
log.append(("Latitude/longitude swapped (lat in 68-97, lon in 6-37)", "Swapped back", int(swapped.sum())))
missing = d["lat"].isna() | d["lon"].isna()
valid = d["lat"].between(6, 37.6) & d["lon"].between(68, 97.6)
d["coord_valid"] = valid
d["lat_map"] = d["lat"].where(valid); d["lon_map"] = d["lon"].where(valid)
log.append(("Coordinates missing/non-numeric", "Facility kept in counts; coordinates null (not on map)", int(missing.sum())))
log.append(("Coordinates outside India sanity box (6-37.6N, 68-97.6E), incl. zeros", "Facility kept in counts; coordinates null (not on map)", int((~valid & ~missing).sum())))

# 5) state join key
d["state_key"] = d["state_raw"].map(key)
unmatched = set(d["state_key"]) - set(dim_state["state_key"])
assert not unmatched, unmatched
d = d.merge(dim_state[["state_key", "state_name"]], on="state_key", how="left")
assert set(d["type_code"]) <= set(dim_type["type_code"]), set(d["type_code"])

d.insert(0, "facility_id", range(1, len(d) + 1))
fact_facility = d[["facility_id", "state_key", "state_name", "district", "subdistrict", "type_code", "facility_name",
                   "location_type", "lat", "lon", "coord_valid", "lat_map", "lon_map"]]
fact_facility.to_csv(OUT + "fact_facility.csv", index=False)
log.append(("Rows read / rows in fact_facility", "-", f"{n0} / {len(fact_facility)}"))
# columns deliberately NOT carried: ActiveFlag_C & NOTIONAL_PHYSICAL (constant), Type Of Facility (constant 'Public'),
# Facility Address (99.99% empty), Nin_N (35% empty identifier, no analytical use)

# ---------------------------------------------------------------- fact_staffing (RHS 2017, rural public facilities)
STAFF = [("allo-doc-PHCS_2017", "Allopathic doctor", "PHC"), ("assistant-female-PHCS_2017", "Health assistant (female)", "PHC"),
         ("assistant-male-PHCS_2017", "Health assistant (male)", "PHC"), ("physician-CHCS_2017", "Physician", "CHC"),
         ("surgeons-CHCS_2017", "Surgeon", "CHC"), ("radiographers-CHCS_2017", "Radiographer", "CHC"),
         ("nursing-staff-PHCS-CHCS_2017", "Nursing staff", "PHC + CHC"), ("pharmacists-PHCS-CHCS_2017", "Pharmacist", "PHC + CHC"),
         ("worker-female-subcen_2017", "Health worker (female)", "Sub Centre"), ("worker-male-subcen_2017", "Health worker (male)", "Sub Centre")]
def num(x): return pd.to_numeric(x, errors="coerce")
rows = []
for f, cadre, level in STAFF:
    x = pd.read_csv(RHS + f + ".csv")
    x = x[~x["State/ UT"].str.contains("All India", case=False)]
    for _, r in x.iterrows():
        rows.append(dict(state_key=key(r["State/ UT"]), cadre=cadre, facility_level=level,
                         required=num(r["Required - [R]"]), sanctioned=num(r["Sanctioned - [S]"]),
                         in_position=num(r["In Position - [P]"]), file_shortfall=num(r["Shortfall - [R-P]"])
                         if "Shortfall - [R-P]" in x.columns else None))
st = pd.DataFrame(rows)
st["shortfall_net"] = st["required"] - st["in_position"]                 # negative = surplus
st["shortfall_floor0"] = st["shortfall_net"].clip(lower=0)
st["surplus"] = (-st["shortfall_net"]).clip(lower=0)
unm = set(st["state_key"]) - set(dim_state["state_key"]); assert not unm, unm
dq = st[(st.required.isna()) | (st.in_position.isna())]
log.append(("Staffing rows with null Required or In Position", "Kept as null (not imputed)", len(dq)))
chk = st[st.file_shortfall.notna() & (st.shortfall_net > 0)]
log.append(("File 'Shortfall' differs from Required-InPosition (where both exist)", "Recomputed shortfall used; file column kept as file_shortfall",
            int((chk.file_shortfall != chk.shortfall_net).sum())))
st.to_csv(OUT + "fact_staffing.csv", index=False)

# ---------------------------------------------------------------- fact_trend (functioning facilities by Five-Year-Plan end)
PLAN_END = {"Sixth": 1985, "Seventh": 1990, "Eigth": 1997, "Ninth": 2002, "Tenth": 2007, "Eleventh": 2012, "Twelth": 2017}
rows = []
for f, level in [("year-wise-Subcentre", "Sub Centre"), ("year-wise-PHCS", "PHC"), ("year-wise-CHCS", "CHC")]:
    x = pd.read_csv(RHS + f + ".csv")
    x = x[~x["State/ UT"].str.contains("Total|All India", case=False)]
    for col in x.columns[2:]:
        plan = next(p for p in PLAN_END if p in col)
        for _, r in x.iterrows():
            rows.append(dict(state_key=key(r["State/ UT"]), facility_level=level, plan_period=plan + " Plan",
                             year=PLAN_END[plan], functioning_count=num(r[col])))
tr = pd.DataFrame(rows)
assert not (set(tr["state_key"]) - set(dim_state["state_key"]))
tr.to_csv(OUT + "fact_trend.csv", index=False)

# ---------------------------------------------------------------- validation: directory (Oct 2016) vs RHS functioning (Mar 2017)
cnt = fact_facility.pivot_table(index="state_key", columns="type_code", values="facility_id", aggfunc="count", fill_value=0)
rhs = tr[tr.year == 2017].pivot_table(index="state_key", columns="facility_level", values="functioning_count", aggfunc="sum")
v = dim_state.set_index("state_key")[["state_name", "population_total"]].join(cnt).join(rhs)
v["total_facilities"] = v[["sub_cen", "phc", "chc", "s_t_h", "dis_h"]].sum(axis=1)
v["per_100k"] = (v["total_facilities"] / v["population_total"] * 1e5).round(2)
for a, b, n in [("sub_cen", "Sub Centre", "sc"), ("phc", "PHC", "phc"), ("chc", "CHC", "chc")]:
    v[f"{n}_diff_vs_rhs"] = v[a] - v[b]
v.reset_index().to_csv(OUT + "validation_directory_vs_rhs.csv", index=False)

pd.DataFrame(log, columns=["issue", "treatment", "rows"]).to_csv(OUT + "data_quality_log.csv", index=False)
print(pd.DataFrame(log, columns=["issue", "treatment", "rows"]).to_string())
print("\nNational: facilities", len(fact_facility), "| population", dim_state.population_total.sum(),
      "| per100k", round(len(fact_facility) / dim_state.population_total.sum() * 1e5, 2))
print(v[["state_name", "total_facilities", "per_100k", "sc_diff_vs_rhs", "phc_diff_vs_rhs", "chc_diff_vs_rhs"]].sort_values("per_100k", ascending=False).to_string())

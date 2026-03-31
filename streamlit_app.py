import streamlit as st
import folium
import pandas as pd
from streamlit_folium import st_folium
import datetime
import ee
import os
import re

# ============================================================
# ✅ IMPORT UTILS
# ============================================================
from utils.vector_io import load_vector
from utils.gee_ndvi import (
    init_gee,
    get_latest_s2_image,
    get_available_s2_dates,
    get_closest_s2_image,
    compute_ndvi,
    compute_vegetation_mask
)
from utils.ndvi_processing import zonal_stats_ndvi

# ============================================================
# ✅ Format NDVI
# ============================================================
def fmt(v):
    try:
        return f"{float(v):.3f}"
    except:
        return "NA"

# ============================================================
# ✅ INIT GEE
# ============================================================
service_account = st.secrets["GEE_SERVICE_ACCOUNT"]
private_key = st.secrets["GEE_PRIVATE_KEY"]
init_gee(service_account, private_key)

st.title("🌱 NDVI – Analyse simple (Kermap)")

# ============================================================
# ✅ SESSION STATE SIMPLIFIÉ
# ============================================================
DEFAULTS = {
    "available_dates_single": None,
    "image_single": None,
    "date_single": None,
    "result_single": None,
}

for k,v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ============================================================
# ✅ UPLOAD SIG
# ============================================================
uploaded = st.file_uploader("📁 Charger un SHP (ZIP) ou GEOJSON", type=["zip","geojson"])
if not uploaded:
    st.stop()

features = load_vector(uploaded)
st.success(f"{len(features)} parcelles chargées ✅")

geoms = [f["geometry"] for f in features]
minx = min(g.bounds[0] for g in geoms)
miny = min(g.bounds[1] for g in geoms)
maxx = max(g.bounds[2] for g in geoms)
maxy = max(g.bounds[3] for g in geoms)

aoi = ee.Geometry.Rectangle([minx,miny,maxx,maxy])


# ============================================================
# ✅ COULEURS & CLASSIFICATION NDVI
# ============================================================
def classify_ndvi(nd):
    if nd is None: return ("Indéterminé","#bdbdbd")
    if nd < 0.25: return ("Sol nu","#d73027")
    if nd < 0.50: return ("Végétation faible","#fee08b")
    return ("Végétation dense","#1a9850")

def covered(v):
    if v is None: return "Indéterminé"
    return "✅ Couvert" if v>=0.5 else "❌ Non couvert"

def colorize(nd):
    if nd is None:
        return "#bbbbbb"
    if nd < 0.25:
        return "#d73027"
    if nd < 0.50:
        return "#fee08b"
    return "#1a9850"


# ============================================================
# ✅ SELECTEUR DE TUILES
# ============================================================
def tuile_selector(label, dates_key):

    mode = st.radio(
        f"Choisir la tuile ({label})",
        ["Dernière tuile","Tuiles disponibles","Recherche par mois"],
        key=f"mode_{label}"
    )

    if mode == "Dernière tuile":
        if st.button(f"🔍 Charger dernière tuile ({label})"):
            return get_latest_s2_image(aoi)
        return None,None

    if mode == "Tuiles disponibles":
        if st.button(f"📅 Lister ({label})"):
            st.session_state[dates_key] = get_available_s2_dates(aoi,120)

        if st.session_state.get(dates_key):
            chosen = st.selectbox(
                f"Dates ({label})",
                st.session_state[dates_key],
                key=f"sel_{label}",
                format_func=lambda d: d.strftime("%Y-%m-%d")
            )
            if st.button(f"✅ Charger ({label})"):
                return get_closest_s2_image(aoi,chosen)

        return None,None

    if mode == "Recherche par mois":

        year = st.selectbox(
            f"Année ({label})",
            list(range(2017, datetime.date.today().year+1))[::-1],
            key=f"year_{label}"
        )

        month_list=[
            ("01","Janvier"),("02","Février"),("03","Mars"),("04","Avril"),
            ("05","Mai"),("06","Juin"),("07","Juillet"),("08","Août"),
            ("09","Septembre"),("10","Octobre"),("11","Novembre"),("12","Décembre")
        ]

        month_num,_ = st.selectbox(
            f"Mois ({label})",
            month_list,
            key=f"month_{label}",
            format_func=lambda x: x[1]
        )

        start=f"{year}-{month_num}-01"
        end=f"{year+1}-01-01" if month_num=="12" else f"{year}-{int(month_num)+1:02d}-01"

        if st.button(f"📅 Rechercher ({label})"):

            col = (
                ee.ImageCollection("COPERNICUS/S2_SR")
                .filterBounds(aoi)
                .filterDate(start,end)
                .sort("system:time_start", False)
            )

            timestamps = col.aggregate_array("system:time_start").getInfo()

            if not timestamps:
                st.error("❌ Aucune tuile ce mois.")
                return None,None

            month_dates = sorted(
                {datetime.datetime.fromtimestamp(t/1000, datetime.UTC).date()
                for t in timestamps},
                reverse=True
            )

            st.session_state[dates_key] = month_dates

        if st.session_state.get(dates_key):

            chosen = st.selectbox(
                f"Dates du mois ({label})",
                st.session_state[dates_key],
                key=f"sel_month_{label}"
            )

            if st.button(f"✅ Charger ({label})"):
                return get_closest_s2_image(aoi,chosen)

        return None,None


# =============================================================================
# ✅ ANALYSE SIMPLE — VERSION ÉPURÉE
# =============================================================================

st.header("🟩 Analyse NDVI — 1 Date")

img, d = tuile_selector("SIMPLE","available_dates_single")

# ✅ DEBUG FOOTPRINT
if img is not None:
    try:
        st.write("DEBUG Footprint :", img.geometry().bounds().getInfo())
    except:
        st.write("DEBUG Footprint : Erreur")

# ✅ Calcul NDVI
if img is not None and d is not None:

    st.session_state.date_single = d

    ndvi = compute_ndvi(img)
    veg_mask = compute_vegetation_mask(ndvi,0.25)

    rows=[]
    for feat in features:
        geom = feat["geometry"]
        num_ilot = feat["properties"].get("NUM_ILOT","ILOT")

        nd_mean, veg_prop = zonal_stats_ndvi(ndvi,veg_mask,geom)
        classe_txt,col_cl = classify_ndvi(nd_mean)

        rows.append({
            "NUM_ILOT": num_ilot,
            "NDVI_moyen": nd_mean,
            "Classe": classe_txt,
            "Proportion_couvert": veg_prop,
            "Couvert": covered(veg_prop),
            "Date": str(d)
        })

    st.session_state.result_single = pd.DataFrame(rows)

# ✅ AFFICHAGE RÉSULTATS
if st.session_state.result_single is not None:

    df = st.session_state.result_single
    st.success(f"✅ Résultats NDVI — Tuile : {st.session_state.date_single}")
    st.dataframe(df)

    # ✅ CARTE NDVI
    m = folium.Map(location=[(miny+maxy)/2,(minx+maxx)/2], zoom_start=14)

    for idx, feat in enumerate(features):
        geom = feat["geometry"]
        nd = df.iloc[idx]["NDVI_moyen"]
        color = colorize(nd)

        tooltip_html = (
            f"<b>Ilot :</b> {df.iloc[idx]['NUM_ILOT']}<br>"
            f"<b>NDVI :</b> {fmt(nd)}<br>"
            f"<b>Classe :</b> {df.iloc[idx]['Classe']}"
        )

        folium.GeoJson(
            geom.__geo_interface__,
            style_function=lambda x, col=color: {
                "fillColor": col,
                "color": "black",
                "weight": 1,
                "fillOpacity": 0.7
            },
            tooltip=tooltip_html
        ).add_to(m)

    st_folium(m, height=600)

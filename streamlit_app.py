import streamlit as st
import folium
import pandas as pd
from streamlit_folium import st_folium
import datetime
import ee
import os
import re
import pyproj
from shapely.ops import transform
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

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

st.title("🌱 NDVI – Analyse simple (SHP avec détection automatique du CRS)")

# ============================================================
# ✅ SESSION STATE
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
uploaded = st.file_uploader("📁 Charger un SHP (ZIP uniquement)", type=["zip"])
if not uploaded:
    st.stop()

# ✅ Chargement brut
features = load_vector(uploaded)
st.success(f"{len(features)} parcelles chargées ✅")

# ============================================================
# ✅ DETECTION AUTOMATIQUE DU CRS POUR SHP
# ============================================================
st.write("DEBUG : détection automatique du CRS...")

try:
    import fiona
    uploaded.seek(0)
    with fiona.BytesCollection(uploaded.read()) as src:
        crs_in = src.crs_wkt or src.crs
except:
    crs_in = None

st.write("DEBUG CRS détecté :", crs_in)

# ✅ Si QGIS/Fiona ne détecte pas le CRS → test intelligent
possible_crs = [
    "EPSG:3948",  # CC48
    "EPSG:3949",  # CC49
    "EPSG:3950",  # CC50
    "EPSG:2154",  # Lambert 93
    "EPSG:4326",  # WGS84
    "EPSG:3857",  # Web Mercator
]

if not crs_in:
    st.warning("⚠ CRS non détecté. Tentative de détection automatique...")

    # On prend un point pour tester
    g0 = features[0]["geometry"]
    x0, y0 = list(g0.exterior.coords)[0] if isinstance(g0, Polygon) else list(list(g0.geoms)[0].exterior.coords)[0]

    detected = None
    for test in possible_crs:
        try:
            transf = pyproj.Transformer.from_crs(test, "EPSG:4326", always_xy=True)
            tx, ty = transf.transform(x0, y0)

            # ✅ Test si le point transformé tombe en France
            if 4 <= tx <= 10 and 42 <= ty <= 52:
                detected = test
                break
        except:
            pass

    if detected is None:
        st.error("❌ Impossible de détecter le CRS. Lis le code EPSG dans QGIS.")
        st.stop()

    crs_in = detected
    st.success(f"✅ CRS automatiquement détecté : {crs_in}")

else:
    crs_in = pyproj.CRS.from_user_input(crs_in).to_authority()[1]
    crs_in = f"EPSG:{crs_in}"
    st.success(f"✅ CRS détecté automatiquement : {crs_in}")

# ============================================================
# ✅ REPROJECTION SHP -> WGS84
# ============================================================
source_crs = pyproj.CRS.from_user_input(crs_in)
target_crs = pyproj.CRS.from_epsg(4326)
transformer = pyproj.Transformer.from_crs(source_crs, target_crs, always_xy=True).transform

for f in features:
    geom = f["geometry"]
    if geom is not None:
        f["geometry"] = transform(transformer, geom)

# ============================================================
# ✅ CALCUL AOI (WGS84)
# ============================================================
geoms = [f["geometry"] for f in features]
minx = min(g.bounds[0] for g in geoms)
miny = min(g.bounds[1] for g in geoms)
maxx = max(g.bounds[2] for g in geoms)
maxy = max(g.bounds[3] for g in geoms)

# ✅ Extension AOI pour garantir la bonne dalle Sentinel
expand = 0.1   # ~10 km
aoi = ee.Geometry.Rectangle([minx-expand, miny-expand, maxx+expand, maxy+expand])

st.write("DEBUG AOI (WGS84) :", [minx, miny, maxx, maxy])

# ============================================================
# ✅ CLASSIFICATION NDVI
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
# ✅ SELECTEUR TUILES
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

        months = [
            ("01","Janvier"),("02","Février"),("03","Mars"),("04","Avril"),
            ("05","Mai"),("06","Juin"),("07","Juillet"),("08","Août"),
            ("09","Septembre"),("10","Octobre"),("11","Novembre"),("12","Décembre")
        ]
        month_num,_ = st.selectbox(
            f"Mois ({label})",
            months,
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
                f"Dates ({label})",
                st.session_state[dates_key],
                key=f"sel_month_{label}"
            )
            if st.button(f"✅ Charger ({label})"):
                return get_closest_s2_image(aoi,chosen)

        return None,None

# ============================================================
# ✅ ANALYSE SIMPLE
# ============================================================
st.header("🟩 Analyse NDVI — 1 Date")

img, d = tuile_selector("SIMPLE","available_dates_single")

if img is not None:
    try:
        st.write("DEBUG Footprint :", img.geometry().bounds().getInfo())
    except:
        st.write("DEBUG Footprint : Erreur")

# ✅ ANALYSE NDVI
if img is not None and d is not None:

    st.session_state.date_single = d

    ndvi = compute_ndvi(img)
    veg_mask = compute_vegetation_mask(ndvi, 0.25)

    # ✅ DEBUG FOOTPRINT SENTINEL
    try:
        footprint = img.geometry().bounds().getInfo()
        st.write("DEBUG FOOTPRINT Sentinel :", footprint)
    except Exception as e:
        st.write("DEBUG FOOTPRINT : erreur :", e)

    rows = []

    for feat in features:
        geom = feat["geometry"]
        num_ilot = feat["properties"].get("NUM_ILOT", "ILOT")

        # ✅ Convertir la géométrie en EarthEngine
        try:
            geom_ee = shapely_to_ee(geom)
        except Exception as e:
            st.write(f"DEBUG conversion geom {num_ilot} :", e)
            geom_ee = None

        # ✅ DEBUG PIXELS (voir si Sentinel couvre l’îlot)
        try:
            pixel_count = ndvi.sample(region=geom_ee, scale=10).size().getInfo()
        except Exception as e:
            pixel_count = f"Erreur sample : {e}"

        st.write(f"DEBUG pixels pour {num_ilot} :", pixel_count)

        # ✅ Calcul NDVI pour l’îlot
        nd_mean, veg_prop = zonal_stats_ndvi(ndvi, veg_mask, geom)

        classe_txt, col_cl = classify_ndvi(nd_mean)

        rows.append({
            "NUM_ILOT": num_ilot,
            "NDVI_moyen": nd_mean,
            "Classe": classe_txt,
            "Proportion_couvert": veg_prop,
            "Couvert": covered(veg_prop),
            "Date": str(d)
        })

    st.session_state.result_single = pd.DataFrame(rows)

# ============================================================
# ✅ AFFICHAGE RÉSULTATS + CARTE
# ============================================================
if st.session_state.result_single is not None:

    df = st.session_state.result_single

    st.success(f"✅ Résultats NDVI — Tuile : {st.session_state.date_single}")
    st.dataframe(df)

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

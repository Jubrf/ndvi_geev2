import streamlit as st
import folium
import pandas as pd
from streamlit_folium import st_folium
import datetime
import ee
import pyproj
from shapely.ops import transform

# ============================================================
# ✅ IMPORT UTILS
# ============================================================
from utils.vector_io import load_vector   # fournis CRS !
from utils.gee_ndvi import (
    init_gee,
    get_latest_s2_image,
    get_available_s2_dates,
    get_closest_s2_image,
    compute_ndvi,
    compute_vegetation_mask
)
from utils.ndvi_processing import zonal_stats_ndvi, shapely_to_ee

# ============================================================
# ✅ INIT GEE
# ============================================================
service_account = st.secrets["GEE_SERVICE_ACCOUNT"]
private_key = st.secrets["GEE_PRIVATE_KEY"]
init_gee(service_account, private_key)

st.title("🌱 NDVI – Analyse simple (SHP avec reprojection fiable depuis .prj)")

# ============================================================
# ✅ UPLOAD SHP
# ============================================================
uploaded = st.file_uploader("📁 Charger un SHP (ZIP)", type=["zip"])
if not uploaded:
    st.stop()

# ➜ IMPORTANT : load_vector() DOIT renvoyer features + CRS
features, source_crs = load_vector(uploaded, return_crs=True)

if not source_crs:
    st.error("❌ Le SHP ne fournit pas de CRS dans son fichier .prj")
    st.stop()

st.success(f"✅ Parcelles chargées : {len(features)}")
st.write("✅ CRS du SHP (depuis .prj) :", source_crs)

# ============================================================
# ✅ REPROJECTION → WGS84
# ============================================================
target_crs = pyproj.CRS.from_epsg(4326)
transformer = pyproj.Transformer.from_crs(source_crs, target_crs, always_xy=True).transform

for f in features:
    geom = f["geometry"]
    f["geometry"] = transform(transformer, geom)

# ============================================================
# ✅ AOI (WGS84)
# ============================================================
geoms = [f["geometry"] for f in features]

minx = min(g.bounds[0] for g in geoms)
miny = min(g.bounds[1] for g in geoms)
maxx = max(g.bounds[2] for g in geoms)
maxy = max(g.bounds[3] for g in geoms)

expand = 0.05   # sécurise la dalle Sentinel
aoi = ee.Geometry.Rectangle([minx-expand, miny-expand, maxx+expand, maxy+expand])

st.write("DEBUG AOI (WGS84) :", [minx, miny, maxx, maxy])

# ============================================================
# ✅ NDVI Classification
# ============================================================
def fmt(v):
    try:
        return f"{float(v):.3f}"
    except:
        return "NA"

def classify_ndvi(v):
    if v is None: return ("Indéterminé", "#bdbdbd")
    if v < 0.25: return ("Sol nu", "#d73027")
    if v < 0.50: return ("Végétation faible", "#fee08b")
    return ("Végétation dense", "#1a9850")

def covered(v):
    if v is None: return "Indéterminé"
    return "✅ Couvert" if v >= 0.5 else "❌ Non couvert"

def colorize(v):
    if v is None: return "#bbbbbb"
    if v < 0.25: return "#d73027"
    if v < 0.50: return "#fee08b"
    return "#1a9850"

# ============================================================
# ✅ SELECTEUR TUILES
# ============================================================
def tuile_selector(label, state_key):

    mode = st.radio(
        f"Choisir une tuile ({label})",
        ["Dernière tuile", "Tuiles disponibles", "Recherche par mois"],
        key=f"mode_{label}"
    )

    if mode == "Dernière tuile":
        if st.button("🔍 Charger dernière tuile"):
            return get_latest_s2_image(aoi)
        return None, None

    if mode == "Tuiles disponibles":
        if st.button("📅 Lister les dates"):
            st.session_state[state_key] = get_available_s2_dates(aoi, 120)

        if st.session_state.get(state_key):
            chosen = st.selectbox("Dates :", st.session_state[state_key])
            if st.button("✅ Charger cette date"):
                return get_closest_s2_image(aoi, chosen)
        return None, None

    if mode == "Recherche par mois":
        year = st.selectbox("Année :", list(range(2017, datetime.date.today().year+1))[::-1])
        month = st.selectbox("Mois :", list(range(1,13)), format_func=lambda m: f"{m:02d}")

        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year+1}-01-01"
        else:
            end = f"{year}-{month+1:02d}-01"

        if st.button("🔎 Rechercher"):
            col = (
                ee.ImageCollection("COPERNICUS/S2_SR")
                .filterBounds(aoi)
                .filterDate(start, end)
                .sort("system:time_start", False)
            )

            timestamps = col.aggregate_array("system:time_start").getInfo()
            if not timestamps:
                st.error("❌ Aucune image ce mois.")
                return None, None

            dates = sorted({
                datetime.datetime.fromtimestamp(t/1000, datetime.UTC).date()
                for t in timestamps
            }, reverse=True)

            st.session_state[state_key] = dates

        if st.session_state.get(state_key):
            chosen = st.selectbox("Dates trouvées :", st.session_state[state_key])
            if st.button("✅ Charger cette image"):
                return get_closest_s2_image(aoi, chosen)

        return None, None

# ============================================================
# ✅ ANALYSE NDVI
# ============================================================
st.header("🟩 Analyse NDVI")

img, d = tuile_selector("SIMPLE", "dates_simple")

# ✅ DEBUG Footprint
if img:
    try:
        st.write("DEBUG Footprint Sentinel :", img.geometry().bounds().getInfo())
    except:
        st.write("DEBUG Footprint : erreur")

if img and d:

    ndvi = compute_ndvi(img)
    veg_mask = compute_vegetation_mask(ndvi, 0.25)

    rows = []

    for feat in features:
        geom = feat["geometry"]
        ilot = feat["properties"].get("NUM_ILOT", "ILOT")

        # ✅ Convertir Shapely → EE
        geom_ee = shapely_to_ee(geom)

        # ✅ DEBUG PIXELS
        try:
            px = ndvi.sample(region=geom_ee, scale=10).size().getInfo()
        except Exception as e:
            px = f"Erreur : {e}"
        st.write(f"DEBUG pixels pour {ilot} :", px)

        # ✅ NDVI
        nd_mean, veg_prop = zonal_stats_ndvi(ndvi, veg_mask, geom)
        classe, col = classify_ndvi(nd_mean)

        rows.append({
            "NUM_ILOT": ilot,
            "NDVI_moyen": nd_mean,
            "Classe": classe,
            "Proportion_couvert": veg_prop,
            "Couvert": covered(veg_prop),
        })

    df = pd.DataFrame(rows)
    st.success(f"✅ Résultats NDVI – tuile du {d}")
    st.dataframe(df)

    # ✅ Carte
    m = folium.Map(location=[(miny+maxy)/2, (minx+maxx)/2], zoom_start=14)

    for idx, feat in enumerate(features):
        geom = feat["geometry"]
        nd = df.iloc[idx]["NDVI_moyen"]
        col = colorize(nd)

        tooltip = f"Ilot : {df.iloc[idx]['NUM_ILOT']}<br>NDVI : {fmt(nd)}"

        folium.GeoJson(
            geom.__geo_interface__,
            style_function=lambda x, color=col: {
                "fillColor": color,
                "color": "black",
                "weight": 1,
                "fillOpacity": 0.7
            },
            tooltip=tooltip
        ).add_to(m)

    st_folium(m, height=600)

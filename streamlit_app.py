import streamlit as st
import folium
import pandas as pd
from streamlit_folium import st_folium
import datetime
import ee
import pyproj
import geopandas as gpd
from shapely.ops import transform

# ============================================================
# ✅ IMPORT UTILS
# ============================================================
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

st.title("🌱 NDVI – Analyse simple (SHP uniquement, CRS via .prj)")

# ============================================================
# ✅ UPLOAD SHP
# ============================================================
uploaded = st.file_uploader("📁 Charger un SHP (ZIP)", type=["zip"])
if not uploaded:
    st.stop()

# ============================================================
# ✅ LECTURE SHP VIA GEOPANDAS (COMPATIBLE STREAMLIT CLOUD)
# ============================================================
try:
    gdf = gpd.read_file(uploaded)
except Exception as e:
    st.error(f"❌ Impossible de lire le SHP : {e}")
    st.stop()

if gdf.crs is None:
    st.error("❌ Le SHP n'a pas de CRS (.prj manquant). Impossible de continuer.")
    st.stop()

st.success(f"✅ {len(gdf)} parcelles chargées")
st.write("📌 CRS détecté (via .prj) :", gdf.crs)

source_crs = pyproj.CRS.from_user_input(gdf.crs)

# ============================================================
# ✅ Conversion en structures Python (features = list)
# ============================================================
features = []
for _, row in gdf.iterrows():
    features.append({
        "properties": {k: v for k, v in row.items() if k != "geometry"},
        "geometry": row.geometry
    })

# ============================================================
# ✅ REPROJECTION → WGS84
# ============================================================
target_crs = pyproj.CRS.from_epsg(4326)
transformer = pyproj.Transformer.from_crs(source_crs, target_crs, always_xy=True).transform

for f in features:
    f["geometry"] = transform(transformer, f["geometry"])

# ============================================================
# ✅ AOI
# ============================================================
geoms = [f["geometry"] for f in features]
minx = min(g.bounds[0] for g in geoms)
miny = min(g.bounds[1] for g in geoms)
maxx = max(g.bounds[2] for g in geoms)
maxy = max(g.bounds[3] for g in geoms)

expand = 0.05
aoi = ee.Geometry.Rectangle([minx-expand, miny-expand, maxx+expand, maxy+expand])

st.write("DEBUG AOI (WGS84):", [minx, miny, maxx, maxy])

# ============================================================
# ✅ CLASSIFICATION NDVI (corrigée)
# ============================================================
def fmt(v):
    try:
        return f"{float(v):.3f}"
    except:
        return "NA"

def classify_ndvi(nd):
    """Retourne (classe_texte, couleur_hex)"""
    if nd is None:
        return "Indéterminé", "#bdbdbd"
    if nd < 0.25:
        return "Sol nu", "#d73027"
    if nd < 0.50:
        return "Végétation faible", "#fee08b"
    return "Végétation dense", "#1a9850"

def covered(v):
    if v is None:
        return "Indéterminé"
    return "✅ Couvert" if v >= 0.5 else "❌ Non couvert"

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
def tuile_selector(key):

    mode = st.radio("Choisir la tuile", ["Dernière tuile","Tuiles disponibles","Recherche par mois"])

    if mode == "Dernière tuile":
        if st.button("Charger dernière tuile"):
            return get_latest_s2_image(aoi)
        return None, None

    if mode == "Tuiles disponibles":
        if st.button("Lister dates"):
            st.session_state[key] = get_available_s2_dates(aoi)

        if st.session_state.get(key):
            date = st.selectbox("Dates :", st.session_state[key])
            if st.button("Charger cette date"):
                return get_closest_s2_image(aoi, date)
        return None,None

    if mode == "Recherche par mois":
        year = st.selectbox("Année :", list(range(2017, datetime.date.today().year+1))[::-1])
        month = st.selectbox("Mois :", range(1,13), format_func=lambda x: f"{x:02d}")

        start = f"{year}-{month:02d}-01"
        end   = f"{year+1}-01-01" if month==12 else f"{year}-{month+1:02d}-01"

        if st.button("Rechercher"):
            col = (
                ee.ImageCollection("COPERNICUS/S2_SR")
                .filterBounds(aoi)
                .filterDate(start,end)
                .sort("system:time_start", False)
            )
            timestamps = col.aggregate_array("system:time_start").getInfo()

            if not timestamps:
                st.error("❌ Aucune image ce mois.")
                return None,None

            dates = sorted({
                datetime.datetime.fromtimestamp(t/1000, datetime.UTC).date()
                for t in timestamps
            }, reverse=True)

            st.session_state[key] = dates

        if st.session_state.get(key):
            date = st.selectbox("Dates trouvées :", st.session_state[key])
            if st.button("Charger image"):
                return get_closest_s2_image(aoi, date)

        return None,None

# ============================================================
# ✅ ANALYSE NDVI
# ============================================================
st.header("🟩 Analyse NDVI")

img, dsel = tuile_selector("dates_simple")

# Footprint
if img:
    st.write("DEBUG Footprint :", img.geometry().bounds().getInfo())

if img and dsel:

    ndvi = compute_ndvi(img)
    veg_mask = compute_vegetation_mask(ndvi,0.25)

    rows = []

    for f in features:
        ilot = f["properties"].get("NUM_ILOT","")
        geom  = f["geometry"]
        geom_ee = shapely_to_ee(geom)

        # DEBUG PIXELS
        try:
            px = ndvi.sample(region=geom_ee, scale=10).size().getInfo()
        except Exception as e:
            px = f"Erreur : {e}"
        st.write(f"DEBUG pixels {ilot} :", px)

        nd_mean, veg_prop = zonal_stats_ndvi(ndvi, veg_mask, geom)
        classe, couleur = classify_ndvi(nd_mean)

        rows.append({
            "NUM_ILOT": ilot,
            "NDVI_moyen": nd_mean,
            "Classe": classe,
            "Couvert": covered(veg_prop),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df)

    # Carte NDVI
    m = folium.Map(location=[(miny+maxy)/2,(minx+maxx)/2], zoom_start=14)
    for i, f in enumerate(features):
        nd = df.iloc[i]["NDVI_moyen"]
        col = colorize(nd)

        folium.GeoJson(
            f["geometry"].__geo_interface__,
            style_function=lambda x, c=col: {
                "fillColor": c,
                "color": "black",
                "weight": 1,
                "fillOpacity": 0.7,
            },
            tooltip=f"Ilot : {df.iloc[i]['NUM_ILOT']}<br>NDVI : {fmt(nd)}"
        ).add_to(m)

    st_folium(m, height=600)

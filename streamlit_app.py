import streamlit as st
import folium
import pandas as pd
from streamlit_folium import st_folium
import datetime
import ee
import io

from utils.vector_io import load_vector
from utils.gee_ndvi import (
    init_gee,
    get_latest_s2_image,
    get_available_s2_dates,
    get_closest_s2_image,
    compute_ndvi,
    compute_vegetation_mask,
    _build_geom_ee,
    _COLLECTIONS
)
from utils.ndvi_processing import zonal_stats_ndvi

# ============================================================
# FORMAT NDVI
# ============================================================
def fmt(v):
    try:
        return f"{float(v):.3f}"
    except:
        return "NA"

# ============================================================
# INIT GEE
# ============================================================
service_account = st.secrets["GEE_SERVICE_ACCOUNT"]
private_key     = st.secrets["GEE_PRIVATE_KEY"]
init_gee(service_account, private_key)

st.title("🌱 NDVI – Analyse parcellaire Sentinel-2")

# ============================================================
# FILE UPLOAD
# ============================================================
uploaded = st.file_uploader("📁 Charger un SHP (ZIP) ou GEOJSON", type=["zip", "geojson"])

# ============================================================
# RESET SESSION STATE SI NOUVEAU FICHIER
# Détecte le changement de fichier via son nom — reset complet si différent.
# ============================================================
if uploaded is not None:
    current_file = uploaded.name
    if st.session_state.get("loaded_file") != current_file:
        # Nouveau fichier → reset total
        for key in ["loaded_file", "result_single", "date_single",
                    "available_dates_single", "features", "bounds"]:
            st.session_state.pop(key, None)
        st.session_state["loaded_file"] = current_file
        st.rerun()
else:
    st.stop()

# ============================================================
# SESSION STATE
# ============================================================
if "result_single"         not in st.session_state: st.session_state.result_single         = None
if "date_single"           not in st.session_state: st.session_state.date_single           = None
if "available_dates_single" not in st.session_state: st.session_state.available_dates_single = None

# ============================================================
# CHARGEMENT VECTEUR
# ============================================================
features = load_vector(uploaded)
st.success(f"{len(features)} parcelles chargées ✅")

# DEBUG géométries
for f in features[:3]:
    st.write("DEBUG geom bounds :", f["geometry"].bounds)

# ============================================================
# AOI (rectangle englobant — conservé pour compatibilité signature)
# ============================================================
geoms = [f["geometry"] for f in features]
minx  = min(g.bounds[0] for g in geoms)
miny  = min(g.bounds[1] for g in geoms)
maxx  = max(g.bounds[2] for g in geoms)
maxy  = max(g.bounds[3] for g in geoms)
aoi   = ee.Geometry.Rectangle([minx, miny, maxx, maxy])

st.write("DEBUG AOI (WGS84):", [minx, miny, maxx, maxy])

# ============================================================
# CLASSIFICATION
# ============================================================
def classify_ndvi(nd):
    if nd is None:      return ("Indéterminé",     "#bdbdbd")
    if nd < 0.25:       return ("Sol nu",           "#d73027")
    if nd < 0.50:       return ("Végétation faible","#fee08b")
    return                     ("Végétation dense", "#1a9850")

def covered(v):
    if v is None: return "Indéterminé"
    return "✅ Couvert" if v >= 0.5 else "❌ Non couvert"

def colorize(nd):
    if nd is None: return "#bbbbbb"
    if nd < 0.25:  return "#d73027"
    if nd < 0.50:  return "#fee08b"
    return                "#1a9850"

# ============================================================
# SÉLECTEUR TUILE
# ============================================================
def tuile_selector(label, dates_key):

    mode = st.radio(
        f"Choisir la tuile ({label})",
        ["Dernière tuile", "Tuiles disponibles", "Recherche par mois"],
        key=f"mode_{label}"
    )

    if mode == "Dernière tuile":
        if st.button(f"Charger dernière tuile ({label})"):
            return get_latest_s2_image(aoi, features)
        return None, None

    if mode == "Tuiles disponibles":
        if st.button(f"Voir tuiles ({label})"):
            st.session_state[dates_key] = get_available_s2_dates(aoi, features)

        if st.session_state.get(dates_key):
            selected = st.selectbox(
                f"Dates ({label})",
                st.session_state[dates_key],
                key=f"sel_{label}",
                format_func=lambda d: d.strftime("%Y-%m-%d")
            )
            if st.button(f"Charger cette tuile ({label})"):
                return get_closest_s2_image(aoi, selected, features)

        return None, None

    if mode == "Recherche par mois":

        year = st.selectbox(
            f"Année ({label})",
            list(range(2017, datetime.date.today().year + 1))[::-1],
            key=f"year_{label}"
        )

        month_list = [
            ("01","Janvier"),("02","Février"),("03","Mars"),("04","Avril"),
            ("05","Mai"),("06","Juin"),("07","Juillet"),("08","Août"),
            ("09","Septembre"),("10","Octobre"),("11","Novembre"),("12","Décembre")
        ]

        month_num, _ = st.selectbox(
            f"Mois ({label})",
            month_list,
            key=f"month_{label}",
            format_func=lambda x: x[1]
        )

        start = f"{year}-{month_num}-01"
        end   = f"{year+1}-01-01" if month_num == "12" \
                else f"{year}-{int(month_num)+1:02d}-01"

        if st.button(f"Rechercher ({label})"):
            geom_ee   = _build_geom_ee(features)
            all_dates = set()
            for colname in _COLLECTIONS:
                col = (
                    ee.ImageCollection(colname)
                    .filterBounds(geom_ee)
                    .filterDate(start, end)
                    .sort("system:time_start", False)
                )
                timestamps = col.aggregate_array("system:time_start").getInfo()
                for t in timestamps:
                    all_dates.add(datetime.datetime.fromtimestamp(t / 1000).date())

            if not all_dates:
                st.error("❌ Aucune tuile ce mois.")
                return None, None

            st.session_state[dates_key] = sorted(all_dates, reverse=True)

        if st.session_state.get(dates_key):
            selected = st.selectbox(
                f"Dates du mois ({label})",
                st.session_state[dates_key],
                key=f"sel_month_{label}"
            )
            if st.button(f"Charger date ({label})"):
                return get_closest_s2_image(aoi, selected, features)

        return None, None

# ============================================================
# ANALYSE NDVI
# ============================================================
st.header("🟩 Analyse NDVI — 1 Date")

img, d = tuile_selector("SIMPLE", "available_dates_single")

# DEBUG footprint
if img is not None:
    try:
        st.write("DEBUG S2 footprint :", img.geometry().bounds().getInfo())
    except Exception as e:
        st.error(f"Erreur debug footprint : {e}")

if img is not None and d is not None:

    st.session_state.date_single = d

    ndvi     = compute_ndvi(img)
    veg_mask = compute_vegetation_mask(ndvi, 0.25)

    rows = []
    for feat in features:

        geom     = feat["geometry"]
        num_ilot = feat["properties"].get("NUM_ILOT", "ILOT")

        # DEBUG pixels
        try:
            geom_ee = ee.Geometry(geom.__geo_interface__)
            px      = ndvi.sample(region=geom_ee, scale=10).size().getInfo()
        except Exception as e:
            px = f"Erreur : {e}"
        st.write(f"DEBUG pixels pour {num_ilot} :", px)

        nd_mean, veg_prop    = zonal_stats_ndvi(ndvi, veg_mask, geom)
        classe_txt, _        = classify_ndvi(nd_mean)

        rows.append({
            "NUM_ILOT"   : num_ilot,
            "NDVI_moyen" : nd_mean,
            "Classe"     : classe_txt,
            "Proportion" : veg_prop,
            "Couvert"    : covered(veg_prop),
            "Date"       : str(d)
        })

    st.session_state.result_single = pd.DataFrame(rows)

# ============================================================
# AFFICHAGE + CARTE + EXPORT CSV PROPRE
# ============================================================
if st.session_state.result_single is not None:

    df = st.session_state.result_single

    st.success(f"✅ Résultats NDVI — {st.session_state.date_single}")
    st.dataframe(df)

    # ✅ EXPORT CSV propre (séparateur ; pour Excel FR)
    csv_bytes = df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    st.download_button(
        label     = "⬇️ Exporter le tableau (CSV Excel)",
        data      = csv_bytes,
        file_name = f"ndvi_{st.session_state.date_single}.csv",
        mime      = "text/csv"
    )

    # CARTE
    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=14)

    for idx, feat in enumerate(features):
        geom  = feat["geometry"]
        nd    = df.iloc[idx]["NDVI_moyen"]
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
                "color"     : "black",
                "weight"    : 1,
                "fillOpacity": 0.7
            },
            tooltip=tooltip_html
        ).add_to(m)

    st_folium(m, height=600)

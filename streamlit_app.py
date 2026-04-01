import streamlit as st
import folium
import pandas as pd
from streamlit_folium import st_folium
import datetime
import ee

from utils.vector_io import load_vector
from utils.gee_ndvi import (
    init_gee,
    get_latest_s2_image,
    get_available_s2_dates,
    get_closest_s2_image,
    compute_ndvi,
    compute_vegetation_mask,
    _build_geom_ee,
    _COLLECTIONS,
    compute_evi2,
)
from utils.ndvi_processing import zonal_stats_all

# ============================================================
# FORMAT NDVI
# ============================================================
def fmt(v):
    try:
        return f"{float(v):.3f}"
    except:
        return "NA"

# ============================================================
# INIT GEE  (@st.cache_resource dans gee_ndvi.py)
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
# ============================================================
if uploaded is not None:
    current_file = uploaded.name
    if st.session_state.get("loaded_file") != current_file:
        for key in ["loaded_file", "result_single", "date_single",
                    "available_dates", "features", "bounds"]:
            st.session_state.pop(key, None)
        st.session_state["loaded_file"] = current_file
        st.rerun()
else:
    st.stop()

# ============================================================
# SESSION STATE
# ============================================================
if "result_single" not in st.session_state: st.session_state.result_single = None
if "date_single"   not in st.session_state: st.session_state.date_single   = None
if "available_dates" not in st.session_state: st.session_state.available_dates = None

# ============================================================
# CHARGEMENT VECTEUR  (@st.cache_data dans load_vector)
# ============================================================
features = load_vector(uploaded)
st.success(f"{len(features)} parcelles chargées ✅")

# DEBUG géométries
for f in features[:3]:
    st.write("DEBUG geom bounds :", f["geometry"].bounds)

# ============================================================
# AOI
# ============================================================
geoms = [f["geometry"] for f in features]
minx  = min(g.bounds[0] for g in geoms)
miny  = min(g.bounds[1] for g in geoms)
maxx  = max(g.bounds[2] for g in geoms)
maxy  = max(g.bounds[3] for g in geoms)
aoi   = ee.Geometry.Rectangle([minx, miny, maxx, maxy])

st.write("DEBUG AOI (WGS84):", [minx, miny, maxx, maxy])

# ============================================================
# Clé de cache stable pour les features (bbox + nb parcelles)
# ============================================================
def _features_cache_key(features, minx, miny, maxx, maxy):
    return f"{len(features)}|{minx:.4f},{miny:.4f},{maxx:.4f},{maxy:.4f}"

def _features_geojson(features):
    return [f["geometry"].__geo_interface__ for f in features]

# ============================================================
# CLASSIFICATION NDVI
# ============================================================
def classify_ndvi(nd):
    if nd is None: return ("Données manquantes", "#9e9e9e")
    if nd < 0.25:  return ("Sol nu",             "#d73027")
    if nd < 0.50:  return ("Végétation faible",  "#fee08b")
    return                ("Végétation dense",   "#1a9850")

# Palette couleurs par catégorie
_COLOR_MAP = {
    "Sol nu ou couvert non levé"  : "#d73027",  # rouge
    "Sol nu ou couvert levant"    : "#fdae61",  # orange
    "Couvert en développement"    : "#66bd63",  # vert moyen
    "Couvert établi"              : "#1a9850",  # vert foncé
    "Données manquantes"          : "#9e9e9e",  # gris
}

def colorize(interpretation):
    for key, color in _COLOR_MAP.items():
        if interpretation.startswith(key):
            return color
    return "#9e9e9e"

# ============================================================
# CLASSIFICATION — NDVI seul (one-shot)
# Seuils :
#   < 0.20          → Sol nu ou couvert non levé
#   0.20 – 0.25     → Sol nu ou couvert levant  (zone limite)
#   0.25 – 0.50     → Couvert en développement
#   ≥ 0.50          → Couvert établi
# ============================================================
def classify_state(nd):
    """Retourne (interpretation: str, couvert: bool|None)"""
    if nd is None:
        return "Données manquantes", None
    if nd < 0.20:
        return "Sol nu ou couvert non levé", False
    if nd < 0.25:
        return "Sol nu ou couvert levant", None   # indéterminé
    if nd < 0.50:
        return "Couvert en développement", True
    return "Couvert établi", True


# ============================================================
# SÉLECTEUR TUILE
# Modes : Dernière tuile | Recherche par période
# ============================================================
def tuile_selector():

    mode = st.radio(
        "Choisir la tuile",
        ["Dernière tuile disponible", "Recherche par mois"],
        key="mode_tuile"
    )

    if mode == "Dernière tuile disponible":
        if st.button("Charger la dernière tuile"):
            return get_latest_s2_image(aoi, features)
        return None, None

    # ── Recherche par mois ──────────────────────────────────
    col1, col2 = st.columns(2)

    with col1:
        year = st.selectbox(
            "Année",
            list(range(2017, datetime.date.today().year + 1))[::-1],
            key="year_tuile"
        )

    month_list = [
        ("01","Janvier"),("02","Février"),("03","Mars"),("04","Avril"),
        ("05","Mai"),("06","Juin"),("07","Juillet"),("08","Août"),
        ("09","Septembre"),("10","Octobre"),("11","Novembre"),("12","Décembre")
    ]
    with col2:
        month_num, _ = st.selectbox(
            "Mois",
            month_list,
            key="month_tuile",
            format_func=lambda x: x[1]
        )

    start = f"{year}-{month_num}-01"
    end   = f"{year+1}-01-01" if month_num == "12" else f"{year}-{int(month_num)+1:02d}-01"

    if st.button("Rechercher les dates disponibles"):
        cache_key = _features_cache_key(features, minx, miny, maxx, maxy)
        geojson   = _features_geojson(features)
        dates     = get_available_s2_dates(aoi, cache_key, geojson, start=start, end=end)
        if not dates:
            st.error("❌ Aucune tuile disponible sur cette période.")
            st.session_state.available_dates = None
        else:
            st.session_state.available_dates = dates

    if st.session_state.get("available_dates"):
        selected = st.selectbox(
            "Date disponible",
            st.session_state.available_dates,
            key="sel_date",
            format_func=lambda d: d.strftime("%Y-%m-%d")
        )
        if st.button("Charger cette date"):
            return get_closest_s2_image(aoi, selected, features)

    return None, None


# ============================================================
# ANALYSE NDVI + EVI2
# ============================================================
st.header("🟩 Analyse NDVI — 1 Date")

img, d = tuile_selector()

# DEBUG footprint
if img is not None:
    try:
        st.write("DEBUG S2 footprint :", img.geometry().bounds().getInfo())
    except Exception as e:
        st.error(f"Erreur debug footprint : {e}")

if img is not None and d is not None:

    st.session_state.date_single = d

    ndvi     = compute_ndvi(img)
    evi2     = compute_evi2(img)
    veg_mask = compute_vegetation_mask(ndvi, 0.25)

    # DEBUG pixels (premier ilot uniquement)
    try:
        geom_ee_first = ee.Geometry(features[0]["geometry"].__geo_interface__)
        px = ndvi.sample(region=geom_ee_first, scale=10).size().getInfo()
        st.write("DEBUG pixels (1er ilot) :", px)
    except Exception as e:
        st.write("DEBUG pixels erreur :", str(e))

    with st.spinner("Calcul des stats zonales en cours…"):
        try:
            stats = zonal_stats_all(ndvi, evi2, features)
        except Exception as e:
            st.error(f"❌ Erreur zonal_stats_all : {e}")
            st.stop()

    rows = []
    for feat, s_row in zip(features, stats):
        num_ilot    = feat["properties"].get("NUM_ILOT", "ILOT")
        nd_mean     = s_row["nd_mean"]
        evi2_mean   = s_row["evi2_mean"]
        quality_pct = s_row["quality_pct"]

        if quality_pct is not None and quality_pct < 50:
            interpretation = "Données manquantes (nuages)"
            couvert        = None
        else:
            interpretation, couvert = classify_state(nd_mean)

        rows.append({
            "NUM_ILOT"       : num_ilot,
            "NDVI_moyen"     : round(nd_mean,   3) if nd_mean   is not None else None,
            "EVI2_moyen"     : round(evi2_mean, 3) if evi2_mean is not None else None,
            "Interpretation" : interpretation,
            "Couvert"        : "✅ Oui" if couvert is True else ("❌ Non" if couvert is False else "—"),
            "Qualite_pixels" : f"{quality_pct}%" if quality_pct is not None else "NA",
            "Date"           : str(d),
        })

    st.session_state.result_single = pd.DataFrame(rows)
    # Conserver quality_pct numériquement pour la carte (hors tableau affiché)
    st.session_state.stats_raw = stats

# ============================================================
# AFFICHAGE + CSV + CARTE
# ============================================================
if st.session_state.result_single is not None:

    df         = st.session_state.result_single
    stats_raw  = st.session_state.get("stats_raw", [])

    st.success(f"✅ Résultats NDVI — {st.session_state.date_single}")
    st.dataframe(df)

    # EXPORT CSV
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
        geom         = feat["geometry"]
        row          = df.iloc[idx]
        interp       = row["Interpretation"]
        color        = colorize(interp)

        tooltip_html = (
            f"<b>Ilot :</b> {row['NUM_ILOT']}<br>"
            f"<b>Interprétation :</b> {interp}<br>"
            f"<b>Couvert :</b> {row['Couvert']}<br>"
            f"<b>NDVI :</b> {fmt(row['NDVI_moyen'])}<br>"
            f"<b>EVI2 :</b> {fmt(row['EVI2_moyen'])}<br>"
            f"<b>Qualité pixels :</b> {row['Qualite_pixels']}"
        )

        folium.GeoJson(
            geom.__geo_interface__,
            style_function=lambda x, col=color: {
                "fillColor"  : col,
                "color"      : "black",
                "weight"     : 1,
                "fillOpacity": 0.7
            },
            tooltip=tooltip_html
        ).add_to(m)

    st_folium(m, height=600)

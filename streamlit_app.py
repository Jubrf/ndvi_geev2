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

def colorize(nd, quality_pct):
    """Gris foncé si nuages (nd None ou qualité < 50%), sinon couleur NDVI."""
    if nd is None or (quality_pct is not None and quality_pct < 50):
        return "#9e9e9e"
    if nd < 0.25:  return "#d73027"
    if nd < 0.50:  return "#fee08b"
    return                "#1a9850"

# ============================================================
# INTERPRÉTATION NDVI + EVI2
# ============================================================
def interpret_ndvi_evi(nd, evi):
    if nd is None or evi is None:
        return "Indéterminé"
    if nd < 0.10 and evi < 0.07:
        return "Sol nu / résidus"
    if nd < 0.20 and abs(nd - evi) < 0.05:
        return "Levée végétale (blé/orge)"
    if nd < 0.20 and nd > evi * 1.8:
        return "Sol clair / résidus"
    if nd > 0.80:
        return "Couvert très dense (saturation NDVI)"
    if nd >= 0.25:
        return "Couvert végétal présent"
    return "Indéterminé"


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
            stats = zonal_stats_all(ndvi, evi2, veg_mask, features)
        except Exception as e:
            st.error(f"❌ Erreur zonal_stats_all : {e}")
            st.stop()

    rows = []
    for feat, s in zip(features, stats):
        num_ilot    = feat["properties"].get("NUM_ILOT", "ILOT")
        nd_mean     = s["nd_mean"]
        evi2_mean   = s["evi2_mean"]
        quality_pct = s["quality_pct"]

        classe_txt, _ = classify_ndvi(nd_mean)
        interpretation = interpret_ndvi_evi(nd_mean, evi2_mean)

        rows.append({
            "NUM_ILOT"       : num_ilot,
            "NDVI_moyen"     : nd_mean,
            "EVI2_moyen"     : evi2_mean,
            "Interprétation" : interpretation,
            "Classe"         : classe_txt,
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
        geom        = feat["geometry"]
        nd          = df.iloc[idx]["NDVI_moyen"]
        quality_pct = stats_raw[idx]["quality_pct"] if idx < len(stats_raw) else None
        color       = colorize(nd, quality_pct)

        if nd is None or (quality_pct is not None and quality_pct < 50):
            ndvi_txt  = "NA (nuages)"
            evi2_txt  = "NA"
            interp    = "Données manquantes (couverture nuageuse)"
            classe    = "—"
        else:
            ndvi_txt  = fmt(nd)
            evi2_txt  = fmt(df.iloc[idx]["EVI2_moyen"])
            interp    = df.iloc[idx]["Interprétation"]
            classe    = df.iloc[idx]["Classe"]

        tooltip_html = (
            f"<b>Ilot :</b> {df.iloc[idx]['NUM_ILOT']}<br>"
            f"<b>NDVI :</b> {ndvi_txt}<br>"
            f"<b>EVI2 :</b> {evi2_txt}<br>"
            f"<b>Classe :</b> {classe}<br>"
            f"<b>Interprétation :</b> {interp}<br>"
            f"<b>Qualité pixels :</b> {df.iloc[idx]['Qualite_pixels']}"
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

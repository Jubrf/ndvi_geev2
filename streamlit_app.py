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
# UTILITAIRES
# ============================================================
def fmt(v):
    try:
        return f"{float(v):.3f}"
    except:
        return "NA"

def _features_cache_key(features, minx, miny, maxx, maxy):
    return f"{len(features)}|{minx:.4f},{miny:.4f},{maxx:.4f},{maxy:.4f}"

def _features_geojson(features):
    return [f["geometry"].__geo_interface__ for f in features]

# ============================================================
# CLASSIFICATION — NDVI seul
#   < 0.20      → Sol nu ou couvert non levé
#   0.20–0.25   → Sol nu ou couvert levant  (zone limite)
#   0.25–0.50   → Couvert en développement
#   ≥ 0.50      → Couvert établi
# ============================================================
_COLOR_MAP = {
    "Sol nu ou couvert non levé" : "#d73027",
    "Sol nu ou couvert levant"   : "#fdae61",
    "Couvert en développement"   : "#66bd63",
    "Couvert établi"             : "#1a9850",
    "Données manquantes"         : "#9e9e9e",
}

def classify_state(nd):
    """Retourne (interpretation: str, couvert: bool|None)"""
    if nd is None:
        return "Données manquantes", None
    if nd < 0.20:
        return "Sol nu ou couvert non levé", False
    if nd < 0.25:
        return "Sol nu ou couvert levant", None
    if nd < 0.50:
        return "Couvert en développement", True
    return "Couvert établi", True

def colorize(interpretation):
    for key, color in _COLOR_MAP.items():
        if interpretation.startswith(key):
            return color
    return "#9e9e9e"

# ============================================================
# TENDANCE TEMPORELLE
#   Δ total (1ère → dernière date propre par parcelle)
#   > +0.10  → Hausse
#   < -0.05  → Baisse
#   sinon    → Stable
# ============================================================
def compute_tendency(ndvi_series):
    """
    ndvi_series : list de (date, ndvi|None, quality_pct|None)
                  triée par date croissante
    Retourne (tendance_str, delta_total|None)
    """
    clean = [
        (d, nd) for d, nd, q in ndvi_series
        if nd is not None and (q is None or q >= 50)
    ]
    if len(clean) < 2:
        return "Indéterminé", None
    delta = clean[-1][1] - clean[0][1]
    if delta > 0.10:
        return "📈 Hausse", round(delta, 3)
    if delta < -0.05:
        return "📉 Baisse", round(delta, 3)
    return "→ Stable", round(delta, 3)

# ============================================================
# INIT GEE
# ============================================================
service_account = st.secrets["GEE_SERVICE_ACCOUNT"]
private_key     = st.secrets["GEE_PRIVATE_KEY"]
init_gee(service_account, private_key)

st.title("🌱 NDVI – Analyse parcellaire Sentinel-2")

# ============================================================
# FILE UPLOAD — commun aux deux onglets
# ============================================================
uploaded = st.file_uploader("📁 Charger un SHP (ZIP) ou GEOJSON", type=["zip", "geojson"])

if uploaded is not None:
    current_file = uploaded.name
    if st.session_state.get("loaded_file") != current_file:
        for key in list(st.session_state.keys()):
            st.session_state.pop(key, None)
        st.session_state["loaded_file"] = current_file
        st.rerun()
else:
    st.stop()

# ============================================================
# CHARGEMENT VECTEUR
# ============================================================
features = load_vector(uploaded)
st.success(f"{len(features)} parcelles chargées ✅")

# DEBUG géométries
for f in features[:3]:
    st.write("DEBUG geom bounds :", f["geometry"].bounds)

# ============================================================
# AOI (commun)
# ============================================================
geoms = [f["geometry"] for f in features]
minx  = min(g.bounds[0] for g in geoms)
miny  = min(g.bounds[1] for g in geoms)
maxx  = max(g.bounds[2] for g in geoms)
maxy  = max(g.bounds[3] for g in geoms)
aoi   = ee.Geometry.Rectangle([minx, miny, maxx, maxy])

st.write("DEBUG AOI (WGS84):", [minx, miny, maxx, maxy])

cache_key = _features_cache_key(features, minx, miny, maxx, maxy)
geojson   = _features_geojson(features)

# ============================================================
# ONGLETS
# ============================================================
tab1, tab2 = st.tabs(["📅 Analyse one-shot", "📈 Analyse temporelle"])


# ╔══════════════════════════════════════════════════════════╗
# ║                   ONGLET 1 — ONE-SHOT                   ║
# ╚══════════════════════════════════════════════════════════╝
with tab1:

    # ── Session state onglet 1 ──────────────────────────────
    if "os_result"        not in st.session_state: st.session_state.os_result        = None
    if "os_date"          not in st.session_state: st.session_state.os_date          = None
    if "os_stats_raw"     not in st.session_state: st.session_state.os_stats_raw     = []
    if "os_avail_dates"   not in st.session_state: st.session_state.os_avail_dates   = None

    st.header("Analyse NDVI — 1 date")

    # ── Sélecteur tuile ─────────────────────────────────────
    mode = st.radio(
        "Mode de sélection",
        ["Dernière tuile disponible", "Recherche par mois"],
        key="os_mode",
        horizontal=True,
    )

    img_os, d_os = None, None

    if mode == "Dernière tuile disponible":
        if st.button("Charger la dernière tuile", key="os_btn_latest"):
            img_os, d_os = get_latest_s2_image(aoi, features)

    else:
        col1, col2 = st.columns(2)
        with col1:
            year_os = st.selectbox(
                "Année", list(range(2017, datetime.date.today().year + 1))[::-1],
                key="os_year"
            )
        month_list = [
            ("01","Janvier"),("02","Février"),("03","Mars"),("04","Avril"),
            ("05","Mai"),("06","Juin"),("07","Juillet"),("08","Août"),
            ("09","Septembre"),("10","Octobre"),("11","Novembre"),("12","Décembre")
        ]
        with col2:
            month_num_os, _ = st.selectbox(
                "Mois", month_list, key="os_month", format_func=lambda x: x[1]
            )

        start_os = f"{year_os}-{month_num_os}-01"
        end_os   = (f"{year_os+1}-01-01" if month_num_os == "12"
                    else f"{year_os}-{int(month_num_os)+1:02d}-01")

        if st.button("Rechercher les dates disponibles", key="os_btn_search"):
            dates = get_available_s2_dates(aoi, cache_key, geojson,
                                           start=start_os, end=end_os)
            st.session_state.os_avail_dates = dates if dates else []

        if st.session_state.os_avail_dates is not None:
            if not st.session_state.os_avail_dates:
                st.error("❌ Aucune tuile disponible sur cette période.")
            else:
                selected_os = st.selectbox(
                    f"{len(st.session_state.os_avail_dates)} date(s) disponible(s)",
                    st.session_state.os_avail_dates,
                    key="os_sel_date",
                    format_func=lambda d: d.strftime("%Y-%m-%d"),
                )
                if st.button("Charger cette date", key="os_btn_load"):
                    img_os, d_os = get_closest_s2_image(aoi, selected_os, features)

    # ── DEBUG footprint ─────────────────────────────────────
    if img_os is not None:
        try:
            st.write("DEBUG S2 footprint :", img_os.geometry().bounds().getInfo())
        except Exception as e:
            st.error(f"Erreur debug footprint : {e}")

    # ── Calcul ──────────────────────────────────────────────
    if img_os is not None and d_os is not None:
        st.session_state.os_date = d_os

        ndvi_os = compute_ndvi(img_os)
        evi2_os = compute_evi2(img_os)
        veg_mask_os = compute_vegetation_mask(ndvi_os, 0.25)

        # DEBUG pixels
        try:
            geom_ee_first = ee.Geometry(features[0]["geometry"].__geo_interface__)
            px = ndvi_os.sample(region=geom_ee_first, scale=10).size().getInfo()
            st.write("DEBUG pixels (1er ilot) :", px)
        except Exception as e:
            st.write("DEBUG pixels erreur :", str(e))

        with st.spinner("Calcul des stats zonales…"):
            try:
                stats_os = zonal_stats_all(ndvi_os, evi2_os, features)
            except Exception as e:
                st.error(f"❌ Erreur zonal_stats_all : {e}")
                st.stop()

        rows_os = []
        for feat, s in zip(features, stats_os):
            num_ilot    = feat["properties"].get("NUM_ILOT", "ILOT")
            nd_mean     = s["nd_mean"]
            evi2_mean   = s["evi2_mean"]
            quality_pct = s["quality_pct"]

            if quality_pct is not None and quality_pct < 50:
                interpretation = "Données manquantes (nuages)"
                couvert        = None
            else:
                interpretation, couvert = classify_state(nd_mean)

            rows_os.append({
                "NUM_ILOT"       : num_ilot,
                "NDVI_moyen"     : round(nd_mean,   3) if nd_mean   is not None else None,
                "EVI2_moyen"     : round(evi2_mean, 3) if evi2_mean is not None else None,
                "Interpretation" : interpretation,
                "Couvert"        : "✅ Oui" if couvert is True else ("❌ Non" if couvert is False else "—"),
                "Qualite_pixels" : f"{quality_pct}%" if quality_pct is not None else "NA",
                "Date"           : str(d_os),
            })

        st.session_state.os_result   = pd.DataFrame(rows_os)
        st.session_state.os_stats_raw = stats_os

    # ── Affichage ────────────────────────────────────────────
    if st.session_state.os_result is not None:
        df_os     = st.session_state.os_result
        stats_raw = st.session_state.os_stats_raw

        st.success(f"✅ Résultats — {st.session_state.os_date}")
        st.dataframe(df_os, use_container_width=True)

        csv_os = df_os.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        st.download_button(
            "⬇️ Exporter CSV",
            data=csv_os,
            file_name=f"ndvi_oneshot_{st.session_state.os_date}.csv",
            mime="text/csv",
            key="os_dl",
        )

        # Carte
        m_os = folium.Map(location=[(miny+maxy)/2, (minx+maxx)/2], zoom_start=14)
        for idx, feat in enumerate(features):
            row   = df_os.iloc[idx]
            color = colorize(row["Interpretation"])
            tooltip_html = (
                f"<b>Ilot :</b> {row['NUM_ILOT']}<br>"
                f"<b>Interprétation :</b> {row['Interpretation']}<br>"
                f"<b>Couvert :</b> {row['Couvert']}<br>"
                f"<b>NDVI :</b> {fmt(row['NDVI_moyen'])}<br>"
                f"<b>EVI2 :</b> {fmt(row['EVI2_moyen'])}<br>"
                f"<b>Qualité pixels :</b> {row['Qualite_pixels']}"
            )
            folium.GeoJson(
                feat["geometry"].__geo_interface__,
                style_function=lambda x, col=color: {
                    "fillColor": col, "color": "black",
                    "weight": 1, "fillOpacity": 0.7
                },
                tooltip=tooltip_html,
            ).add_to(m_os)
        st_folium(m_os, height=500, key="os_map")


# ╔══════════════════════════════════════════════════════════╗
# ║                ONGLET 2 — ANALYSE TEMPORELLE            ║
# ╚══════════════════════════════════════════════════════════╝
with tab2:

    # ── Session state onglet 2 ──────────────────────────────
    if "mt_avail_dates"  not in st.session_state: st.session_state.mt_avail_dates  = None
    if "mt_sel_dates"    not in st.session_state: st.session_state.mt_sel_dates    = []
    if "mt_result_long"  not in st.session_state: st.session_state.mt_result_long  = None
    if "mt_result_pivot" not in st.session_state: st.session_state.mt_result_pivot = None

    st.header("Analyse NDVI — Série temporelle")

    # ── Étape 1 : sélection plage de dates ──────────────────
    st.subheader("1. Définir la plage d'analyse")

    col_d1, col_d2 = st.columns(2)
    today = datetime.date.today()
    with col_d1:
        date_start = st.date_input(
            "Date début",
            value=today - datetime.timedelta(days=60),
            max_value=today,
            key="mt_date_start",
        )
    with col_d2:
        date_end = st.date_input(
            "Date fin",
            value=today,
            max_value=today,
            key="mt_date_end",
        )

    if date_start >= date_end:
        st.error("❌ La date de début doit être antérieure à la date de fin.")
        st.stop()

    if st.button("🔍 Rechercher les dates disponibles", key="mt_btn_search"):
        with st.spinner("Interrogation GEE…"):
            dates = get_available_s2_dates(
                aoi, cache_key, geojson,
                start=str(date_start), end=str(date_end)
            )
        if not dates:
            st.error("❌ Aucune tuile Sentinel-2 sur cette période.")
            st.session_state.mt_avail_dates = []
            st.session_state.mt_sel_dates   = []
        else:
            st.session_state.mt_avail_dates = dates
            st.session_state.mt_sel_dates   = dates  # toutes sélectionnées par défaut

    # ── Étape 2 : sélection des dates à analyser ────────────
    if st.session_state.mt_avail_dates is not None:
        if not st.session_state.mt_avail_dates:
            st.info("Aucune date disponible sur cette période.")
        else:
            st.subheader("2. Sélectionner les dates à analyser")
            st.caption(f"{len(st.session_state.mt_avail_dates)} date(s) trouvée(s) — décocher pour exclure")

            sel_dates = st.multiselect(
                "Dates disponibles",
                options=st.session_state.mt_avail_dates,
                default=st.session_state.mt_avail_dates,
                format_func=lambda d: d.strftime("%Y-%m-%d"),
                key="mt_multisel",
            )
            st.session_state.mt_sel_dates = sel_dates

            # ── Étape 3 : lancer l'analyse ───────────────────
            if sel_dates:
                st.subheader("3. Lancer l'analyse")
                n_dates   = len(sel_dates)
                n_parcels = len(features)
                st.caption(f"{n_dates} date(s) × {n_parcels} parcelles — ~{n_dates * 4}–{n_dates * 6}s estimé")

                if st.button("▶️ Lancer l'analyse temporelle", key="mt_btn_run"):

                    progress_bar = st.progress(0, text="Initialisation…")
                    rows_long    = []

                    for i, date in enumerate(sorted(sel_dates)):
                        date_str = str(date)
                        progress_bar.progress(
                            i / n_dates,
                            text=f"Traitement {date_str} ({i+1}/{n_dates})…"
                        )

                        # Chargement image
                        img_mt, d_mt = get_closest_s2_image(aoi, date, features)

                        if img_mt is None:
                            # Date sans image valide → toutes les parcelles en NA
                            for feat in features:
                                num_ilot = feat["properties"].get("NUM_ILOT", "ILOT")
                                rows_long.append({
                                    "Date"           : date_str,
                                    "NUM_ILOT"       : num_ilot,
                                    "NDVI_moyen"     : None,
                                    "EVI2_moyen"     : None,
                                    "Qualite_pixels" : None,
                                    "Interpretation" : "Image non disponible",
                                    "Couvert"        : "—",
                                    "Delta_NDVI"     : None,
                                })
                            continue

                        ndvi_mt     = compute_ndvi(img_mt)
                        evi2_mt     = compute_evi2(img_mt)
                        veg_mask_mt = compute_vegetation_mask(ndvi_mt, 0.25)

                        try:
                            stats_mt = zonal_stats_all(ndvi_mt, evi2_mt, features)
                        except Exception as e:
                            st.warning(f"⚠️ Erreur sur {date_str} : {e}")
                            for feat in features:
                                rows_long.append({
                                    "Date"           : date_str,
                                    "NUM_ILOT"       : feat["properties"].get("NUM_ILOT", "ILOT"),
                                    "NDVI_moyen"     : None,
                                    "EVI2_moyen"     : None,
                                    "Qualite_pixels" : None,
                                    "Interpretation" : "Erreur calcul",
                                    "Couvert"        : "—",
                                    "Delta_NDVI"     : None,
                                })
                            continue

                        for feat, s in zip(features, stats_mt):
                            num_ilot    = feat["properties"].get("NUM_ILOT", "ILOT")
                            nd_mean     = s["nd_mean"]
                            evi2_mean   = s["evi2_mean"]
                            quality_pct = s["quality_pct"]

                            # Qualité < 50% : on garde la valeur mais on flag
                            nuageux = quality_pct is not None and quality_pct < 50
                            if nd_mean is None or nuageux:
                                interpretation = "Données manquantes (nuages)" if nuageux else "Données manquantes"
                                couvert        = None
                            else:
                                interpretation, couvert = classify_state(nd_mean)

                            rows_long.append({
                                "Date"           : date_str,
                                "NUM_ILOT"       : num_ilot,
                                "NDVI_moyen"     : round(nd_mean,   3) if nd_mean   is not None else None,
                                "EVI2_moyen"     : round(evi2_mean, 3) if evi2_mean is not None else None,
                                "Qualite_pixels" : f"{quality_pct}%" if quality_pct is not None else "NA",
                                "Interpretation" : interpretation,
                                "Couvert"        : "✅ Oui" if couvert is True else ("❌ Non" if couvert is False else "—"),
                                "Delta_NDVI"     : None,  # calculé en post-traitement
                            })

                    progress_bar.progress(1.0, text="Calcul des deltas…")

                    # ── Delta NDVI (post-traitement Python) ──
                    # Pour chaque parcelle, delta vs date précédente propre
                    # (quality >= 50% et nd_mean non None)
                    df_long = pd.DataFrame(rows_long)
                    df_long["Date"] = pd.to_datetime(df_long["Date"])
                    df_long = df_long.sort_values(["NUM_ILOT", "Date"]).reset_index(drop=True)

                    for ilot in df_long["NUM_ILOT"].unique():
                        mask  = df_long["NUM_ILOT"] == ilot
                        sub   = df_long[mask].copy()
                        # Valeurs propres = nd_mean non None + qualité >= 50%
                        propre = sub[sub["NDVI_moyen"].notna()].copy()
                        # Exclure lignes nuageuses du calcul delta
                        propre = propre[~propre["Interpretation"].str.startswith("Données manquantes")]

                        prev_nd   = None
                        prev_date = None
                        for row_idx, row in propre.iterrows():
                            if prev_nd is not None:
                                delta = round(row["NDVI_moyen"] - prev_nd, 3)
                                df_long.at[row_idx, "Delta_NDVI"] = delta
                            prev_nd   = row["NDVI_moyen"]
                            prev_date = row["Date"]

                    # Reformater date en string pour affichage
                    df_long["Date"] = df_long["Date"].dt.strftime("%Y-%m-%d")

                    # ── Tableau pivot synthèse ────────────────
                    # Une ligne par parcelle, colonnes = dates, valeur = NDVI
                    pivot = df_long.pivot_table(
                        index="NUM_ILOT",
                        columns="Date",
                        values="NDVI_moyen",
                        aggfunc="first",
                    ).reset_index()

                    # Tendance par parcelle
                    tendances = {}
                    for ilot in df_long["NUM_ILOT"].unique():
                        sub = df_long[df_long["NUM_ILOT"] == ilot].sort_values("Date")
                        series = [
                            (row["Date"], row["NDVI_moyen"],
                             float(row["Qualite_pixels"].replace("%","")) if row["Qualite_pixels"] not in ("NA", None) else None)
                            for _, row in sub.iterrows()
                        ]
                        tendance, delta_total = compute_tendency(series)
                        tendances[ilot] = {"Tendance": tendance, "Delta_total": delta_total}

                    pivot["Tendance"]    = pivot["NUM_ILOT"].map(lambda x: tendances[x]["Tendance"])
                    pivot["Delta_total"] = pivot["NUM_ILOT"].map(lambda x: tendances[x]["Delta_total"])

                    st.session_state.mt_result_long  = df_long
                    st.session_state.mt_result_pivot = pivot
                    progress_bar.empty()

            else:
                st.info("Sélectionne au moins une date pour lancer l'analyse.")

    # ── Affichage résultats ──────────────────────────────────
    if st.session_state.mt_result_long is not None:
        df_long  = st.session_state.mt_result_long
        df_pivot = st.session_state.mt_result_pivot

        n_dates_analysees = df_long["Date"].nunique()
        n_parcelles       = df_long["NUM_ILOT"].nunique()
        st.success(f"✅ Analyse terminée — {n_dates_analysees} date(s), {n_parcelles} parcelle(s)")

        # Synthèse (tableau croisé)
        st.subheader("Synthèse — NDVI par parcelle × date")
        st.dataframe(df_pivot, use_container_width=True)

        # Détail dépliable
        with st.expander("📋 Détail complet (toutes les dates × parcelles)", expanded=False):
            st.dataframe(df_long, use_container_width=True)

        # Export CSV format long
        csv_mt = df_long.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        st.download_button(
            "⬇️ Exporter CSV (format long)",
            data=csv_mt,
            file_name=f"ndvi_temporel_{date_start}_{date_end}.csv",
            mime="text/csv",
            key="mt_dl",
        )

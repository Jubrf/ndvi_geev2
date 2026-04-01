import ee
import datetime
import streamlit as st

# ----------------------------------------------------------
# INITIALISATION GEE
# cache_resource : connexion EE partagée pour toute la session,
# jamais réinitialisée sauf redémarrage du process.
# ----------------------------------------------------------
@st.cache_resource
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ----------------------------------------------------------
# GÉOMÉTRIE EXACTE DES PARCELLES
# ----------------------------------------------------------
def _build_geom_ee(features):
    geoms = [f["geometry"] for f in features]
    total_geom = geoms[0]
    for g in geoms[1:]:
        try:
            total_geom = total_geom.union(g)
        except:
            pass
    return ee.Geometry(total_geom.__geo_interface__)


# ----------------------------------------------------------
# COLLECTIONS (ordre de priorité)
# ----------------------------------------------------------
_COLLECTIONS = [
    "COPERNICUS/S2_SR_HARMONIZED",
    "COPERNICUS/S2_SR",
    "COPERNICUS/S2_HARMONIZED",
]

# Valeurs SCL à masquer : ombres (3), nuages moy (8), nuages haute (9), cirrus (10)
_SCL_CLOUD_VALUES = [3, 8, 9, 10]


# ----------------------------------------------------------
# MASQUE NUAGE/OMBRE via SCL
# S'applique uniquement aux collections SR (qui ont la bande SCL).
# Les collections TOA (S2_HARMONIZED) n'ont pas SCL → pas de masque.
# ----------------------------------------------------------
def _apply_scl_mask(img, colname):
    if "SR" not in colname:
        return img  # TOA : pas de SCL disponible

    scl = img.select("SCL")
    cloud_mask = scl.eq(_SCL_CLOUD_VALUES[0])
    for val in _SCL_CLOUD_VALUES[1:]:
        cloud_mask = cloud_mask.Or(scl.eq(val))

    # Masque inversé : 1 = pixel propre, 0 = nuage/ombre
    clean_mask = cloud_mask.Not()
    return img.updateMask(clean_mask)


# ----------------------------------------------------------
# MOSAÏQUE D'UNE DATE DONNÉE (avec masque SCL)
# ----------------------------------------------------------
def _build_mosaic_for_date(features, date_str, colname):
    geom_ee = _build_geom_ee(features)

    start = f"{date_str}T00:00"
    end   = f"{date_str}T23:59"

    col = (
        ee.ImageCollection(colname)
        .filterBounds(geom_ee)
        .filterDate(start, end)
        .map(lambda img: _apply_scl_mask(img, colname))  # ✅ masque SCL
    )

    count = col.size().getInfo()
    if count == 0:
        return None, None

    mosaic = col.mosaic().clip(geom_ee)

    # Vérifier qu'il reste des pixels propres sur les parcelles
    try:
        count_dict = mosaic.select("B4").reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=geom_ee,
            scale=10,
            maxPixels=1e8
        ).getInfo()
        pixel_count = count_dict.get("B4", 0) or 0
        if pixel_count == 0:
            return None, None
    except:
        return None, None

    d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    return mosaic, d


# ----------------------------------------------------------
# CHERCHE LA MOSAÏQUE VALIDE POUR UNE DATE
# ----------------------------------------------------------
def _find_mosaic(features, date_str):
    for colname in _COLLECTIONS:
        mosaic, d = _build_mosaic_for_date(features, date_str, colname)
        if mosaic is not None:
            return mosaic, d
    return None, None


# ----------------------------------------------------------
# DERNIÈRE MOSAÏQUE DISPONIBLE
# ----------------------------------------------------------
def get_latest_s2_image(aoi, features, max_days=30):
    today = datetime.date.today()
    for delta in range(0, max_days + 1):
        d = today - datetime.timedelta(days=delta)
        mosaic, date = _find_mosaic(features, str(d))
        if mosaic is not None:
            return mosaic, date
    return None, None


# ----------------------------------------------------------
# LISTE DES DATES DISPONIBLES
# cache_data : les dates GEE ne changent pas pendant une session.
# features n'est pas hashable directement → on passe une cache_key
# dérivée (bbox arrondie + nb parcelles) calculée côté appelant.
# ----------------------------------------------------------
@st.cache_data(show_spinner="Recherche des dates disponibles…")
def get_available_s2_dates(aoi, features_cache_key, features_geojson, max_days=120):
    """
    features_cache_key : str — clé stable pour le cache (bbox + nb parcelles)
    features_geojson   : list[dict] — géométries __geo_interface__ sérialisées
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=max_days)

    geoms_ee = [ee.Geometry(g) for g in features_geojson]
    geom_ee  = geoms_ee[0]
    for g in geoms_ee[1:]:
        try:
            geom_ee = geom_ee.union(g)
        except:
            pass

    all_dates = set()
    for colname in _COLLECTIONS:
        col = (
            ee.ImageCollection(colname)
            .filterBounds(geom_ee)
            .filterDate(str(start), str(today))
        )
        timestamps = col.aggregate_array("system:time_start").getInfo()
        for t in timestamps:
            all_dates.add(datetime.datetime.fromtimestamp(t / 1000).date())

    return sorted(all_dates, reverse=True)


# ----------------------------------------------------------
# MOSAÏQUE POUR UNE DATE CIBLE (sélection manuelle)
# ----------------------------------------------------------
def get_closest_s2_image(aoi, target_date, features, max_days=120):
    if isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()

    for delta in range(0, max_days + 1):
        d = target_date - datetime.timedelta(days=delta)
        mosaic, date = _find_mosaic(features, str(d))
        if mosaic is not None:
            return mosaic, date

    return None, None


# ----------------------------------------------------------
# NDVI
# ----------------------------------------------------------
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")


# ----------------------------------------------------------
# MASQUE VÉGÉTATION (NDVI > seuil)
# ----------------------------------------------------------
def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

# ----------------------------------------------------------
# EVI2 (Enhanced Vegetation Index 2)
# ----------------------------------------------------------
def compute_evi2(img):
    """
    EVI2 = 2.5 * (NIR - RED) / (NIR + 2.4*RED + 1)
    Adapté Sentinel-2 (B8 = NIR, B4 = RED)
    """
    nir = img.select("B8")
    red = img.select("B4")
    evi2 = nir.subtract(red).divide(
        nir.add(red.multiply(2.4)).add(1)
    ).multiply(2.5)
    return evi2.rename("EVI2")

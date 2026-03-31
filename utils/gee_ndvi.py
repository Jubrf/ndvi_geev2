import ee
import datetime

# ----------------------------------------------------------
# ✅ INITIALISATION GEE
# ----------------------------------------------------------
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ----------------------------------------------------------
# ✅ GÉOMÉTRIE EXACTE DES PARCELLES (utilisée partout)
# ----------------------------------------------------------
def _build_geom_ee(features):
    """
    Construit la géométrie Earth Engine à partir des parcelles Shapely.
    Utilisé systématiquement pour filtrer les collections.
    """
    geoms = [f["geometry"] for f in features]
    total_geom = geoms[0]
    for g in geoms[1:]:
        try:
            total_geom = total_geom.union(g)
        except:
            pass
    return ee.Geometry(total_geom.__geo_interface__)


# ----------------------------------------------------------
# ✅ LISTE DES COLLECTIONS
# ----------------------------------------------------------
_COLLECTIONS = [
    "COPERNICUS/S2_SR_HARMONIZED",  # priorité : SR harmonisé (le plus complet)
    "COPERNICUS/S2_SR",             # legacy SR (centre Alsace)
    "COPERNICUS/S2_HARMONIZED",     # TOA harmonisé
]


# ----------------------------------------------------------
# ✅ MOSAÏQUE D'UNE DATE DONNÉE
# Fusionne toutes les tuiles d'une même date qui couvrent les parcelles.
# ----------------------------------------------------------
def _build_mosaic_for_date(features, date_str, colname):
    geom_ee = _build_geom_ee(features)

    start = f"{date_str}T00:00"
    end   = f"{date_str}T23:59"

    col = (
        ee.ImageCollection(colname)
        .filterBounds(geom_ee)
        .filterDate(start, end)
    )

    count = col.size().getInfo()
    if count == 0:
        return None, None

    mosaic = col.mosaic().clip(geom_ee)

    # Vérifier que la mosaïque a bien des pixels sur les parcelles
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
# ✅ CHERCHE LA MOSAÏQUE VALIDE POUR UNE DATE
# ----------------------------------------------------------
def _find_mosaic(features, date_str):
    for colname in _COLLECTIONS:
        mosaic, d = _build_mosaic_for_date(features, date_str, colname)
        if mosaic is not None:
            return mosaic, d
    return None, None


# ----------------------------------------------------------
# ✅ DERNIÈRE MOSAÏQUE DISPONIBLE
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
# ✅ LISTE DES DATES DISPONIBLES
# Filtre sur la géométrie EXACTE des parcelles (pas l'AOI rectangle)
# ----------------------------------------------------------
def get_available_s2_dates(aoi, features, max_days=120):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=max_days)

    geom_ee = _build_geom_ee(features)

    all_dates = set()

    for colname in _COLLECTIONS:
        col = (
            ee.ImageCollection(colname)
            .filterBounds(geom_ee)
            .filterDate(str(start), str(today))
        )
        timestamps = col.aggregate_array("system:time_start").getInfo()
        for t in timestamps:
            d = datetime.datetime.fromtimestamp(t / 1000).date()
            all_dates.add(d)

    return sorted(all_dates, reverse=True)


# ----------------------------------------------------------
# ✅ MOSAÏQUE POUR UNE DATE CIBLE (sélection manuelle)
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
# ✅ NDVI
# ----------------------------------------------------------
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")


# ----------------------------------------------------------
# ✅ MASQUE NDVI > seuil
# ----------------------------------------------------------
def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

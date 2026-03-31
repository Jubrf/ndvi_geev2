import ee
import datetime

# ============================================================
# ✅ INIT GEE
# ============================================================
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ============================================================
# ✅ Détection automatique de la dalle Sentinel-2 couvrant les parcelles
# ============================================================
def detect_mgrs_tile(aoi):
    """
    Retourne automatiquement la dalle Sentinel-2 (MGRS_TILE)
    correspondant au centroïde de l'AOI.

    Dataset utilisé : grille officielle Sentinel-2 MGRS
    fournie par Google et accessible à tous :
    "projects/sat-io/open-datasets/MGRS"
    """

    centroid = aoi.centroid()

    # ✅ Grille Sentinel-2 officielle, publique, fiable
    mgrs = ee.FeatureCollection("projects/sat-io/open-datasets/MGRS")

    # Trouver la dalle contenant le centroïde
    feature = mgrs.filterBounds(centroid).first()

    # Aucun résultat : retourner None
    if feature is None:
        return None

    # ✅ Champ correct = "name"
    tile = feature.get("name")
    return tile


# ============================================================
# ✅ Dernière image Sentinel-2 de la dalle correspondant aux parcelles
# ============================================================
def get_latest_s2_image(aoi):
    tile = detect_mgrs_tile(aoi)
    if tile is None:
        return None, None

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filter(ee.Filter.eq("MGRS_TILE", tile))
        .sort("system:time_start", False)
    )

    img = col.first()
    if img is None:
        return None, None

    ts = img.get("system:time_start").getInfo()
    d = datetime.datetime.fromtimestamp(ts / 1000).date()

    return img, d


# ============================================================
# ✅ Liste des dates disponibles pour la dalle Sentinel détectée
# ============================================================
def get_available_s2_dates(aoi, limit=200):
    tile = detect_mgrs_tile(aoi)
    if tile is None:
        return []

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filter(ee.Filter.eq("MGRS_TILE", tile))
        .sort("system:time_start", False)
        .limit(limit)
    )

    timestamps = col.aggregate_array("system:time_start").getInfo()
    if not timestamps:
        return []

    dates = sorted(
        [datetime.datetime.fromtimestamp(t / 1000).date() for t in timestamps],
        reverse=True
    )

    return dates


# ============================================================
# ✅ Image Sentinel la plus proche d’une date donnée
# ============================================================
def get_closest_s2_image(aoi, date):
    tile = detect_mgrs_tile(aoi)
    if tile is None:
        return None, None

    # Fenêtre de 30 jours
    start = date - datetime.timedelta(days=15)
    end   = date + datetime.timedelta(days=15)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filter(ee.Filter.eq("MGRS_TILE", tile))
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .sort("system:time_start", False)
    )

    img = col.first()
    if img is None:
        return None, None

    ts = img.get("system:time_start").getInfo()
    d = datetime.datetime.fromtimestamp(ts / 1000).date()

    return img, d


# ============================================================
# ✅ NDVI & masque NDVI > 0.25 (inchangé)
# ============================================================
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")

def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

import ee
import datetime

# ============================================================
# ✅ INIT GEE
# ============================================================
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ============================================================
# ✅ Fonction interne : détection AUTOMATIQUE de la dalle MGRS
# ============================================================
def detect_mgrs_tile(aoi):
    """
    Détermine automatiquement la dalle Sentinel-2 (MGRS_TILE)
    correspondant au centroïde du SHP.
    """
    # Centroïde WGS84 des parcelles
    centroid = aoi.centroid()

    # Grille officielle des dalles Sentinel-2
    # ⚠️ Très stable, maintenue par Google
    mgrs = ee.FeatureCollection("users/soi/MGRS_tiles")

    # Trouver la dalle qui contient le centroïde
    tile = mgrs.filterBounds(centroid).first().get("Name")

    # Renvoie une chaine EE (string)
    return tile


# ============================================================
# ✅ Récupération de la dernière tuile Sentinel-2 POUR LA DALLE CORRESPONDANTE
# ============================================================
def get_latest_s2_image(aoi):
    tile = detect_mgrs_tile(aoi)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filter(ee.Filter.eq("MGRS_TILE", tile))
        .sort("system:time_start", False)
    )

    img = col.first()
    if img is None:
        return None, None

    timestamp = img.get("system:time_start").getInfo()
    date = datetime.datetime.fromtimestamp(timestamp/1000).date()

    return img, date


# ============================================================
# ✅ Lister les dates de la dalle Sentinel correspondante
# ============================================================
def get_available_s2_dates(aoi, limit=200):
    tile = detect_mgrs_tile(aoi)

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
        [datetime.datetime.fromtimestamp(t/1000).date() for t in timestamps],
        reverse=True
    )

    return dates


# ============================================================
# ✅ Récupérer l'image la plus proche d'une date donnée
# ============================================================
def get_closest_s2_image(aoi, date):

    tile = detect_mgrs_tile(aoi)

    # J+1 mois pour élargir la recherche
    start = date - datetime.timedelta(days=15)
    end   = date + datetime.timedelta(days=15)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filter(ee.Filter.eq("MGRS_TILE", tile))
        .filterDate(start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"))
        .sort("system:time_start", False)
    )

    img = col.first()
    if img is None:
        return None, None

    timestamp = img.get("system:time_start").getInfo()
    dt = datetime.datetime.fromtimestamp(timestamp/1000).date()

    return img, dt


# ============================================================
# ✅ NDVI & masque vegetation (inchangés)
# ============================================================
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")

def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

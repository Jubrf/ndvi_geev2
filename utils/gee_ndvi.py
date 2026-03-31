import ee
import datetime

# ============================================================
# ✅ INIT GEE
# ============================================================
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ============================================================
# ✅ Détecter automatiquement la dalle Sentinel-2 couvrant l'AOI
# ============================================================
def detect_mgrs_tile(aoi):
    """
    Détecte automatiquement la dalle Sentinel-2 grâce à une
    requête directe sur COPERNICUS/S2_SR, sans aucun dataset externe.
    """
    # Centroïde de l'AOI
    centroid = aoi.centroid()

    # On prend n'importe quelle image Sentinel-2 qui couvre ce point
    any_img = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(centroid)
        .first()
    )

    # Si aucune image ne couvre — très rare
    if any_img is None:
        return None

    tile = any_img.get("MGRS_TILE")
    return tile


# ============================================================
# ✅ Dernière image Sentinel-2 pour la tuile détectée
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
# ✅ Liste des dates disponibles
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

    return sorted(
        [datetime.datetime.fromtimestamp(t/1000).date() for t in timestamps],
        reverse=True
    )


# ============================================================
# ✅ Récupérer l'image la plus proche
# ============================================================
def get_closest_s2_image(aoi, date):
    tile = detect_mgrs_tile(aoi)
    if tile is None:
        return None, None

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
    d = datetime.datetime.fromtimestamp(ts/1000).date()

    return img, d


# ============================================================
# ✅ NDVI & masque vegetation
# ============================================================
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")

def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

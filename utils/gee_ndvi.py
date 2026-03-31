import ee
import datetime

def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)

def compute_ndvi(img):
    return img.normalizedDifference(["B8","B4"]).rename("NDVI")

def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")


# ✅ Détermination automatique de la dalle correcte
def _select_tile(aoi):
    # On analyse la latitude du centre de l’AOI
    bounds = aoi.bounds().coordinates().getInfo()[0]
    miny = min(p[1] for p in bounds)
    maxy = max(p[1] for p in bounds)

    # ✅ Centre Alsace
    if maxy < 48.60:
        return None  # pas de filtre → dalle normale (32UQV/32UQU)

    # ✅ Nord Alsace
    if maxy >= 48.60:
        return "32UPU"

    return None


def get_latest_s2_image(aoi):
    tile = _select_tile(aoi)

    col = ee.ImageCollection("COPERNICUS/S2_SR").filterBounds(aoi)

    if tile:
        col = col.filter(ee.Filter.eq("MGRS_TILE", tile))

    col = col.sort("system:time_start", False)

    img = col.first()
    if img is None:
        return None, None

    ts = img.get("system:time_start").getInfo()
    d = datetime.datetime.fromtimestamp(ts/1000).date()
    return img, d


def get_available_s2_dates(aoi, limit=120):

    tile = _select_tile(aoi)

    col = ee.ImageCollection("COPERNICUS/S2_SR").filterBounds(aoi)

    if tile:
        col = col.filter(ee.Filter.eq("MGRS_TILE", tile))

    col = col.sort("system:time_start", False).limit(limit)

    timestamps = col.aggregate_array("system:time_start").getInfo()
    if not timestamps:
        return []

    return sorted({datetime.datetime.fromtimestamp(t/1000).date() for t in timestamps}, reverse=True)


def get_closest_s2_image(aoi, date):
    tile = _select_tile(aoi)

    start = date - datetime.timedelta(days=15)
    end   = date + datetime.timedelta(days=15)

    col = ee.ImageCollection("COPERNICUS/S2_SR") \
        .filterBounds(aoi) \
        .filterDate(str(start), str(end))

    if tile:
        col = col.filter(ee.Filter.eq("MGRS_TILE", tile))

    col = col.sort("system:time_start", False)

    img = col.first()
    if img is None:
        return None, None

    ts = img.get("system:time_start").getInfo()
    d = datetime.datetime.fromtimestamp(ts/1000).date()

    return img, d

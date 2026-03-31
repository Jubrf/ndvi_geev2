import ee
import datetime

# ============================================================
# ✅ INIT EARTH ENGINE
# ============================================================
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ============================================================
# ✅ FONCTION CLEF : TROUVER UNE IMAGE QUI A VRAIMENT DES PIXELS
# ============================================================
def _find_valid_image(aoi, collection, max_tests=15):
    """
    Parcourt les dernières images et retourne la première
    contenant vraiment des pixels SENTINEL-2 sur la zone.
    """

    imgs = collection.toList(max_tests)

    for i in range(max_tests):
        try:
            img = ee.Image(imgs.get(i))

            # Test réel : y a-t-il des pixels B4 dans l'AOI ?
            px = img.select("B4").sample(region=aoi, scale=20).size().getInfo()

            if px > 0:
                ts = img.get("system:time_start").getInfo()
                d  = datetime.datetime.fromtimestamp(ts/1000).date()
                return img, d

        except Exception:
            pass

    return None, None


# ============================================================
# ✅ DERNIÈRE IMAGE SENTINEL UTILE
# ============================================================
def get_latest_s2_image(aoi):

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(aoi)
        .sort("system:time_start", False)
    )

    return _find_valid_image(aoi, col)


# ============================================================
# ✅ LISTE DES DATES
# ============================================================
def get_available_s2_dates(aoi, limit=100):

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(aoi)
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
# ✅ IMAGE LA PLUS PROCHE D'UNE DATE
# ============================================================
def get_closest_s2_image(aoi, date):

    start = date - datetime.timedelta(days=15)
    end   = date + datetime.timedelta(days=15)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(aoi)
        .filterDate(start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"))
        .sort("system:time_start", False)
    )

    return _find_valid_image(aoi, col)


# ============================================================
# ✅ NDVI
# ============================================================
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")


# ============================================================
# ✅ Masque NDVI > 0.25
# ============================================================
def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

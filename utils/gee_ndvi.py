import ee
import datetime

# ----------------------------------------------------------
# ✅ INITIALISATION GEE
# ----------------------------------------------------------
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ----------------------------------------------------------
# ✅ TEST SI UNE IMAGE A VRAIMENT DES PIXELS SUR LES PARCELLES
# ----------------------------------------------------------
def _has_pixels(img, geom):
    """
    Vérifie si l'image contient des pixels (bandes B4) 
    dans la géométrie du SHP (pas seulement l'AOI).
    """
    try:
        px = img.select("B4").sample(region=geom, scale=10).size().getInfo()
        return (px is not None) and (px > 0)
    except:
        return False


# ----------------------------------------------------------
# ✅ LISTE DES COLLECTIONS À TESTER (COMPLET + FIABLE)
# ----------------------------------------------------------
_COLLECTIONS = [
    "COPERNICUS/S2_SR",          # legacy SR (utile pour le centre Alsace)
    "COPERNICUS/S2",             # TOA (tuiles complètes pour le nord)
    "COPERNICUS/S2_HARMONIZED",  # acquisitions récentes
]


# ----------------------------------------------------------
# ✅ CHERCHE L'IMAGE UTILISABLE (CENTRE + NORD)
# ----------------------------------------------------------
def _find_image(features, start=None, end=None, limit=60):

    # ✅ géométrie EXACTE des parcelles (pas AOI)
    geoms = [f["geometry"] for f in features]

    # fusion des parcelles → géométrie totale
    total_geom = geoms[0]
    for g in geoms[1:]:
        try:
            total_geom = total_geom.union(g)
        except:
            pass

    geom_ee = ee.Geometry(total_geom.__geo_interface__)

    for colname in _COLLECTIONS:

        col = ee.ImageCollection(colname).filterBounds(geom_ee)

        if start and end:
            col = col.filterDate(start, end)

        col = col.sort("system:time_start", False).limit(limit)

        imgs = col.toList(limit)

        for i in range(limit):
            try:
                img = ee.Image(imgs.get(i))
                if _has_pixels(img, geom_ee):
                    ts = img.get("system:time_start").getInfo()
                    d = datetime.datetime.fromtimestamp(ts/1000).date()
                    return img, d
            except:
                pass

    return None, None


# ----------------------------------------------------------
# ✅ DERNIÈRE IMAGE
# ----------------------------------------------------------
def get_latest_s2_image(aoi, features, max_days=30):
    today = datetime.date.today()

    for delta in range(0, max_days+1):
        d = today - datetime.timedelta(days=delta)
        start=f"{d}T00:00"
        end=f"{d}T23:59"

        img, date = _find_image(features, start, end)
        if img is not None:
            return img, date

    return None, None


# ----------------------------------------------------------
# ✅ LISTE DES DATES
# ----------------------------------------------------------
def get_available_s2_dates(aoi, features, max_days=120):

    today = datetime.date.today()
    start = today - datetime.timedelta(days=max_days)

    # explorer SR + TOA + HARMONIZED
    col_sr = ee.ImageCollection("COPERNICUS/S2_SR") \
                .filterBounds(aoi) \
                .filterDate(str(start), str(today))

    col_toa = ee.ImageCollection("COPERNICUS/S2") \
                .filterBounds(aoi) \
                .filterDate(str(start), str(today))

    col_hz = ee.ImageCollection("COPERNICUS/S2_HARMONIZED") \
                .filterBounds(aoi) \
                .filterDate(str(start), str(today))

    col = col_sr.merge(col_toa).merge(col_hz).sort("system:time_start", False)

    timestamps = col.aggregate_array("system:time_start").getInfo()

    dates = sorted(
        {datetime.datetime.fromtimestamp(t/1000).date() for t in timestamps},
        reverse=True
    )

    return dates


# ----------------------------------------------------------
# ✅ IMAGE PROCHE
# ----------------------------------------------------------
def get_closest_s2_image(aoi, target_date, features, max_days=120):

    if isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()

    for delta in range(0, max_days+1):

        d = target_date - datetime.timedelta(days=delta)
        start=f"{d}T00:00"
        end=f"{d}T23:59"

        img, dt = _find_image(features, start, end)

        if img is not None:
            return img, dt

    return None, None


# ----------------------------------------------------------
# ✅ NDVI
# ----------------------------------------------------------
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")


# ----------------------------------------------------------
# ✅ masque NDVI > 0.25
# ----------------------------------------------------------
def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

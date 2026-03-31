import ee
import datetime

# ----------------------------------------------------------
# ✅ INITIALISATION GEE
# ----------------------------------------------------------
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ----------------------------------------------------------
# ✅ Tester si une image contient VRAIMENT des pixels sur l'AOI
# ----------------------------------------------------------
def _image_has_pixels(img, aoi):
    try:
        count = img.select("B4").sample(region=aoi, scale=20).size().getInfo()
        return count is not None and count > 0
    except:
        return False


# ----------------------------------------------------------
# ✅ Sélection d’une image UTILISABLE dans 2 collections :
#     - COPERNICUS/S2_SR
#     - COPERNICUS/S2_HARMONIZED
# ----------------------------------------------------------
def _search_valid_image(aoi, start_date=None, end_date=None, limit=40):

    collections = [
        ee.ImageCollection("COPERNICUS/S2_SR"),
        ee.ImageCollection("COPERNICUS/S2"),
        ee.ImageCollection("COPERNICUS/S2_HARMONIZED"),
    ]

    for col in collections:

        col2 = col.filterBounds(aoi)

        if start_date and end_date:
            col2 = col2.filterDate(start_date, end_date)

        col2 = col2.sort("system:time_start", False).limit(limit)

        imgs = col2.toList(limit)

        for i in range(limit):
            try:
                img = ee.Image(imgs.get(i))
                if _image_has_pixels(img, aoi):
                    ts = img.get("system:time_start").getInfo()
                    d = datetime.datetime.fromtimestamp(ts/1000).date()
                    return img, d
            except:
                pass

    return None, None


# ----------------------------------------------------------
# ✅ DERNIÈRE IMAGE UTILISABLE
# ----------------------------------------------------------
def get_latest_s2_image(aoi_geom):

    today = datetime.date.today()

    # On teste sur les 30 derniers jours
    for delta in range(0, 31):
        d = today - datetime.timedelta(days=delta)
        start = f"{d}T00:00"
        end   = f"{d}T23:59"

        img, date = _search_valid_image(aoi_geom, start, end, limit=20)
        if img is not None:
            return img, date

    return None, None


# ----------------------------------------------------------
# ✅ LISTE DES DATES DISPONIBLES
# ----------------------------------------------------------
def get_available_s2_dates(aoi_geom, max_days=120):

    today = datetime.date.today()
    start = today - datetime.timedelta(days=max_days)

    col_hr = ee.ImageCollection("COPERNICUS/S2_HARMONIZED") \
                .filterBounds(aoi_geom) \
                .filterDate(str(start), str(today)) \
                .sort("system:time_start", False)

    col_sr = ee.ImageCollection("COPERNICUS/S2_SR") \
                .filterBounds(aoi_geom) \
                .filterDate(str(start), str(today)) \
                .sort("system:time_start", False)

    col = col_hr.merge(col_sr)

    timestamps = col.aggregate_array("system:time_start").getInfo()

    dates = []
    for t in timestamps:
        d = datetime.datetime.fromtimestamp(t / 1000, datetime.UTC).date()
        if d not in dates:
            dates.append(d)

    return sorted(dates, reverse=True)


# ----------------------------------------------------------
# ✅ IMAGE PROCHE D’UNE DATE
# ----------------------------------------------------------
def get_closest_s2_image(aoi_geom, target_date, max_days=120):

    if isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()

    for delta in range(0, max_days + 1):

        d = target_date - datetime.timedelta(days=delta)

        start = f"{d}T00:00"
        end   = f"{d}T23:59"

        img, date = _search_valid_image(aoi_geom, start, end, limit=20)

        if img is not None:
            return img, date

    return None, None


# ----------------------------------------------------------
# ✅ NDVI
# ----------------------------------------------------------
def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")


# ----------------------------------------------------------
# ✅ Masque NDVI > 0.25
# ----------------------------------------------------------
def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

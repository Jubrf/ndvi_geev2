import ee
import datetime

def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)

# ✅ VERSION D’ORIGINE — NE PAS TOUCHER
def get_latest_s2_image(aoi):
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(aoi)
        .sort("system:time_start", False)
    )
    img = col.first()
    if img is None:
        return None, None

    timestamp = img.get("system:time_start").getInfo()
    date = datetime.datetime.fromtimestamp(timestamp/1000).date()
    return img, date

def get_available_s2_dates(aoi, limit=200):
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(aoi)
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

def get_closest_s2_image(aoi, date):
    start = date - datetime.timedelta(days=15)
    end = date + datetime.timedelta(days=15)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(aoi)
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .sort("system:time_start", False)
    )

    img = col.first()
    if img is None:
        return None, None

    ts = img.get("system:time_start").getInfo()
    d = datetime.datetime.fromtimestamp(ts/1000).date()
    return img, d

def compute_ndvi(img):
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")

def compute_vegetation_mask(ndvi_img, threshold=0.25):
    return ndvi_img.gt(threshold).rename("VEG")

import ee
import datetime

# ----------------------------------------------------------
# ✅ INITIALISATION GEE
# ----------------------------------------------------------
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ----------------------------------------------------------
# ✅ NDVI robuste (gestion automatique des bandes Sentinel‑2)
# ----------------------------------------------------------
def compute_ndvi(img):
    """
    Calcule un NDVI robuste sur S2_SR en détectant automatiquement les bandes RED et NIR.
    Fonctionne même si les bandes s'appellent B4, B04, B4_1, B8, B08, B8A, B8_1, etc.
    """

    # Récupération des noms de bandes disponibles dans l'image
    bands = img.bandNames().getInfo()

    # Candidats pour la bande rouge Sentinel‑2
    red_candidates = ["B4", "B04", "B4_1"]

    # Candidats pour la bande NIR
    nir_candidates = ["B8", "B08", "B8A", "B8_1"]

    # Sélection de la première bande RED existante
    red = next((b for b in red_candidates if b in bands), None)

    # Sélection de la première bande NIR existante
    nir = next((b for b in nir_candidates if b in bands), None)

    # Si aucune bande valide trouvée → NDVI vide mais propre
    if red is None or nir is None:
        # renvoie une image NDVI masquée pour éviter tous les plantages
        return ee.Image.constant(0).rename("NDVI").updateMask(ee.Image.constant(0))

    # Sinon NDVI normalisé Sentinel‑2
    ndvi = img.normalizedDifference([nir, red]).rename("NDVI")

    return ndvi


# ----------------------------------------------------------
# ✅ Masque végétation NDVI > threshold
# ----------------------------------------------------------
def compute_vegetation_mask(ndvi_img, threshold=0.25):
    """Masque végétation NDVI > threshold"""
    return ndvi_img.gt(threshold).rename("VEG")


# ----------------------------------------------------------
# ✅ Récupérer la dernière tuile Sentinel‑2
# ----------------------------------------------------------
def get_latest_s2_image(aoi_geom, max_days=30):
    today = datetime.date.today()

    for delta in range(0, max_days + 1):
        day = today - datetime.timedelta(days=delta)
        start = f"{day}T00:00"
        end = f"{day}T23:59"

        col = (
            ee.ImageCollection("COPERNICUS/S2_SR")
            .filterBounds(aoi_geom)
            .filterDate(start, end)
            .filter("SCL != 9")  # nuages élevés
            .filter("SCL != 8")  # nuages moyens
            .sort("system:time_start", False)
        )

        img = col.first()
        if img.getInfo() is not None:
            return img, day

    return None, None


# ----------------------------------------------------------
# ✅ Liste des dates disponibles dans la zone
# ----------------------------------------------------------
def get_available_s2_dates(aoi_geom, max_days=120):

    today = datetime.date.today()
    start = today - datetime.timedelta(days=max_days)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR")
        .filterBounds(aoi_geom)
        .filterDate(str(start), str(today))
        .sort("system:time_start", False)
    )

    timestamps = col.aggregate_array("system:time_start").getInfo()

    dates = []
    for t in timestamps:
        d = datetime.datetime.fromtimestamp(t / 1000, datetime.UTC).date()
        if d not in dates:
            dates.append(d)

    return sorted(dates, reverse=True)


# ----------------------------------------------------------
# ✅ Récupérer l'image la plus proche d'une date choisie
# ----------------------------------------------------------
def get_closest_s2_image(aoi_geom, target_date, max_days=120):

    if isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()

    for delta in range(0, max_days + 1):

        d = target_date - datetime.timedelta(days=delta)
        start = f"{d}T00:00"
        end = f"{d}T23:59"

        col = (
            ee.ImageCollection("COPERNICUS/S2_SR")
            .filterBounds(aoi_geom)
            .filterDate(start, end)
            .filter("SCL != 9")
            .filter("SCL != 8")
            .sort("system:time_start", False)
        )

        img = col.first()

        if img.getInfo() is not None:
            return img, d

    return None, None

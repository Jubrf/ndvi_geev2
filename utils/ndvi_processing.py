import ee
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import transform


# ============================================================
# Conversion Shapely → Earth Engine
# ============================================================
def shapely_to_ee(geom):
    def strip_z(x, y, z=None):
        return (x, y)

    geom2d = transform(strip_z, geom)

    if isinstance(geom2d, Polygon):
        coords = list(geom2d.exterior.coords)
        return ee.Geometry.Polygon([coords])

    if isinstance(geom2d, MultiPolygon):
        parts = []
        for poly in geom2d.geoms:
            coords = list(poly.exterior.coords)
            parts.append([coords])
        return ee.Geometry.MultiPolygon(parts)

    return None


# ============================================================
# Zonal stats NDVI + EVI2 + qualité pixels — TOUTES LES PARCELLES
# en un seul appel reduceRegions côté GEE.
# ============================================================
def zonal_stats_all(ndvi_img, evi2_img, features):
    """
    ndvi_img  : ee.Image bande "NDVI"
    evi2_img  : ee.Image bande "EVI2"
    features  : list[dict] avec clés "geometry" (Shapely) et "properties"

    Retourne list[dict] :
      { num_ilot, nd_mean, evi2_mean, quality_pct }
    """

    # ----------------------------------------------------------
    # Construction de la FeatureCollection EE
    # ----------------------------------------------------------
    ee_features = []
    for i, feat in enumerate(features):
        geom    = feat["geometry"].buffer(0)
        geom_ee = shapely_to_ee(geom)
        if geom_ee is None:
            continue
        num_ilot = str(feat["properties"].get("NUM_ILOT", f"ILOT_{i}"))
        ee_features.append(
            ee.Feature(geom_ee, {"NUM_ILOT": num_ilot})
        )

    fc = ee.FeatureCollection(ee_features)

    # ----------------------------------------------------------
    # Image multi-bandes pour les moyennes : NDVI + EVI2
    # ----------------------------------------------------------
    stack_mean = (
        ndvi_img.rename("NDVI")
        .addBands(evi2_img.rename("EVI2"))
    )

    # Image pour compter les pixels totaux (masque désactivé)
    # Bande séparée pour éviter les conflits de nommage.
    stack_total = ndvi_img.unmask().rename("NDVI_total")

    # ----------------------------------------------------------
    # Deux appels reduceRegions séparés :
    #   1) mean sur NDVI + EVI2  (pixels valides seulement)
    #   2) count sur NDVI + NDVI_total  (valide vs total)
    # Deux appels évitent les ambiguïtés de nommage du reducer combiné.
    # Le coût reste bien inférieur à N appels individuels.
    # ----------------------------------------------------------
    fc_mean = stack_mean.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        scale=10,
    )

    fc_count_valid = ndvi_img.rename("NDVI").reduceRegions(
        collection=fc,
        reducer=ee.Reducer.count().setOutputs(["count_valid"]),
        scale=10,
    )

    fc_count_total = stack_total.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.count().setOutputs(["count_total"]),
        scale=10,
    )

    # ----------------------------------------------------------
    # Récupération (3 getInfo au lieu de N×3)
    # ----------------------------------------------------------
    info_mean        = fc_mean.getInfo()
    info_count_valid = fc_count_valid.getInfo()
    info_count_total = fc_count_total.getInfo()

    # Index par NUM_ILOT
    def index_by_ilot(info):
        d = {}
        for f in info["features"]:
            key = str(f["properties"].get("NUM_ILOT", "?"))
            d[key] = f["properties"]
        return d

    by_mean        = index_by_ilot(info_mean)
    by_count_valid = index_by_ilot(info_count_valid)
    by_count_total = index_by_ilot(info_count_total)

    # ----------------------------------------------------------
    # Reconstruction dans l'ordre original
    # ----------------------------------------------------------
    output = []
    for feat in features:
        num_ilot = str(feat["properties"].get("NUM_ILOT", "?"))

        m  = by_mean.get(num_ilot, {})
        cv = by_count_valid.get(num_ilot, {})
        ct = by_count_total.get(num_ilot, {})

        nd_mean   = m.get("NDVI",  None)
        evi2_mean = m.get("EVI2",  None)
        c_valid   = cv.get("count_valid", 0) or 0
        c_total   = ct.get("count_total", 0) or 0

        quality_pct = None
        if c_total > 0:
            quality_pct = round((c_valid / c_total) * 100, 1)

        output.append({
            "num_ilot"   : num_ilot,
            "nd_mean"    : float(nd_mean)   if nd_mean   is not None else None,
            "evi2_mean"  : float(evi2_mean) if evi2_mean is not None else None,
            "quality_pct": quality_pct,
        })

    return output


# ============================================================
# Compat shim — conservé, non utilisé en prod.
# ============================================================
def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    geom    = geom.buffer(0)
    geom_ee = shapely_to_ee(geom)
    if geom_ee is None:
        return None, None, None

    nd_local  = ndvi_img.clip(geom_ee)
    mean_dict = nd_local.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=geom_ee, scale=10, maxPixels=1e10
    ).getInfo()
    nd_mean = mean_dict.get("NDVI", None)
    if nd_mean is not None:
        nd_mean = float(nd_mean)

    veg_prop = None
    if veg_mask is not None:
        veg_dict = veg_mask.clip(geom_ee).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=geom_ee, scale=10, maxPixels=1e10
        ).getInfo()
        veg_prop = veg_dict.get("VEG", None)
        if veg_prop is not None:
            veg_prop = float(veg_prop)

    pixel_quality_pct = None
    try:
        c_valid = nd_local.reduceRegion(
            reducer=ee.Reducer.count(), geometry=geom_ee, scale=10, maxPixels=1e10
        ).getInfo().get("NDVI", 0) or 0
        c_total = ndvi_img.unmask().clip(geom_ee).reduceRegion(
            reducer=ee.Reducer.count(), geometry=geom_ee, scale=10, maxPixels=1e10
        ).getInfo().get("NDVI", 0) or 0
        if c_total > 0:
            pixel_quality_pct = round((c_valid / c_total) * 100, 1)
    except:
        pixel_quality_pct = None

    return nd_mean, veg_prop, pixel_quality_pct

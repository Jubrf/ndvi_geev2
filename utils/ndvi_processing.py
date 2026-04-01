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
#
# Retourne : list[dict] avec pour chaque parcelle :
#   {
#     "num_ilot"    : str,
#     "nd_mean"     : float | None,
#     "evi2_mean"   : float | None,
#     "veg_prop"    : float | None,
#     "quality_pct" : float | None,
#   }
# ============================================================
def zonal_stats_all(ndvi_img, evi2_img, veg_mask, features):
    """
    Calcule NDVI moyen, EVI2 moyen, proportion végétation et qualité pixels
    pour toutes les parcelles en un seul appel GEE (reduceRegions).

    ndvi_img  : ee.Image bande "NDVI"
    evi2_img  : ee.Image bande "EVI2"
    veg_mask  : ee.Image bande "VEG"
    features  : list[dict] avec clés "geometry" (Shapely) et "properties"
    """

    # ----------------------------------------------------------
    # Construction de la FeatureCollection EE
    # ----------------------------------------------------------
    ee_features = []
    for i, feat in enumerate(features):
        geom = feat["geometry"].buffer(0)
        geom_ee = shapely_to_ee(geom)
        if geom_ee is None:
            continue
        num_ilot = str(feat["properties"].get("NUM_ILOT", f"ILOT_{i}"))
        ee_features.append(
            ee.Feature(geom_ee, {"NUM_ILOT": num_ilot, "_idx": i})
        )

    fc = ee.FeatureCollection(ee_features)

    # ----------------------------------------------------------
    # Image multi-bandes : NDVI + EVI2 + VEG + NDVI_total (unmasked)
    # NDVI_total sert à compter les pixels totaux pour la qualité.
    # ----------------------------------------------------------
    ndvi_unmasked = ndvi_img.unmask().rename("NDVI_total")

    stack = (
        ndvi_img.rename("NDVI")
        .addBands(evi2_img.rename("EVI2"))
        .addBands(veg_mask.rename("VEG"))
        .addBands(ndvi_unmasked)
    )

    # ----------------------------------------------------------
    # Un seul appel reduceRegions : mean + count en une passe.
    # Le reducer combiné applique mean ET count sur chaque bande.
    # Nommage GEE : {bande}_mean et {bande}_count.
    # ----------------------------------------------------------
    reducer = (
        ee.Reducer.mean()
        .combine(ee.Reducer.count(), sharedInputs=False)
    )

    result_fc = stack.reduceRegions(
        collection=fc,
        reducer=reducer,
        scale=10,
        crs="EPSG:4326",
    )

    # ----------------------------------------------------------
    # Récupération des résultats (un seul getInfo)
    # ----------------------------------------------------------
    results_info = result_fc.getInfo()

    stats_by_ilot = {}
    for feat_info in results_info["features"]:
        props = feat_info["properties"]
        num_ilot = str(props.get("NUM_ILOT", "?"))

        nd_mean     = props.get("NDVI_mean",        None)
        evi2_mean   = props.get("EVI2_mean",         None)
        veg_prop    = props.get("VEG_mean",          None)
        count_valid = props.get("NDVI_count",        0) or 0
        count_total = props.get("NDVI_total_count",  0) or 0

        quality_pct = None
        if count_total > 0:
            quality_pct = round((count_valid / count_total) * 100, 1)

        stats_by_ilot[num_ilot] = {
            "num_ilot"   : num_ilot,
            "nd_mean"    : float(nd_mean)   if nd_mean   is not None else None,
            "evi2_mean"  : float(evi2_mean) if evi2_mean is not None else None,
            "veg_prop"   : float(veg_prop)  if veg_prop  is not None else None,
            "quality_pct": quality_pct,
        }

    # Reconstruction dans l'ordre original des features
    output = []
    for feat in features:
        num_ilot = str(feat["properties"].get("NUM_ILOT", "?"))
        if num_ilot in stats_by_ilot:
            output.append(stats_by_ilot[num_ilot])
        else:
            output.append({
                "num_ilot"   : num_ilot,
                "nd_mean"    : None,
                "evi2_mean"  : None,
                "veg_prop"   : None,
                "quality_pct": None,
            })

    return output


# ============================================================
# Compat shim — ancienne signature, conservée pour sécurité.
# Deprecated : utiliser zonal_stats_all() à la place.
# ============================================================
def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    geom = geom.buffer(0)
    geom_ee = shapely_to_ee(geom)
    if geom_ee is None:
        return None, None, None

    nd_local = ndvi_img.clip(geom_ee)
    mean_dict = nd_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    nd_mean = mean_dict.get("NDVI", None)
    if nd_mean is not None:
        nd_mean = float(nd_mean)

    veg_prop = None
    if veg_mask is not None:
        veg_local = veg_mask.clip(geom_ee)
        veg_dict  = veg_local.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geom_ee,
            scale=10,
            maxPixels=1e10
        ).getInfo()
        veg_prop = veg_dict.get("VEG", None)
        if veg_prop is not None:
            veg_prop = float(veg_prop)

    pixel_quality_pct = None
    try:
        count_valid = nd_local.reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=geom_ee,
            scale=10,
            maxPixels=1e10
        ).getInfo().get("NDVI", 0) or 0

        count_total = ndvi_img.unmask().clip(geom_ee).reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=geom_ee,
            scale=10,
            maxPixels=1e10
        ).getInfo().get("NDVI", 0) or 0

        if count_total > 0:
            pixel_quality_pct = round((count_valid / count_total) * 100, 1)
    except:
        pixel_quality_pct = None

    return nd_mean, veg_prop, pixel_quality_pct

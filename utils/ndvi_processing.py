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
# Zonal stats NDVI + qualité pixels
# ============================================================
def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    """
    Calcule :
    - NDVI moyen sur les pixels valides (non masqués nuage/ombre)
    - Proportion de végétation (NDVI > 0.25)
    - % de pixels valides sur la parcelle (indicateur qualité SCL)

    Retourne (nd_mean, veg_prop, pixel_quality_pct)
    """

    geom = geom.buffer(0)
    geom_ee = shapely_to_ee(geom)
    if geom_ee is None:
        return None, None, None

    # --- NDVI moyen (pixels valides seulement, masque déjà appliqué) ---
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

    # --- Proportion végétation ---
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

    # --- Qualité pixels : % de pixels valides vs total théorique ---
    # On compare le count des pixels NDVI valides (non masqués)
    # au count total en désactivant le masque sur la même zone.
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

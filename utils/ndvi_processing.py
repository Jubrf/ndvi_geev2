import ee
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import transform

def shapely_to_ee(geom):
    """
    Convertit n'importe quel Polygon / MultiPolygon Shapely
    en géométrie Earth Engine valide :
    ✅ enlève les trous
    ✅ supprime la 3ème dimension (Z)
    ✅ découpe les multipolygones en parties valides
    """

    # ---- 1) On vire les coordonnées Z (beaucoup de SHP en ont) ----
    def strip_z(x, y, z=None):
        return (x, y)

    geom2d = transform(strip_z, geom)

    # ---- 2) Polygon simple ----
    if isinstance(geom2d, Polygon):
        exterior = list(geom2d.exterior.coords)
        return ee.Geometry.Polygon([exterior])

    # ---- 3) MultiPolygon ----
    elif isinstance(geom2d, MultiPolygon):
        parts = []
        for poly in geom2d:
            exterior = list(poly.exterior.coords)
            parts.append([exterior])
        return ee.Geometry.MultiPolygon(parts)

    # ---- 4) Cas non géré ----
    return None


def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    """
    Calcule NDVI moyen + proportion NDVI > 0.25
    Fonction 100% compatible Polygon / MultiPolygon complexes
    """

    # ✅ Conversion Shapely -> Earth Engine (robuste)
    geom_ee = shapely_to_ee(geom)

    if geom_ee is None:
        return None, None

    # ✅ NDVI moyen
    mean_dict = ndvi_img.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    ndvi_mean = mean_dict.get("NDVI", None)
    if ndvi_mean is not None:
        ndvi_mean = float(ndvi_mean)

    # ✅ Mode comparaison → pas de végétation
    if veg_mask is None:
        return ndvi_mean, None

    # ✅ Proportion NDVI > threshold
    veg_dict = veg_mask.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    veg_prop = veg_dict.get("VEG", None)
    if veg_prop is not None:
        veg_prop = float(veg_prop)

    return ndvi_mean, veg_prop

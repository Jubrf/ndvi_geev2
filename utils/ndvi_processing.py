import ee
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import transform

def shapely_to_ee(geom):
    """
    Conversion robuste Shapely -> Earth Engine
    Compatible Polygon et MultiPolygon
    """
    from shapely.geometry import Polygon, MultiPolygon

    # assurer 2D
    def strip_z(x, y, z=None): return (x, y)
    geom2d = transform(strip_z, geom)

    if isinstance(geom2d, Polygon):
        exterior = list(geom2d.exterior.coords)
        return ee.Geometry.Polygon([exterior])

    if isinstance(geom2d, MultiPolygon):
        parts = []
        for poly in geom2d.geoms:
            ext = list(poly.exterior.coords)
            parts.append([ext])
        return ee.Geometry.MultiPolygon(parts)

    return None

def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    """
    Calcule :
    ✅ NDVI moyen
    ✅ proportion NDVI > 0.25
    ✅ Version épurée : SHP uniquement
    """

    # ✅ Corrige automatiquement les polygones “légèrement” invalides
    geom = geom.buffer(0)

    # ✅ Conversion Shapely -> Earth Engine
    geom_ee = shapely_to_ee(geom)
    if geom_ee is None:
        return None, None

    # ✅ Clip indispensable pour éviter NDVI=None sur bords de dalle
    nd_local = ndvi_img.clip(geom_ee)
    veg_local = veg_mask.clip(geom_ee) if veg_mask is not None else None

    # ✅ NDVI moyen
    mean_dict = nd_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    nd_mean = mean_dict.get("NDVI", None)
    if nd_mean is not None:
        nd_mean = float(nd_mean)

    # ✅ Si pas de masque végétation (rare dans version épurée)
    if veg_local is None:
        return nd_mean, None

    # ✅ Proportion NDVI > 0.25
    veg_dict = veg_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    veg_prop = veg_dict.get("VEG", None)
    if veg_prop is not None:
        veg_prop = float(veg_prop)

    return nd_mean, veg_prop

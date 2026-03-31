import ee
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import transform

def normalize_multipolygon(struct):
    """
    Aplati la structure de coordonnées GeoJSON
    de n niveaux -> 2 niveaux maximum :
    MultiPolygon = [ [ exterior ], [ exterior2 ] ... ]
    """
    # structure = [[[ [points] ]]] etc.
    # On remove tous les niveaux vides jusqu'à tomber sur la liste des anneaux.
    while isinstance(struct, list) and len(struct) == 1 and isinstance(struct[0], list):
        struct = struct[0]
    return struct


def shapely_to_ee(geom):
    """ Convertit Polygon / MultiPolygon / GeometryCollection en ee.Geometry propre """

    # --- remove Z dimension ---
    def strip_z(x, y, z=None):
        return (x, y)

    geom2d = transform(strip_z, geom)

    # --- Polygon simple ---
    if isinstance(geom2d, Polygon):
        exterior = list(geom2d.exterior.coords)
        return ee.Geometry.Polygon([exterior])

    # --- MultiPolygon ---
    if isinstance(geom2d, MultiPolygon):
        parts = []
        for poly in geom2d.geoms:
            ext = list(poly.exterior.coords)
            parts.append([ext])     # anneau unique, sans trous
        return ee.Geometry.MultiPolygon(parts)

    # --- GeometryCollection ---
    if isinstance(geom2d, GeometryCollection):
        parts = []
        for sub in geom2d.geoms:
            if isinstance(sub, Polygon):
                parts.append([list(sub.exterior.coords)])
            elif isinstance(sub, MultiPolygon):
                for poly in sub.geoms:
                    parts.append([list(poly.exterior.coords)])
        if len(parts) == 1:
            return ee.Geometry.Polygon(parts[0])
        elif len(parts) > 1:
            return ee.Geometry.MultiPolygon(parts)

    return None


def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    """ NDVI moyen + proportion NDVI > 0.25 """

    # ✅ Répare les polygones si besoin
    geom = geom.buffer(0)

    # ✅ Convertit Shapely -> EE
    geom_ee = shapely_to_ee(geom)

    if geom_ee is None:
        return None, None

    # ✅ Clip indispensable
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

    if veg_local is None:
        return nd_mean, None

    # ✅ Proportion NDVI > 0.25
    veg_dict = veg_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    vprop = veg_dict.get("VEG", None)
    if vprop is not None:
        vprop = float(vprop)

    return nd_mean, vprop

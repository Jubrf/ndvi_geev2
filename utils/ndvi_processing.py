import ee
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import transform

def shapely_to_ee(geom):
    """
    Convertit Polygon, MultiPolygon ou GeometryCollection en ee.Geometry
    ✅ sans jamais boucler sur un objet non-itérable
    ✅ compatible GeoJSON WGS84
    """

    # 🔹 Retirer Z si présent
    def strip_z(x, y, z=None):
        return (x, y)

    geom2d = transform(strip_z, geom)

    # ---------- CASE 1 : POLYGON ----------
    if isinstance(geom2d, Polygon):
        coords = list(geom2d.exterior.coords)
        return ee.Geometry.Polygon([coords])

    # ---------- CASE 2 : MULTIPOLYGON ----------
    if isinstance(geom2d, MultiPolygon):
        parts = []
        for poly in geom2d.geoms:
            parts.append([list(poly.exterior.coords)])
        return ee.Geometry.MultiPolygon(parts)

    # ---------- CASE 3 : GEOMETRY COLLECTION ----------
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
        if len(parts) > 1:
            return ee.Geometry.MultiPolygon(parts)

    # ---------- Other → cannot convert ----------
    return None


def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    """
    Calcule :
    ✅ NDVI moyen
    ✅ proportion NDVI > 0.25
    ✅ compatible Polygon / MultiPolygon / GeometryCollection
    """

    geom_ee = shapely_to_ee(geom)

    if geom_ee is None:
        return None, None

    # ✅ CLIP obligatoire pour éviter NDVI=None
    ndvi_local = ndvi_img.clip(geom_ee)
    veg_local = veg_mask.clip(geom_ee) if veg_mask is not None else None

    # ✅ NDVI moyen
    mean_dict = ndvi_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e9
    ).getInfo()

    nd_mean = mean_dict.get("NDVI", None)
    if nd_mean is not None:
        nd_mean = float(nd_mean)

    # ✅ Comparateur → pas de calcul veg
    if veg_local is None:
        return nd_mean, None

    # ✅ Proportion NDVI>0.25
    veg_dict = veg_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e9
    ).getInfo()

    veg_prop = veg_dict.get("VEG", None)
    if veg_prop is not None:
        veg_prop = float(veg_prop)

    return nd_mean, veg_prop

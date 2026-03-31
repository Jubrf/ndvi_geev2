import ee
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import transform

def shapely_to_ee(geom):
    """
    Convertit Polygon / MultiPolygon / GeometryCollection Shapely
    en géométrie Earth Engine valide :
    ✅ enlève la 3ème dimension
    ✅ gère Polygon
    ✅ gère MultiPolygon (via .geoms)
    ✅ gère GeometryCollection
    """

    # --- 1) Retirer Z si présent ---
    def strip_z(x, y, z=None):
        return (x, y)

    geom2d = transform(strip_z, geom)

    # --- 2) Polygon simple ---
    if isinstance(geom2d, Polygon):
        exterior = list(geom2d.exterior.coords)
        return ee.Geometry.Polygon([exterior])

    # --- 3) MultiPolygon ---
    if isinstance(geom2d, MultiPolygon):
        parts = []
        for poly in geom2d.geoms:          # ✅ Correction MAJEURE
            parts.append([list(poly.exterior.coords)])
        return ee.Geometry.MultiPolygon(parts)

    # --- 4) GeometryCollection ---
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

    # --- 5) Cas non géré ---
    return None


def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    """
    Calcule NDVI moyen + proportion NDVI > 0.25
    ✅ Gère Polygon / MultiPolygon / GeometryCollection
    ✅ Version adaptée à partir de ton code
    """

    # Conversion Shapely → EE
    geom_ee = shapely_to_ee(geom)
    if geom_ee is None:
        return None, None

    # ✅ Clip (indispensable pour éviter NDVI=None hors dalle)
    ndvi_local = ndvi_img.clip(geom_ee)
    veg_local  = veg_mask.clip(geom_ee) if veg_mask is not None else None

    # --- NDVI moyen ---
    mean_dict = ndvi_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    ndvi_mean = mean_dict.get("NDVI", None)
    if ndvi_mean is not None:
        ndvi_mean = float(ndvi_mean)

    # --- Si pas de masque vegetation (comparaison) ---
    if veg_local is None:
        return ndvi_mean, None

    # --- Proportion NDVI > 0.25 ---
    veg_dict = veg_local.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom_ee,
        scale=10,
        maxPixels=1e10
    ).getInfo()

    veg_prop = veg_dict.get("VEG", None)
    if veg_prop is not None:
        veg_prop = float(veg_prop)

    return ndvi_mean, veg_prop

import ee
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import transform

def shapely_to_ee(geom):
    """
    Convertit Polygon / MultiPolygon / GeometryCollection en ee.Geometry valide
    ✅ enlève Z
    ✅ normalise les MultiPolygon profondeur 2/3
    ✅ supprime les trous
    """

    # -- 1) Enlever Z si présent --
    def strip_z(x, y, z=None):
        return (x, y)

    geom2d = transform(strip_z, geom)

    # -- 2) Polygon --
    if isinstance(geom2d, Polygon):
        exterior = list(geom2d.exterior.coords)
        return ee.Geometry.Polygon([exterior])

    # -- 3) MultiPolygon (toutes profondeurs) --
    if isinstance(geom2d, MultiPolygon):
        parts = []
        for poly in geom2d.geoms:
            # on ignore les trous
            exterior = list(poly.exterior.coords)
            parts.append([exterior])
        return ee.Geometry.MultiPolygon(parts)

    # -- 4) GeometryCollection --
    if isinstance(geom2d, GeometryCollection):
        parts = []
        for item in geom2d.geoms:
            if isinstance(item, Polygon):
                parts.append([list(item.exterior.coords)])
            elif isinstance(item, MultiPolygon):
                for poly in item.geoms:
                    parts.append([list(poly.exterior.coords)])
        if len(parts) == 1:
            return ee.Geometry.Polygon(parts[0])
        elif len(parts) > 1:
            return ee.Geometry.MultiPolygon(parts)

    return None


def zonal_stats_ndvi(ndvi_img, veg_mask, geom):
    """
    NDVI moyen + proportion NDVI>0.25
    ✅ version robuste compatible SHP + GeoJSON
    """

    # ✅ corrige auto les géométries invalides ou trop complexes
    geom = geom.buffer(0)

    geom_ee = shapely_to_ee(geom)
    if geom_ee is None:
        return None, None

    # ✅ CLIP indispensable
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

    # ✅ Proportion NDVI>0.25
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

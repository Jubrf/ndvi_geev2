import tempfile
import json
import zipfile
import os
import shapefile
import streamlit as st
from shapely.geometry import shape
from shapely.ops import transform
import pyproj


# cache_data sur les bytes bruts : même fichier → pas de re-parsing.
# On passe les bytes explicitement pour que Streamlit puisse les hasher.
@st.cache_data(show_spinner="Chargement du fichier vecteur…")
def _load_vector_from_bytes(file_bytes, filename):
    """Parsing SHP/GeoJSON depuis les bytes bruts. Caché par contenu."""
    suffix = ".zip" if filename.endswith(".zip") else ".geojson"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(file_bytes)
    tmp.close()

    if suffix == ".geojson":
        with open(tmp.name, "r") as f:
            data = json.load(f)
        features = []
        for feat in data["features"]:
            geom  = shape(feat["geometry"])
            props = feat.get("properties", {})
            features.append({"geometry": geom, "properties": props})
        return features

    # ZIP SHP
    with zipfile.ZipFile(tmp.name, "r") as z:
        extract = tempfile.mkdtemp()
        z.extractall(extract)

    shp = [f for f in os.listdir(extract) if f.endswith(".shp")][0]
    shp_path = os.path.join(extract, shp)

    sf = shapefile.Reader(shp_path)
    fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    shapes  = sf.shapes()
    records = sf.records()

    prj = shp_path.replace(".shp", ".prj")
    transformer = None
    if os.path.exists(prj):
        with open(prj, "r") as f:
            wkt = f.read()
        try:
            src = pyproj.CRS.from_wkt(wkt)
            if src.to_epsg() is None or src.to_epsg() != 4326:
                dst = pyproj.CRS.from_epsg(4326)
                transformer = pyproj.Transformer.from_crs(src, dst, always_xy=True).transform
        except:
            pass

    features = []
    for shp_rec, rec in zip(shapes, records):
        geom = shape(shp_rec.__geo_interface__)
        if transformer:
            geom = transform(transformer, geom)
        props = dict(zip(fields, rec))
        features.append({"geometry": geom, "properties": props})

    return features


def load_vector(uploaded):
    """Point d'entrée public. Lit les bytes une seule fois et délègue au cache."""
    file_bytes = uploaded.read()
    uploaded.seek(0)  # rewind pour usage ultérieur éventuel
    return _load_vector_from_bytes(file_bytes, uploaded.name)

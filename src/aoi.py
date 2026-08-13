"""Descarga y valida los contornos de los lagos usados como máscara espacial."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from .config import AOI_DIR, LAKES, Lake

LOGGER = logging.getLogger(__name__)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "UVG-CC3084-Lab4/1.0 (academic geospatial analysis)"


def aoi_path(lake: Lake) -> Path:
    """Ruta estable del GeoJSON de un lago."""

    return AOI_DIR / f"{lake.key}.geojson"


def _download_aoi(lake: Lake) -> dict:
    response = requests.get(
        NOMINATIM_URL,
        params={
            "q": lake.nominatim_query,
            "format": "geojson",
            "polygon_geojson": 1,
            "addressdetails": 1,
            "limit": 10,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    features = response.json().get("features", [])
    matches = [
        feature
        for feature in features
        if feature.get("properties", {}).get("osm_type") == "relation"
        and int(feature.get("properties", {}).get("osm_id", -1))
        == lake.osm_relation
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"No se obtuvo exactamente el contorno OSM esperado para {lake.display_name}; "
            f"relación={lake.osm_relation}, coincidencias={len(matches)}."
        )

    feature = matches[0]
    feature["properties"] = {
        "name": lake.display_name,
        "osm_type": "relation",
        "osm_id": lake.osm_relation,
        "source": "OpenStreetMap via Nominatim",
        "source_url": f"https://www.openstreetmap.org/relation/{lake.osm_relation}",
        "license": "ODbL 1.0",
    }
    return {
        "type": "FeatureCollection",
        "name": f"contorno_{lake.key}",
        "attribution": "© OpenStreetMap contributors, ODbL 1.0",
        "features": [feature],
    }


def ensure_aoi(lake: Lake, refresh: bool = False) -> Path:
    """Obtiene el contorno OSM una sola vez y lo guarda como entrada pequeña."""

    path = aoi_path(lake)
    if refresh or not path.exists():
        LOGGER.info("Descargando contorno de %s desde Nominatim", lake.display_name)
        data = _download_aoi(lake)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    load_aoi(lake)  # valida el archivo persistido
    return path


def load_aoi(lake: Lake) -> BaseGeometry:
    """Carga el polígono y comprueba que corresponde al bbox entregado."""

    path = aoi_path(lake)
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data.get("features", [])
    if len(features) != 1:
        raise ValueError(f"{path} debe contener exactamente una geometría.")
    geometry = shape(features[0]["geometry"])
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"El AOI de {lake.display_name} no es poligonal.")
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    if geometry.is_empty or geometry.area <= 0:
        raise ValueError(f"El AOI de {lake.display_name} está vacío.")
    study_bbox = box(*lake.bbox)
    if not study_bbox.contains(geometry):
        # Un margen diminuto tolera diferencias de redondeo en los extremos OSM.
        if not study_bbox.buffer(0.002).contains(geometry):
            raise ValueError(
                f"El contorno OSM de {lake.display_name} cae fuera de las coordenadas del PDF."
            )
    return geometry


def ensure_all_aois() -> dict[str, BaseGeometry]:
    """Asegura y devuelve los dos contornos obligatorios."""

    geometries: dict[str, BaseGeometry] = {}
    for key, lake in LAKES.items():
        ensure_aoi(lake)
        geometries[key] = load_aoi(lake)
    return geometries


def geometry_geojson(geometry: BaseGeometry) -> dict:
    """Convierte una geometría Shapely a un objeto GeoJSON serializable."""

    return mapping(geometry)


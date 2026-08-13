"""Conexión real y selección reproducible de escenas Sentinel-2 L2A vía STAC."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import planetary_computer
import pystac
import pystac_client
import requests
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from .config import (
    L1C_STAC_API_URL,
    L1C_STAC_COLLECTION,
    OUTPUTS_DIR,
    STAC_API_URL,
    STAC_COLLECTION,
    Lake,
    Observation,
)

LOGGER = logging.getLogger(__name__)


def connect_catalog(sign_assets: bool = True) -> pystac_client.Client:
    """Abre el catálogo y, por defecto, firma temporalmente los activos Azure."""

    modifier = planetary_computer.sign_inplace if sign_assets else None
    catalog = pystac_client.Client.open(STAC_API_URL, modifier=modifier)
    if not catalog.conforms_to("ITEM_SEARCH"):
        raise RuntimeError("El endpoint configurado no implementa STAC Item Search.")
    return catalog


def connect_l1c_catalog() -> pystac_client.Client:
    """Abre Earth Search, cuyo catálogo L1C expone JP2 públicos en AWS."""

    catalog = pystac_client.Client.open(L1C_STAC_API_URL)
    if not catalog.conforms_to("ITEM_SEARCH"):
        raise RuntimeError("El endpoint L1C no implementa STAC Item Search.")
    return catalog


def verify_connection(
    catalog: pystac_client.Client, l1c_catalog: pystac_client.Client | None = None
) -> dict:
    """Comprueba colección y búsqueda, dejando evidencia legible por máquina."""

    collection = catalog.get_collection(STAC_COLLECTION)
    if collection is None:
        raise RuntimeError(f"No existe la colección {STAC_COLLECTION}.")
    evidence = {
        "status": "ok",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "api_url": STAC_API_URL,
        "collection": STAC_COLLECTION,
        "collection_title": collection.title,
        "stac_version": catalog.to_dict().get("stac_version", "unknown"),
        "authentication": "anonymous catalog; temporary public SAS tokens for assets",
    }
    if l1c_catalog is not None:
        l1c_collection = l1c_catalog.get_collection(L1C_STAC_COLLECTION)
        if l1c_collection is None:
            raise RuntimeError(f"No existe la colección {L1C_STAC_COLLECTION}.")
        evidence["l1c_source"] = {
            "api_url": L1C_STAC_API_URL,
            "collection": L1C_STAC_COLLECTION,
            "collection_title": l1c_collection.title,
            "purpose": "bandas TOA para reproducir el script oficial L1C",
            "authentication": "anonymous public AWS assets",
        }
    path = OUTPUTS_DIR / "api_connection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


def select_items(
    catalog: pystac_client.Client,
    lake: Lake,
    observation: Observation,
    geometry: BaseGeometry,
) -> list[pystac.Item]:
    """Selecciona solo la fecha y plataforma oficiales, conservando teselas necesarias."""

    interval = f"{observation.date}T00:00:00Z/{observation.date}T23:59:59Z"
    items = list(
        catalog.search(
            collections=[STAC_COLLECTION],
            intersects=mapping(geometry),
            datetime=interval,
            max_items=20,
        ).item_collection()
    )
    selected = [
        item
        for item in items
        if item.properties.get("platform") == observation.expected_satellite
        and item.datetime is not None
        and item.datetime.date().isoformat() == observation.date
        and shape(item.geometry).intersects(geometry)
    ]
    if not selected:
        candidates = [
            (item.id, item.properties.get("platform"), str(item.datetime)) for item in items
        ]
        raise RuntimeError(
            f"No hay escena oficial para {lake.key} {observation.date} "
            f"({observation.expected_satellite}). Candidatas: {candidates}"
        )
    acquisition_times = {item.datetime for item in selected}
    if len(acquisition_times) != 1:
        raise RuntimeError(
            f"Se obtuvieron adquisiciones distintas para {lake.key} {observation.date}: "
            f"{sorted(map(str, acquisition_times))}"
        )
    return sorted(selected, key=lambda item: item.properties.get("s2:mgrs_tile", ""))


def _earth_search_tile(item: pystac.Item) -> str:
    properties = item.properties
    return (
        f"{properties.get('mgrs:utm_zone', '')}"
        f"{properties.get('mgrs:latitude_band', '')}"
        f"{properties.get('mgrs:grid_square', '')}"
    )


def select_l1c_items(
    catalog: pystac_client.Client,
    lake: Lake,
    observation: Observation,
    geometry: BaseGeometry,
    fallback_l2a_items: list[pystac.Item] | None = None,
) -> list[pystac.Item]:
    """Selecciona L1C de la misma fecha/plataforma para el Cyano Detection."""

    interval = f"{observation.date}T00:00:00Z/{observation.date}T23:59:59Z"
    items = list(
        catalog.search(
            collections=[L1C_STAC_COLLECTION],
            intersects=mapping(geometry),
            datetime=interval,
            max_items=20,
        ).item_collection()
    )
    expected_platform = observation.expected_satellite.lower()
    selected = [
        item
        for item in items
        if str(item.properties.get("platform", "")).lower() == expected_platform
        and item.datetime is not None
        and item.datetime.date().isoformat() == observation.date
        and shape(item.geometry).intersects(geometry)
    ]
    if not selected and fallback_l2a_items:
        LOGGER.warning(
            "Earth Search no indexó %s %s; verificando rutas L1C públicas determinísticas",
            lake.key,
            observation.date,
        )
        selected = [
            _l1c_item_from_l2a(item, observation) for item in fallback_l2a_items
        ]
    if not selected:
        raise RuntimeError(
            f"No hay L1C para {lake.key} {observation.date} "
            f"({observation.expected_satellite})."
        )
    return sorted(selected, key=_earth_search_tile)


def _l1c_item_from_l2a(
    l2a_item: pystac.Item, observation: Observation
) -> pystac.Item:
    """Reconstruye un item L1C cuando el índice STAC omite un activo público existente."""

    tile = str(l2a_item.properties.get("s2:mgrs_tile", ""))
    if len(tile) != 5 or not tile[:2].isdigit():
        raise ValueError(f"Tesela MGRS inesperada: {tile}")
    zone, latitude_band, grid_square = tile[:2], tile[2], tile[3:]
    year, month, day = map(int, observation.date.split("-"))
    prefix = (
        f"s3://sentinel-s2-l1c/tiles/{zone}/{latitude_band}/{grid_square}/"
        f"{year}/{month}/{day}/0"
    )
    satellite_code = observation.expected_satellite.replace("Sentinel-", "S")
    item = pystac.Item(
        id=f"{satellite_code}_{tile}_{observation.date.replace('-', '')}_0_L1C_fallback",
        geometry=l2a_item.geometry,
        bbox=l2a_item.bbox,
        datetime=l2a_item.datetime,
        properties={
            "platform": observation.expected_satellite.lower(),
            "constellation": "sentinel-2",
            "s2:product_type": "S2MSI1C",
            "s2:processing_baseline": l2a_item.properties.get("s2:processing_baseline"),
            "mgrs:utm_zone": int(zone),
            "mgrs:latitude_band": latitude_band,
            "mgrs:grid_square": grid_square,
            "catalog_fallback": (
                "deterministic public AWS path verified after Earth Search index gap"
            ),
        },
    )
    asset_files = {
        "blue": "B02.jp2",
        "green": "B03.jp2",
        "red": "B04.jp2",
        "rededge1": "B05.jp2",
        "rededge3": "B07.jp2",
        "nir": "B08.jp2",
        "nir08": "B8A.jp2",
        "swir16": "B11.jp2",
        "swir22": "B12.jp2",
    }
    for key, filename in asset_files.items():
        item.add_asset(
            key,
            pystac.Asset(
                href=f"{prefix}/{filename}",
                media_type="image/jp2",
                roles=["data", "reflectance"],
            ),
        )
    check_url = item.assets["red"].href.replace(
        "s3://sentinel-s2-l1c/",
        "https://sentinel-s2-l1c.s3.amazonaws.com/",
        1,
    )
    response = requests.head(check_url, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"El activo L1C de respaldo no existe: {check_url}")
    return item


def item_manifest_rows(
    lake: Lake, observation: Observation, items: Iterable[pystac.Item]
) -> list[dict]:
    """Convierte la selección a filas auditables sin incluir URLs firmadas."""

    rows = []
    for item in items:
        cloud = float(item.properties.get("eo:cloud_cover", float("nan")))
        rows.append(
            {
                "lake": lake.key,
                "date": observation.date,
                "item_id": item.id,
                "mgrs_tile": item.properties.get("s2:mgrs_tile"),
                "satellite": item.properties.get("platform"),
                "expected_satellite": observation.expected_satellite,
                "tile_cloud_percent": cloud,
                "pdf_reference_cloud_percent": observation.expected_cloud_percent,
                "cloud_difference_points": abs(cloud - observation.expected_cloud_percent),
                "processing_baseline": item.properties.get("s2:processing_baseline"),
                "acquired_at": item.datetime.isoformat() if item.datetime else None,
                "note": observation.note,
            }
        )
    return rows


def save_item_collection(
    lake: Lake,
    observation: Observation,
    items: list[pystac.Item],
    subdirectory: str = "stac",
) -> Path:
    """Guarda metadatos STAC reproducibles, eliminando tokens temporales."""

    output = OUTPUTS_DIR / subdirectory / lake.key / f"{observation.date}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    clean_items = []
    for item in items:
        clone = item.clone()
        for asset in clone.assets.values():
            asset.href = asset.href.split("?", 1)[0]
        clean_items.append(clone)
    collection = pystac.ItemCollection(clean_items)
    output.write_text(json.dumps(collection.to_dict(), indent=2), encoding="utf-8")
    return output


def load_item_collection(
    lake: Lake,
    observation: Observation,
    subdirectory: str = "stac",
    sign_assets: bool = False,
) -> list[pystac.Item]:
    """Carga una selección STAC cacheada y renueva tokens solo cuando procede."""

    path = OUTPUTS_DIR / subdirectory / lake.key / f"{observation.date}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    collection = pystac.ItemCollection.from_dict(data)
    items = list(collection.items)
    if sign_assets:
        for item in items:
            planetary_computer.sign_inplace(item)
    return items


def write_scene_manifest(rows: list[dict]) -> Path:
    """Escribe el inventario final de escenas en orden estable."""

    output = OUTPUTS_DIR / "scene_manifest.csv"
    frame = pd.DataFrame(rows).sort_values(["lake", "date", "mgrs_tile"])
    frame.to_csv(output, index=False)
    return output

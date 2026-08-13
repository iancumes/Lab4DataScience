"""Lectura por ventana, máscaras y cálculo de Cyano Detection, NDVI y NDWI."""

from __future__ import annotations

import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.vrt import WarpedVRT
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from .config import (
    EXCLUDED_SCL,
    OUTPUTS_DIR,
    PROCESSED_DIR,
    QUALITY_BAND,
    RESOLUTION_METERS,
    SPECTRAL_BANDS,
    TARGET_CRS,
    Lake,
    Observation,
)

LOGGER = logging.getLogger(__name__)
NODATA_FLOAT = -9999.0
CYANO_SCRIPT_URL = (
    "https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/"
    "cyanobacteria_chla_ndci_l1c/"
)
L1C_ASSET_KEYS = {
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B05": "rededge1",
    "B07": "rededge3",
    "B08": "nir",
    "B8A": "nir08",
    "B11": "swir16",
    "B12": "swir22",
}
GDAL_OPTIONS = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.jp2",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "5000000",
}


@dataclass(frozen=True)
class Grid:
    """Grilla común alineada a 20 m para todos los productos de un lago."""

    crs: str
    transform: Affine
    width: int
    height: int

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)


def make_grid(geometry_wgs84: BaseGeometry) -> tuple[Grid, BaseGeometry]:
    """Proyecta el AOI a UTM y crea una grilla alineada a resolución constante."""

    transformer = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    projected = transform_geometry(transformer.transform, geometry_wgs84)
    minx, miny, maxx, maxy = projected.bounds
    resolution = float(RESOLUTION_METERS)
    minx = math.floor(minx / resolution) * resolution
    miny = math.floor(miny / resolution) * resolution
    maxx = math.ceil(maxx / resolution) * resolution
    maxy = math.ceil(maxy / resolution) * resolution
    width = int(round((maxx - minx) / resolution))
    height = int(round((maxy - miny) / resolution))
    transform = Affine(resolution, 0.0, minx, 0.0, -resolution, maxy)
    return Grid(TARGET_CRS, transform, width, height), projected


def _lake_mask(grid: Grid, projected_geometry: BaseGeometry) -> np.ndarray:
    return rasterize(
        [(projected_geometry, 1)],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)


def _reflectance_from_dn(data: np.ndarray, baseline: float) -> np.ndarray:
    """Convierte DN a BOA y replica `harmonizeValues=true` de Sentinel Hub."""

    offset = -1000.0 if baseline >= 4.0 else 0.0
    result = (data.astype("float32") + offset) / 10000.0
    # El script oficial no desactiva harmonizeValues. Sentinel Hub usa `true`
    # por defecto y recorta reflectancias negativas a cero antes del evalscript.
    np.maximum(result, 0.0, out=result)
    result[data <= 0] = np.nan
    return result


def _public_https(href: str) -> str:
    """Convierte el URI público s3 de Earth Search a HTTPS para GDAL."""

    if href.startswith("s3://sentinel-s2-l1c/"):
        return href.replace(
            "s3://sentinel-s2-l1c/",
            "https://sentinel-s2-l1c.s3.amazonaws.com/",
            1,
        )
    return href


def _read_mosaic(
    items: list,
    band: str,
    grid: Grid,
    asset_key: str | None = None,
    spectral: bool = True,
) -> np.ndarray:
    """Lee únicamente la ventana del AOI y mosaica teselas de la misma adquisición."""

    destination = np.full(grid.shape, np.nan, dtype="float32")
    resampling = Resampling.nearest if band == QUALITY_BAND else Resampling.bilinear
    for item in items:
        asset = item.assets.get(asset_key or band)
        if asset is None:
            raise KeyError(f"El activo {asset_key or band} no existe en {item.id}.")
        baseline = float(item.properties.get("s2:processing_baseline", 0.0))
        error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with rasterio.Env(**GDAL_OPTIONS):
                    with rasterio.open(_public_https(asset.href)) as source:
                        with WarpedVRT(
                            source,
                            crs=grid.crs,
                            transform=grid.transform,
                            width=grid.width,
                            height=grid.height,
                            src_nodata=0,
                            nodata=0,
                            resampling=resampling,
                        ) as vrt:
                            raw = vrt.read(1, out_dtype="float32")
                error = None
                break
            except rasterio.errors.RasterioError as exc:
                error = exc
                LOGGER.warning(
                    "Lectura incompleta %s/%s (intento %d/3): %s",
                    item.id,
                    band,
                    attempt,
                    exc,
                )
                time.sleep(attempt * 2)
        if error is not None:
            raise error
        valid = raw > 0
        values = _reflectance_from_dn(raw, baseline) if spectral else raw
        fill = valid & ~np.isfinite(destination)
        destination[fill] = values[fill]
    return destination


def safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Divide sin warnings y devuelve NaN cuando el denominador es cero/no finito."""

    result = np.full(numerator.shape, np.nan, dtype="float32")
    valid = (
        np.isfinite(numerator)
        & np.isfinite(denominator)
        & (np.abs(denominator) > 1e-8)
    )
    np.divide(numerator, denominator, out=result, where=valid)
    return result


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Calcula (a-b)/(a+b) con manejo explícito de divisiones por cero."""

    return safe_ratio(a - b, a + b)


def cyano_detection(bands: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Reproduce literalmente las operaciones numéricas del script oficial.

    El script original devuelve colores RGB. Aquí se conserva `chlorophyll_a`,
    el valor analítico calculado antes de aplicar la paleta, en mg/m³ (equivale
    numéricamente a µg/L), junto con sus máscaras auxiliares.
    """

    blue, green, red = bands["B02"], bands["B03"], bands["B04"]
    red_edge, red_edge3 = bands["B05"], bands["B07"]
    nir, nir_narrow = bands["B08"], bands["B8A"]
    swir1, swir2 = bands["B11"], bands["B12"]

    ndvi = normalized_difference(nir, red)
    mndwi = normalized_difference(green, swir1)
    ndwi = normalized_difference(green, nir)
    ndwi_leaves = normalized_difference(nir, swir1)
    aweish = blue + 2.5 * green - 1.5 * (nir + swir1) - 0.25 * swir2
    aweinsh = 4.0 * (green - swir1) - (0.25 * nir + 2.75 * swir1)
    dbsi = normalized_difference(swir1, green) - ndvi

    water = (
        (mndwi > 0.42)
        | (ndwi > 0.4)
        | (aweinsh > 0.1879)
        | (aweish > 0.1112)
        | (ndvi < -0.2)
        | (ndwi_leaves > 1.0)
    )
    # filter_UABS=true en el script: retira urbano y suelo desnudo.
    water &= ~((aweinsh <= -0.03) | (dbsi > 0.0))

    fai = red_edge3 - red - (nir_narrow - red) * ((783.0 - 665.0) / (865.0 - 665.0))
    ndci = normalized_difference(red_edge, red)
    chlorophyll = (
        826.57 * np.power(ndci, 3)
        - 176.43 * np.power(ndci, 2)
        + 19.0 * ndci
        + 4.071
    ).astype("float32")
    all_finite = np.logical_and.reduce([np.isfinite(bands[name]) for name in SPECTRAL_BANDS])
    water &= all_finite
    return {
        "water": water,
        "fai": fai.astype("float32"),
        "floating_vegetation": (fai > 0.08) & water,
        "ndci": ndci,
        "chlorophyll_a": chlorophyll,
    }


def _write_float_raster(
    path: Path,
    values: np.ndarray,
    grid: Grid,
    description: str,
    units: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = np.where(np.isfinite(values), values, NODATA_FLOAT).astype("float32")
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": "float32",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": NODATA_FLOAT,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(encoded, 1)
        dataset.set_band_description(1, description)
        dataset.update_tags(
            units=units,
            source="Sentinel-2 L1C spectral data with Sentinel-2 L2A SCL quality mask",
            nodata_reason="quality/spatial mask",
        )


def _write_masks(
    path: Path,
    grid: Grid,
    lake_mask: np.ndarray,
    source_valid: np.ndarray,
    quality_valid: np.ndarray,
    water: np.ndarray,
) -> None:
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 4,
        "dtype": "uint8",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": 0,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        for index, (array, name) in enumerate(
            [
                (lake_mask, "lake_polygon"),
                (source_valid, "source_coverage"),
                (quality_valid, "scl_quality_valid"),
                (water, "official_script_water_mask"),
            ],
            start=1,
        ):
            dataset.write(array.astype("uint8"), index)
            dataset.set_band_description(index, name)


def _array_statistics(values: np.ndarray, prefix: str) -> dict:
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        raise ValueError(f"No hay píxeles válidos para calcular {prefix}.")
    return {
        f"mean_{prefix}": float(np.mean(valid)),
        f"median_{prefix}": float(np.median(valid)),
        f"std_{prefix}": float(np.std(valid, ddof=0)),
        f"min_{prefix}": float(np.min(valid)),
        f"p10_{prefix}": float(np.percentile(valid, 10)),
        f"p25_{prefix}": float(np.percentile(valid, 25)),
        f"p75_{prefix}": float(np.percentile(valid, 75)),
        f"p90_{prefix}": float(np.percentile(valid, 90)),
        f"p95_{prefix}": float(np.percentile(valid, 95)),
        f"max_{prefix}": float(np.max(valid)),
    }


def _metadata_path(lake: Lake, observation: Observation) -> Path:
    return PROCESSED_DIR / lake.key / observation.date / "metadata.json"


def _outputs_complete(lake: Lake, observation: Observation) -> bool:
    folder = PROCESSED_DIR / lake.key / observation.date
    return all(
        (folder / name).exists()
        for name in ("cyanobacteria.tif", "ndvi.tif", "ndwi.tif", "masks.tif", "metadata.json")
    )


def process_observation(
    lake: Lake,
    observation: Observation,
    items: list,
    l1c_items: list,
    geometry_wgs84: BaseGeometry,
    force: bool = False,
) -> dict:
    """Procesa una fecha completa y devuelve sus métricas auditables."""

    metadata_path = _metadata_path(lake, observation)
    if not force and _outputs_complete(lake, observation):
        LOGGER.info("Reutilizando %s %s", lake.key, observation.date)
        return json.loads(metadata_path.read_text(encoding="utf-8"))["statistics"]

    LOGGER.info(
        "Procesando %s %s (%d L2A + %d L1C tesela/s)",
        lake.key,
        observation.date,
        len(items),
        len(l1c_items),
    )
    grid, projected_geometry = make_grid(geometry_wgs84)
    lake_mask = _lake_mask(grid, projected_geometry)
    total_pixels = int(np.count_nonzero(lake_mask))
    if total_pixels == 0:
        raise ValueError(f"La máscara de {lake.display_name} no contiene píxeles.")

    # El script oficial está definido para L1C. Sus nueve bandas TOA se leen
    # en paralelo y SCL se toma de la adquisición L2A correspondiente.
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            name: executor.submit(
                _read_mosaic,
                l1c_items,
                name,
                grid,
                L1C_ASSET_KEYS[name],
                True,
            )
            for name in SPECTRAL_BANDS
        }
        scl_future = executor.submit(
            _read_mosaic,
            items,
            QUALITY_BAND,
            grid,
            QUALITY_BAND,
            False,
        )
        bands = {name: future.result() for name, future in futures.items()}
        scl = scl_future.result()

    all_spectral_finite = np.logical_and.reduce(
        [np.isfinite(bands[name]) for name in SPECTRAL_BANDS]
    )
    source_valid = all_spectral_finite & np.isfinite(scl)
    scl_integer = np.where(np.isfinite(scl), np.rint(scl), 0).astype("uint8")
    quality_valid = source_valid & ~np.isin(scl_integer, list(EXCLUDED_SCL))

    cyano = cyano_detection(bands)
    common = lake_mask & quality_valid & cyano["water"]

    ndvi_raw = normalized_difference(bands["B08"], bands["B04"])
    ndwi_raw = normalized_difference(bands["B03"], bands["B08"])
    ndvi_valid = common & np.isfinite(ndvi_raw) & (ndvi_raw >= -1.0) & (ndvi_raw <= 1.0)
    ndwi_valid = common & np.isfinite(ndwi_raw) & (ndwi_raw >= -1.0) & (ndwi_raw <= 1.0)
    cyano_raw = cyano["chlorophyll_a"]
    cyano_valid = common & np.isfinite(cyano_raw) & (cyano_raw >= 0.0) & (cyano_raw <= 500.0)

    ndvi = np.where(ndvi_valid, ndvi_raw, np.nan).astype("float32")
    ndwi = np.where(ndwi_valid, ndwi_raw, np.nan).astype("float32")
    chlorophyll = np.where(cyano_valid, cyano_raw, np.nan).astype("float32")

    folder = metadata_path.parent
    _write_float_raster(
        folder / "cyanobacteria.tif",
        chlorophyll,
        grid,
        "chlorophyll_a_cyanobacteria_proxy",
        "mg m-3 (numerically equivalent to µg L-1)",
    )
    _write_float_raster(folder / "ndvi.tif", ndvi, grid, "NDVI", "unitless")
    _write_float_raster(folder / "ndwi.tif", ndwi, grid, "NDWI", "unitless")
    _write_masks(
        folder / "masks.tif",
        grid,
        lake_mask,
        source_valid & lake_mask,
        quality_valid & lake_mask,
        cyano["water"] & lake_mask,
    )

    valid_pixels = int(np.count_nonzero(cyano_valid))
    statistics = {
        "lake": lake.key,
        "date": observation.date,
        "satellite": observation.expected_satellite,
        "scene_ids": ";".join(item.id for item in items),
        "l1c_scene_ids": ";".join(item.id for item in l1c_items),
        "mgrs_tiles": ";".join(item.properties.get("s2:mgrs_tile", "") for item in items),
        "total_lake_pixels": total_pixels,
        "source_pixels": int(np.count_nonzero(source_valid & lake_mask)),
        "clear_pixels": int(np.count_nonzero(quality_valid & lake_mask)),
        "water_pixels": int(np.count_nonzero(common)),
        "valid_pixels": valid_pixels,
        "valid_percent": 100.0 * valid_pixels / total_pixels,
        "masked_percent": 100.0 * (total_pixels - valid_pixels) / total_pixels,
        "source_coverage_percent": 100.0
        * np.count_nonzero(source_valid & lake_mask)
        / total_pixels,
        "clear_percent": 100.0
        * np.count_nonzero(quality_valid & lake_mask)
        / total_pixels,
        "floating_vegetation_pixels": int(
            np.count_nonzero(cyano["floating_vegetation"] & common)
        ),
        "model_out_of_range_pixels": int(
            np.count_nonzero(common & np.isfinite(cyano_raw) & ~((cyano_raw >= 0) & (cyano_raw <= 500)))
        ),
        "excluded_scl_pixels": int(
            np.count_nonzero(lake_mask & source_valid & np.isin(scl_integer, list(EXCLUDED_SCL)))
        ),
        "note": observation.note,
        **_array_statistics(chlorophyll, "cyano"),
        **_array_statistics(ndvi, "ndvi"),
        **_array_statistics(ndwi, "ndwi"),
    }
    metadata = {
        "lake": lake.display_name,
        "date": observation.date,
        "source": {
            "spectral_api": "Element 84 Earth Search STAC",
            "spectral_collection": "sentinel-2-l1c",
            "spectral_items": [item.id for item in l1c_items],
            "spectral_assets_read": list(SPECTRAL_BANDS),
            "quality_api": "Microsoft Planetary Computer STAC",
            "quality_collection": "sentinel-2-l2a",
            "quality_items": [item.id for item in items],
            "quality_asset_read": QUALITY_BAND,
            "read_strategy": "remote raster windows only; no complete scene downloaded",
        },
        "grid": {
            "crs": grid.crs,
            "resolution_m": RESOLUTION_METERS,
            "width": grid.width,
            "height": grid.height,
            "transform": list(grid.transform)[:6],
        },
        "radiometry": {
            "formula": (
                "reflectance=max(0,(DN-1000)/10000) for processing baseline >= 04.00; "
                "equivalent to Sentinel Hub harmonizeValues=true"
            ),
            "processing_baselines": sorted(
                {item.properties.get("s2:processing_baseline") for item in l1c_items}
            ),
        },
        "masking": {
            "spatial": "OpenStreetMap lake polygon",
            "excluded_scl_classes": sorted(EXCLUDED_SCL),
            "water": "water-body function from official Sentinel Hub Cyano Detection script",
            "cyano_model_range": "0 to 500 mg/m3; values outside excluded",
        },
        "cyano_algorithm": {
            "source": CYANO_SCRIPT_URL,
            "ndci": "(B05-B04)/(B05+B04)",
            "chlorophyll_a": "826.57*NDCI^3 - 176.43*NDCI^2 + 19*NDCI + 4.071",
            "rgb_not_used_for_statistics": True,
        },
        "statistics": statistics,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return statistics


def write_quality_metrics(rows: list[dict]) -> Path:
    """Guarda estadísticas por fecha, incluidos NDVI/NDWI y porcentajes válidos."""

    path = OUTPUTS_DIR / "quality_metrics.csv"
    pd.DataFrame(rows).sort_values(["lake", "date"]).to_csv(path, index=False)
    return path

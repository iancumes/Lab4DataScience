"""Construcción del dataset a nivel de píxel para los modelos de la Parte II.

Reutiliza literalmente los rásters de la Parte I (`data/processed/<lago>/<fecha>/`)
en lugar de volver a descargar Sentinel-2: NDVI, NDWI y el proxy de cianobacteria
(`chlorophyll_a`, calculado a partir de NDCI) ya están escritos en disco, junto con
una máscara de agua oficial. Este módulo solo aplana esos rásters en una tabla
tidy (una fila por píxel válido) y agrega las columnas de apoyo (variable
respuesta, variables predictoras) que piden los ejercicios 1 a 3 de la Parte II.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from shapely.ops import transform as shapely_transform

from .aoi import load_aoi
from .config import LAKES, OUTPUTS_DIR, PROCESSED_DIR, TARGET_CRS, expected_dates

PIXEL_DATASET_PATH = OUTPUTS_DIR / "ml_pixel_dataset.parquet"
READY_DATASET_PATH = OUTPUTS_DIR / "ml_dataset_ready.parquet"

# Umbral de cianobacteria alta (Ejercicio 2.1-2.2), en µg/L (equivalente
# numérico a mg/m3 en este proxy, ver metadata.json de cada fecha). Corresponde
# al Alert Level 1 de la guía de la OMS para aguas recreativas (Chorus & Bartram,
# 1999; WHO, 2003 "Guidelines for Safe Recreational Water Environments", Vol. 1,
# cap. 8): ≈10 µg/L de clorofila-a con dominancia de cianobacterias se asocia a
# probabilidad moderada de efectos adversos a la salud y activa monitoreo
# reforzado. Se documenta y puede recalcularse con otro corte en el Ejercicio 2.
WHO_ALERT_LEVEL_1_UG_L = 10.0

RESPONSE_COLUMN = "high_cyano"

# Variables que participan, directa o indirectamente, en la construcción de la
# variable respuesta y por lo tanto NO deben usarse como predictoras (Ejercicio
# 2.5): `cyano_mg_m3` es el proxy continuo de cianobacteria (clorofila-a vía
# NDCI = (B05-B04)/(B05+B04)) del cual `high_cyano` se deriva por corte directo.
RESPONSE_LEAKAGE_COLUMNS = ("cyano_mg_m3",)

BASE_COLUMNS = (
    "lake",
    "date",
    "x_utm15n",
    "y_utm15n",
    "lon",
    "lat",
    "ndvi",
    "ndwi",
    "cyano_mg_m3",
)


def _pixel_centers(transform: rasterio.Affine, height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    """Coordenadas del centro de cada píxel de la grilla, vectorizado."""

    rows, cols = np.mgrid[0:height, 0:width]
    xs, ys = transform * (cols + 0.5, rows + 0.5)
    return xs, ys


def _read_band(path, band: int = 1) -> np.ndarray:
    with rasterio.open(path) as dataset:
        return dataset.read(band, masked=True).filled(np.nan)


def _read_lake_date(lake_key: str, date: str) -> pd.DataFrame:
    """Aplana los rásters de un lago/fecha a filas de píxeles válidos."""

    folder = PROCESSED_DIR / lake_key / date
    with rasterio.open(folder / "cyanobacteria.tif") as dataset:
        cyano = dataset.read(1, masked=True).filled(np.nan)
        transform = dataset.transform
        crs = dataset.crs

    ndvi = _read_band(folder / "ndvi.tif")
    ndwi = _read_band(folder / "ndwi.tif")
    with rasterio.open(folder / "masks.tif") as dataset:
        water = dataset.read(4)  # banda 4: official_script_water_mask

    # Mismo criterio de validez que `processing.process_observation`: dentro del
    # lago, agua según el script oficial, y con NDVI/NDWI/cianobacteria finitos
    # (fuera de rango, nubes, sombras y NoData ya quedaron excluidos al escribir
    # estos rásters en la Parte I).
    valid = (water == 1) & np.isfinite(cyano) & np.isfinite(ndvi) & np.isfinite(ndwi)
    if not np.any(valid):
        raise ValueError(f"{lake_key} {date}: no hay píxeles válidos.")

    xs, ys = _pixel_centers(transform, cyano.shape[0], cyano.shape[1])
    xs, ys = xs[valid], ys[valid]
    to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon, lat = to_wgs84.transform(xs, ys)

    return pd.DataFrame(
        {
            "lake": lake_key,
            "date": date,
            "x_utm15n": xs,
            "y_utm15n": ys,
            "lon": lon,
            "lat": lat,
            "ndvi": ndvi[valid],
            "ndwi": ndwi[valid],
            "cyano_mg_m3": cyano[valid],
        }
    )


def build_pixel_dataset(force: bool = False) -> pd.DataFrame:
    """Construye (o reutiliza en disco) el dataset a nivel de píxel, todas las
    fechas oficiales de ambos lagos, con las columnas de `BASE_COLUMNS`."""

    if PIXEL_DATASET_PATH.exists() and not force:
        return pd.read_parquet(PIXEL_DATASET_PATH)

    frames = []
    for lake_key in LAKES:
        for date in expected_dates(lake_key):
            frames.append(_read_lake_date(lake_key, date))

    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df["lake"] = df["lake"].astype("category")
    df = df[list(BASE_COLUMNS)]

    PIXEL_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PIXEL_DATASET_PATH, index=False)
    return df


def dataset_summary(df: pd.DataFrame) -> dict:
    """Resumen para el Ejercicio 1.4: conteos por lago/fecha, tipos y % faltantes."""

    by_lake = df.groupby("lake", observed=True).size().rename("n_observaciones")
    by_lake_date = (
        df.groupby(["lake", "date"], observed=True).size().rename("n_observaciones").reset_index()
    )
    dtypes = df.dtypes.astype(str).rename("tipo")
    missing_pct = (df.isna().mean() * 100).rename("pct_faltante")
    return {
        "total_observaciones": int(len(df)),
        "por_lago": by_lake,
        "por_lago_fecha": by_lake_date,
        "tipos_y_faltantes": pd.concat([dtypes, missing_pct], axis=1),
    }


def add_response_variable(
    df: pd.DataFrame,
    threshold_ug_l: float = WHO_ALERT_LEVEL_1_UG_L,
    column: str = RESPONSE_COLUMN,
) -> pd.DataFrame:
    """Ejercicio 2.1: variable binaria 0/1 a partir del corte de cianobacteria."""

    out = df.copy()
    out[column] = (out["cyano_mg_m3"] >= threshold_ug_l).astype(int)
    return out


def lake_centroids_utm() -> dict[str, tuple[float, float]]:
    """Centroide (x, y) en EPSG:32615 del polígono OSM de cada lago."""

    to_utm = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    centroids = {}
    for key, lake in LAKES.items():
        geometry_wgs84 = load_aoi(lake)
        geometry_utm = shapely_transform(lambda x, y: to_utm.transform(x, y), geometry_wgs84)
        centroid = geometry_utm.centroid
        centroids[key] = (centroid.x, centroid.y)
    return centroids


def engineer_features(df: pd.DataFrame, centroids: dict[str, tuple[float, float]] | None = None) -> pd.DataFrame:
    """Ejercicio 3.3: variables temporales y espaciales derivadas.

    - `month`, `day_of_year`: estacionalidad (la Parte I encontró patrones
      estacionales de floración).
    - `dist_centroid_m`: distancia euclidiana (m) al centroide del lago,
      proxy espacial simple de "qué tan cerca del centro del lago" está la
      observación, sin depender de bandas espectrales adicionales.
    """

    if centroids is None:
        centroids = lake_centroids_utm()

    out = df.copy()
    out["month"] = out["date"].dt.month
    out["day_of_year"] = out["date"].dt.dayofyear

    cx = out["lake"].map(lambda key: centroids[key][0]).astype(float)
    cy = out["lake"].map(lambda key: centroids[key][1]).astype(float)
    out["dist_centroid_m"] = np.sqrt((out["x_utm15n"] - cx) ** 2 + (out["y_utm15n"] - cy) ** 2)
    return out


def save_ready_dataset(df: pd.DataFrame) -> None:
    READY_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(READY_DATASET_PATH, index=False)

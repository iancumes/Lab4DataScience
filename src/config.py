"""Configuración central derivada literalmente del enunciado del laboratorio."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
AOI_DIR = DATA_DIR / "aoi"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUTS_DIR = ROOT / "outputs"
FIGURES_DIR = ROOT / "figures"
DOCS_DIR = ROOT / "docs"

STAC_API_URL = os.getenv(
    "STAC_API_URL", "https://planetarycomputer.microsoft.com/api/stac/v1"
)
STAC_COLLECTION = os.getenv("STAC_COLLECTION", "sentinel-2-l2a")
L1C_STAC_API_URL = os.getenv("L1C_STAC_API_URL", "https://earth-search.aws.element84.com/v1")
L1C_STAC_COLLECTION = os.getenv("L1C_STAC_COLLECTION", "sentinel-2-l1c")
TARGET_CRS = "EPSG:32615"  # UTM 15N, apropiado para ambos lagos
RESOLUTION_METERS = 20

# Unión mínima de bandas que reproduce el script oficial Cyano Detection y
# también permite NDVI/NDWI. SCL se usa solo para calidad.
SPECTRAL_BANDS = ("B02", "B03", "B04", "B05", "B07", "B08", "B8A", "B11", "B12")
QUALITY_BAND = "SCL"

# SCL de Sentinel-2 L2A excluidas: no-data, saturado/defectuoso, sombra de nube,
# no clasificado/baja probabilidad de nube, nube media, nube alta, cirrus y nieve.
EXCLUDED_SCL = frozenset({0, 1, 3, 7, 8, 9, 10, 11})


@dataclass(frozen=True)
class Observation:
    """Fecha oficial y metadatos de comprobación dados por el profesor."""

    date: str
    expected_cloud_percent: float
    expected_satellite: str
    note: str = ""


@dataclass(frozen=True)
class Lake:
    """Área de estudio y observaciones obligatorias."""

    key: str
    display_name: str
    bbox: tuple[float, float, float, float]  # west, south, east, north
    nominatim_query: str
    osm_relation: int
    observations: tuple[Observation, ...]


LAKES: dict[str, Lake] = {
    "amatitlan": Lake(
        key="amatitlan",
        display_name="Lago Amatitlán",
        bbox=(-90.638065, 14.412347, -90.512924, 14.493799),
        nominatim_query="Lago Amatitlan, Guatemala",
        osm_relation=11018382,
        observations=(
            Observation("2025-01-28", 0.06, "Sentinel-2B"),
            Observation("2025-04-15", 0.09, "Sentinel-2A"),
            Observation("2025-04-28", 1.03, "Sentinel-2B"),
            Observation("2025-11-24", 0.50, "Sentinel-2B"),
            Observation("2026-01-08", 0.77, "Sentinel-2C"),
            Observation("2026-02-02", 0.39, "Sentinel-2B"),
            Observation(
                "2026-02-07",
                0.02,
                "Sentinel-2C",
                "Cobertura válida parcial indicada por el PDF: aproximadamente 57.1%.",
            ),
            Observation("2026-03-29", 0.01, "Sentinel-2C"),
            Observation("2026-04-13", 0.09, "Sentinel-2B"),
            Observation("2026-04-28", 4.96, "Sentinel-2C"),
            Observation("2026-06-19", 13.00, "Sentinel-2A"),
        ),
    ),
    "atitlan": Lake(
        key="atitlan",
        display_name="Lago Atitlán",
        bbox=(-91.326256, 14.5948, -91.07151, 14.750979),
        nominatim_query="Lago Atitlan, Guatemala",
        osm_relation=5781818,
        observations=(
            Observation("2025-01-18", 0.02, "Sentinel-2B"),
            Observation("2025-04-13", 0.54, "Sentinel-2C"),
            Observation("2025-05-13", 4.37, "Sentinel-2C"),
            Observation("2025-07-17", 3.57, "Sentinel-2A"),
            Observation("2025-11-21", 3.15, "Sentinel-2A"),
            Observation("2025-12-29", 3.17, "Sentinel-2C"),
            Observation("2026-02-12", 0.04, "Sentinel-2B"),
            Observation("2026-03-24", 3.17, "Sentinel-2B"),
            Observation("2026-04-13", 0.01, "Sentinel-2B"),
            Observation("2026-04-28", 4.96, "Sentinel-2C"),
            Observation("2026-07-22", 4.02, "Sentinel-2B"),
        ),
    ),
}


def ensure_directories() -> None:
    """Crea únicamente las carpetas de trabajo esperadas."""

    for path in (AOI_DIR, PROCESSED_DIR, OUTPUTS_DIR, FIGURES_DIR, DOCS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def expected_dates(lake_key: str) -> tuple[str, ...]:
    """Devuelve las fechas oficiales en el orden del PDF."""

    return tuple(obs.date for obs in LAKES[lake_key].observations)

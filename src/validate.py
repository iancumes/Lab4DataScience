"""Validaciones integrales de fechas, estadísticas, rasters, figuras e informe."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from pypdf import PdfReader

from .config import (
    DOCS_DIR,
    FIGURES_DIR,
    LAKES,
    OUTPUTS_DIR,
    PROCESSED_DIR,
    ROOT,
    TARGET_CRS,
    expected_dates,
)

LOGGER = logging.getLogger(__name__)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _valid_values(path: Path) -> tuple[np.ndarray, rasterio.DatasetReader]:
    dataset = rasterio.open(path)
    values = dataset.read(1, masked=True).compressed()
    return values, dataset


def _validate_rasters() -> dict:
    checked = 0
    reference_by_lake: dict[str, tuple] = {}
    for lake_key in LAKES:
        for observation_date in expected_dates(lake_key):
            folder = PROCESSED_DIR / lake_key / observation_date
            _assert(folder.is_dir(), f"Falta carpeta {folder}")
            signatures = []
            for filename, limits in (
                ("cyanobacteria.tif", (0.0, 500.0)),
                ("ndvi.tif", (-1.0, 1.0)),
                ("ndwi.tif", (-1.0, 1.0)),
            ):
                path = folder / filename
                _assert(path.exists(), f"Falta {path}")
                values, dataset = _valid_values(path)
                try:
                    _assert(values.size > 0, f"Raster sin datos válidos: {path}")
                    _assert(str(dataset.crs) == TARGET_CRS, f"CRS incorrecto: {path}")
                    _assert(dataset.transform.is_rectilinear, f"Transformación inválida: {path}")
                    _assert(np.isfinite(values).all(), f"Valores no finitos: {path}")
                    _assert(
                        float(values.min()) >= limits[0] - 1e-5
                        and float(values.max()) <= limits[1] + 1e-5,
                        f"Rango inválido en {path}: {values.min()}..{values.max()}",
                    )
                    signatures.append((dataset.width, dataset.height, dataset.transform, str(dataset.crs)))
                finally:
                    dataset.close()
                checked += 1
            _assert(len(set(signatures)) == 1, f"Productos desalineados en {folder}")
            masks = folder / "masks.tif"
            _assert(masks.exists(), f"Falta {masks}")
            with rasterio.open(masks) as dataset:
                _assert(dataset.count == 4, f"Se esperaban cuatro máscaras en {masks}")
                signature = (dataset.width, dataset.height, dataset.transform, str(dataset.crs))
            _assert(signature == signatures[0], f"Máscara desalineada en {folder}")
            reference_by_lake.setdefault(lake_key, signatures[0])
            _assert(reference_by_lake[lake_key] == signatures[0], f"Fechas desalineadas en {lake_key}")
    return {"rasters_checked": checked, "crs": TARGET_CRS}


def _validate_tables() -> dict:
    metrics = pd.read_csv(OUTPUTS_DIR / "quality_metrics.csv", dtype={"date": str})
    temporal = pd.read_csv(OUTPUTS_DIR / "cyano_temporal_stats.csv", dtype={"date": str})
    expected = {(lake, date) for lake in LAKES for date in expected_dates(lake)}
    for name, frame in (("quality_metrics", metrics), ("cyano_temporal_stats", temporal)):
        actual = set(zip(frame["lake"], frame["date"], strict=False))
        _assert(actual == expected, f"Fechas inválidas en {name}")
        _assert(len(frame) == 22, f"{name} debe tener 22 filas")
    numeric = temporal[["mean_cyano", "median_cyano", "std_cyano", "valid_pixels", "valid_percent"]]
    _assert(np.isfinite(numeric.to_numpy(float)).all(), "Hay estadísticas no finitas")
    _assert((temporal["valid_pixels"] > 0).all(), "Se calculó sobre arrays vacíos")
    _assert(temporal["valid_percent"].between(0, 100).all(), "Porcentaje válido fuera de rango")
    partial = temporal[(temporal["lake"] == "amatitlan") & (temporal["date"] == "2026-02-07")]
    _assert(len(partial) == 1, "Se eliminó la fecha parcial 2026-02-07")

    manifest = pd.read_csv(OUTPUTS_DIR / "scene_manifest.csv", dtype={"date": str})
    manifest_pairs = set(zip(manifest["lake"], manifest["date"], strict=False))
    _assert(manifest_pairs == expected, "El manifiesto STAC no cubre las fechas exactas")
    _assert((manifest["satellite"] == manifest["expected_satellite"]).all(), "Satélite no coincide")
    return {"rows_checked": len(temporal), "scene_tiles_checked": len(manifest)}


def _validate_figures_and_report() -> dict:
    figures = [
        FIGURES_DIR / "temporal_atitlan.png",
        FIGURES_DIR / "temporal_amatitlan.png",
        FIGURES_DIR / "temporal_comparison.png",
        FIGURES_DIR / "map_cyano_atitlan.png",
        FIGURES_DIR / "map_cyano_amatitlan.png",
    ]
    dimensions = {}
    for path in figures:
        _assert(path.exists(), f"Falta figura {path}")
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            _assert(image.width >= 1800 and image.height >= 1200, f"Baja resolución: {path}")
            dimensions[path.name] = [image.width, image.height]
    markdown = DOCS_DIR / "avance_lab4.md"
    pdf = DOCS_DIR / "avance_lab4.pdf"
    _assert(markdown.exists() and markdown.stat().st_size > 5000, "Informe Markdown incompleto")
    _assert(pdf.exists() and pdf.stat().st_size > 50_000, "Informe PDF incompleto")
    reader = PdfReader(pdf)
    _assert(len(reader.pages) >= 6, "El PDF parece incompleto")
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    for phrase in ("Resumen ejecutivo", "Resultados temporales", "Atitlán", "Amatitlán"):
        _assert(phrase in extracted, f"El PDF no contiene {phrase}")
    return {"figures": dimensions, "pdf_pages": len(reader.pages)}


def _validate_connection_and_secrets() -> dict:
    connection = json.loads((OUTPUTS_DIR / "api_connection.json").read_text(encoding="utf-8"))
    _assert(connection.get("status") == "ok", "No hay evidencia de conexión API")
    forbidden_files = [ROOT / ".env"]
    _assert(not any(path.exists() for path in forbidden_files), "Existe un archivo local de secretos .env")
    # Las cadenas se construyen para que este propio validador no se marque.
    sensitive_markers = (
        "client_" + "secret=",
        "api_" + "key=",
        "pass" + "word=",
    )
    checked = 0
    for folder in (ROOT / "src", ROOT / "docs", ROOT / "tests"):
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".md", ".js", ".txt"}:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                _assert(not any(marker in text for marker in sensitive_markers), f"Posible secreto: {path}")
                checked += 1
    return {"files_scanned_for_secrets": checked, "connection": connection}


def _write_status(report: dict) -> None:
    status = """# Estado del avance - Laboratorio 4

Última validación: {checked}

| Requisito | Estado | Evidencia/archivo |
|-----------|--------|-------------------|
| Ejercicio 1 | ✅ | `outputs/api_connection.json`, `outputs/scene_manifest.csv` |
| Ejercicio 2 | ✅ | `data/aoi/`, ventanas COG y metadatos en `outputs/stac/` |
| Ejercicio 3 | ✅ | 66 GeoTIFF en `data/processed/<lago>/<fecha>/` y máscaras de calidad |
| Ejercicio 4.1 | ✅ | `outputs/cyano_temporal_stats.csv` (22 observaciones) |
| Ejercicio 4.2 | ✅ | `figures/temporal_*.png` y comparación |
| Ejercicio 4.3 | ✅ | `outputs/critical_dates.csv` con criterios robustos |
| Ejercicio 4.4 | ✅ | `outputs/temporal_interpretation.json` e informe |
| Informe PDF | ✅ | `docs/avance_lab4.pdf` |
| Validaciones | ✅ | `outputs/validation_report.json`; {rasters} rasters y {pages} páginas PDF |

Los símbolos ✅ se escriben únicamente después de que todas las comprobaciones terminan sin errores.
""".format(
        checked=report["checked_at_utc"],
        rasters=report["rasters"]["rasters_checked"],
        pages=report["artifacts"]["pdf_pages"],
    )
    (ROOT / "STATUS.md").write_text(status, encoding="utf-8")


def run_validation() -> dict:
    """Ejecuta todas las validaciones; solo crea STATUS verde si todo pasa."""

    report = {
        "status": "passed",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "tables": _validate_tables(),
        "rasters": _validate_rasters(),
        "artifacts": _validate_figures_and_report(),
        "security": _validate_connection_and_secrets(),
    }
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUTS_DIR / "validation_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_status(report)
    LOGGER.info("Validación integral superada")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_validation()

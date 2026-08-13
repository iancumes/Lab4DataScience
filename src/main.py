"""Punto de entrada reproducible del avance (ejercicios 1 al 4)."""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .analysis import run_analysis
from .aoi import ensure_all_aois
from .config import LAKES, OUTPUTS_DIR, ensure_directories
from .processing import process_observation, write_quality_metrics
from .report import build_reports
from .stac import (
    connect_catalog,
    connect_l1c_catalog,
    item_manifest_rows,
    load_item_collection,
    save_item_collection,
    select_items,
    select_l1c_items,
    verify_connection,
    write_scene_manifest,
)
from .validate import run_validation


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("rasterio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def run_data_stage(force: bool = False) -> None:
    """Ejecuta conexión, selección STAC y procesamiento de las 22 observaciones."""

    geometries = ensure_all_aois()
    all_cached = all(
        (OUTPUTS_DIR / subdirectory / lake.key / f"{observation.date}.json").exists()
        for lake in LAKES.values()
        for observation in lake.observations
        for subdirectory in ("stac", "stac_l1c")
    )
    catalog = None
    l1c_catalog = None
    if not all_cached:
        catalog = connect_catalog(sign_assets=True)
        l1c_catalog = connect_l1c_catalog()
        verify_connection(catalog, l1c_catalog)
    else:
        logging.getLogger(__name__).info("Reutilizando las 22 selecciones STAC auditadas")
    manifest_rows: list[dict] = []
    metric_rows: list[dict] = []
    total = sum(len(lake.observations) for lake in LAKES.values())
    completed = 0
    jobs: list[tuple] = []
    for lake_key, lake in LAKES.items():
        geometry = geometries[lake_key]
        for observation in lake.observations:
            l2_cache = OUTPUTS_DIR / "stac" / lake.key / f"{observation.date}.json"
            l1_cache = OUTPUTS_DIR / "stac_l1c" / lake.key / f"{observation.date}.json"
            if l2_cache.exists() and l1_cache.exists():
                items = load_item_collection(
                    lake, observation, subdirectory="stac", sign_assets=True
                )
                l1c_items = load_item_collection(
                    lake, observation, subdirectory="stac_l1c"
                )
            else:
                assert catalog is not None and l1c_catalog is not None
                items = select_items(catalog, lake, observation, geometry)
                l1c_items = select_l1c_items(
                    l1c_catalog, lake, observation, geometry, fallback_l2a_items=items
                )
                save_item_collection(lake, observation, items)
                save_item_collection(
                    lake, observation, l1c_items, subdirectory="stac_l1c"
                )
            manifest_rows.extend(item_manifest_rows(lake, observation, items))
            jobs.append((lake, observation, items, l1c_items, geometry))

    # Se procesa una observación a la vez: los JP2 ya paralelizan sus bandas y
    # demasiadas solicitudes simultáneas pueden provocar range responses truncadas.
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {
            executor.submit(
                process_observation,
                lake,
                observation,
                items,
                l1c_items,
                geometry,
                force,
            ): (lake.key, observation.date)
            for lake, observation, items, l1c_items, geometry in jobs
        }
        for future in as_completed(futures):
            lake_key, observation_date = futures[future]
            try:
                metric_rows.append(future.result())
            except Exception:
                logging.getLogger(__name__).exception(
                    "Falló %s %s", lake_key, observation_date
                )
                raise
            else:
                completed += 1
                logging.getLogger(__name__).info(
                    "Avance de datos: %d/%d observaciones", completed, total
                )
    write_scene_manifest(manifest_rows)
    write_quality_metrics(metric_rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("all", "data", "analysis", "report", "validate"),
        default="all",
        help="Etapa a ejecutar; por defecto ejecuta el pipeline completo.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocesa rasters aunque ya existan productos completos.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    ensure_directories()
    args = parse_args(argv)
    if args.stage in {"all", "data"}:
        run_data_stage(force=args.force)
    if args.stage in {"all", "analysis"}:
        run_analysis()
    if args.stage in {"all", "report"}:
        build_reports()
    if args.stage in {"all", "validate"}:
        run_validation()
    return 0


if __name__ == "__main__":
    sys.exit(main())

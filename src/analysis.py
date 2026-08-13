"""Estadística temporal, detección robusta de picos y visualizaciones."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

from .config import FIGURES_DIR, LAKES, OUTPUTS_DIR, PROCESSED_DIR, expected_dates

LOGGER = logging.getLogger(__name__)
COLORS = {"atitlan": "#007C91", "amatitlan": "#D55E00"}


def _style_axes(axis: plt.Axes) -> None:
    axis.grid(True, color="#D7DEE3", linewidth=0.8, alpha=0.8)
    axis.spines[["top", "right"]].set_visible(False)
    axis.tick_params(labelsize=9)


def build_temporal_tables(metrics_path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Crea la tabla temporal principal y clasifica máximos/picos estadísticos."""

    source = metrics_path or OUTPUTS_DIR / "quality_metrics.csv"
    metrics = pd.read_csv(source, dtype={"date": str})
    required = {(lake, date) for lake in LAKES for date in expected_dates(lake)}
    actual = set(zip(metrics["lake"], metrics["date"], strict=False))
    if actual != required:
        raise ValueError(
            f"Fechas inesperadas/faltantes. Faltan={sorted(required-actual)}; sobran={sorted(actual-required)}"
        )

    columns = [
        "lake",
        "date",
        "mean_cyano",
        "median_cyano",
        "std_cyano",
        "min_cyano",
        "p10_cyano",
        "p25_cyano",
        "p75_cyano",
        "p90_cyano",
        "p95_cyano",
        "max_cyano",
        "valid_pixels",
        "valid_percent",
        "masked_percent",
        "source_coverage_percent",
        "clear_percent",
        "mean_ndvi",
        "mean_ndwi",
        "satellite",
        "mgrs_tiles",
        "note",
    ]
    temporal = metrics[columns].copy().sort_values(["lake", "date"])
    temporal.to_csv(OUTPUTS_DIR / "cyano_temporal_stats.csv", index=False)

    critical_rows: list[pd.DataFrame] = []
    for lake_key, group in temporal.groupby("lake", sort=True):
        ranked = group.copy()
        values = ranked["mean_cyano"].to_numpy(float)
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        robust_z = (
            0.6745 * (values - median) / mad if mad > 1e-12 else np.full_like(values, np.nan)
        )
        q1, q3 = np.percentile(values, [25, 75])
        upper_fence = q3 + 1.5 * (q3 - q1)
        ranked["robust_z"] = robust_z
        ranked["iqr_upper_fence"] = upper_fence
        ranked["rank_desc"] = ranked["mean_cyano"].rank(method="min", ascending=False).astype(int)
        ranked["percentile_rank"] = 100.0 * ranked["mean_cyano"].rank(
            method="average", pct=True
        )
        ranked["is_statistical_peak"] = (ranked["robust_z"] >= 2.0) | (
            ranked["mean_cyano"] > upper_fence
        )
        ranked["is_low_coverage"] = ranked["source_coverage_percent"] < 50.0
        ranked["classification"] = np.where(
            ranked["is_low_coverage"],
            "cobertura_baja_no_comparable",
            np.where(ranked["is_statistical_peak"],
            "pico_estadistico",
            np.where(ranked["rank_desc"] == 1, "maximo_observado", "sin_pico"),
            ),
        )
        critical_rows.append(ranked.sort_values("rank_desc"))
    critical = pd.concat(critical_rows, ignore_index=True)
    critical.to_csv(OUTPUTS_DIR / "critical_dates.csv", index=False)
    return temporal, critical


def _plot_lake_series(group: pd.DataFrame, lake_key: str) -> Path:
    lake = LAKES[lake_key]
    data = group.sort_values("date").copy()
    dates = pd.to_datetime(data["date"])
    low_coverage = data["source_coverage_percent"] < 50.0
    comparable_mean = data["mean_cyano"].mask(low_coverage)
    comparable_std = data["std_cyano"].mask(low_coverage)
    fig, axis = plt.subplots(figsize=(9.2, 5.2), constrained_layout=True)
    axis.plot(
        dates,
        comparable_mean,
        color=COLORS[lake_key],
        linewidth=2.2,
        marker="o",
        markersize=5.5,
        label="Promedio de clorofila-a",
    )
    axis.fill_between(
        dates,
        np.maximum(0, comparable_mean - comparable_std),
        comparable_mean + comparable_std,
        color=COLORS[lake_key],
        alpha=0.14,
        label="± 1 desviación espacial",
    )
    if low_coverage.any():
        axis.scatter(
            dates[low_coverage],
            data.loc[low_coverage, "mean_cyano"],
            marker="X",
            s=75,
            color="#555555",
            zorder=4,
            label="Cobertura fuente <50% (no comparable)",
        )
    axis.set_title(f"Evolución temporal del indicador de cianobacteria - {lake.display_name}", pad=12)
    axis.set_xlabel("Fecha de adquisición Sentinel-2")
    axis.set_ylabel("Clorofila-a estimada (mg/m³)")
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    axis.tick_params(axis="x", rotation=35)
    _style_axes(axis)
    axis.legend(frameon=False, loc="best")
    output = FIGURES_DIR / f"temporal_{lake_key}.png"
    fig.savefig(output, dpi=300, facecolor="white")
    plt.close(fig)
    return output


def _plot_comparison(temporal: pd.DataFrame) -> Path:
    fig, axis = plt.subplots(figsize=(10, 5.4), constrained_layout=True)
    for lake_key, group in temporal.groupby("lake", sort=True):
        data = group.sort_values("date")
        dates = pd.to_datetime(data["date"])
        low_coverage = data["source_coverage_percent"] < 50.0
        axis.plot(
            dates,
            data["mean_cyano"].mask(low_coverage),
            marker="o",
            linewidth=2.0,
            markersize=5,
            color=COLORS[lake_key],
            label=LAKES[lake_key].display_name,
        )
        if low_coverage.any():
            axis.scatter(
                dates[low_coverage],
                data.loc[low_coverage, "mean_cyano"],
                marker="X",
                s=65,
                color=COLORS[lake_key],
                edgecolors="#333333",
                linewidths=0.6,
                zorder=4,
                label=f"{LAKES[lake_key].display_name}: cobertura <50%",
            )
    axis.set_title("Comparación temporal del indicador de cianobacteria", pad=12)
    axis.set_xlabel("Fecha de adquisición Sentinel-2")
    axis.set_ylabel("Clorofila-a estimada (mg/m³)")
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    axis.tick_params(axis="x", rotation=30)
    _style_axes(axis)
    axis.legend(frameon=False)
    output = FIGURES_DIR / "temporal_comparison.png"
    fig.savefig(output, dpi=300, facecolor="white")
    plt.close(fig)
    return output


def _plot_representative_map(lake_key: str, date: str) -> Path:
    source = PROCESSED_DIR / lake_key / date / "cyanobacteria.tif"
    with rasterio.open(source) as dataset:
        values = dataset.read(1, masked=True).astype("float32")
        bounds = dataset.bounds
    finite = values.compressed()
    if finite.size == 0:
        raise ValueError(f"Mapa vacío: {source}")
    vmin = max(0.0, float(np.percentile(finite, 2)))
    vmax = float(np.percentile(finite, 98))
    if vmax <= vmin:
        vmax = float(np.max(finite)) + 1e-6

    fig, axis = plt.subplots(figsize=(8.2, 6.1), constrained_layout=True)
    image = axis.imshow(
        values,
        extent=(bounds.left / 1000, bounds.right / 1000, bounds.bottom / 1000, bounds.top / 1000),
        origin="upper",
        cmap="turbo",
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
    )
    axis.set_facecolor("#EDF1F3")
    axis.set_title(
        f"Distribución del indicador de cianobacteria - {LAKES[lake_key].display_name}\n{date}",
        pad=10,
    )
    axis.set_xlabel("Este UTM 15N (km)")
    axis.set_ylabel("Norte UTM 15N (km)")
    axis.set_aspect("equal")
    colorbar = fig.colorbar(image, ax=axis, shrink=0.86, pad=0.025)
    colorbar.set_label("Clorofila-a estimada (mg/m³)")
    output = FIGURES_DIR / f"map_cyano_{lake_key}.png"
    fig.savefig(output, dpi=300, facecolor="white")
    plt.close(fig)
    return output


def _interpret(temporal: pd.DataFrame, critical: pd.DataFrame) -> dict:
    result: dict[str, dict] = {}
    for lake_key, group in temporal.groupby("lake", sort=True):
        data = group.sort_values("date")
        first, last = float(data.iloc[0]["mean_cyano"]), float(data.iloc[-1]["mean_cyano"])
        differences = np.diff(data["mean_cyano"].to_numpy(float))
        nonzero_signs = np.sign(differences[np.abs(differences) > 1e-9])
        sign_changes = int(np.count_nonzero(np.diff(nonzero_signs))) if nonzero_signs.size > 1 else 0
        if sign_changes >= 2:
            pattern = "fluctúa, con múltiples cambios entre aumentos y descensos"
        elif last > first:
            pattern = "termina por encima del valor inicial, aunque la serie no demuestra causalidad"
        elif last < first:
            pattern = "termina por debajo del valor inicial, aunque la serie no demuestra causalidad"
        else:
            pattern = "no muestra cambio neto entre la primera y la última observación"

        ranked = critical[critical["lake"] == lake_key].sort_values("rank_desc")
        top = ranked.iloc[0]
        statistical = ranked[ranked["is_statistical_peak"] & ~ranked["is_low_coverage"]]
        result[lake_key] = {
            "lake": LAKES[lake_key].display_name,
            "pattern": pattern,
            "first_date": data.iloc[0]["date"],
            "first_mean": first,
            "last_date": data.iloc[-1]["date"],
            "last_mean": last,
            "maximum_date": top["date"],
            "maximum_mean": float(top["mean_cyano"]),
            "maximum_median": float(top["median_cyano"]),
            "maximum_std": float(top["std_cyano"]),
            "maximum_percentile_rank": float(top["percentile_rank"]),
            "statistical_peak_dates": statistical["date"].tolist(),
            "low_coverage_dates": ranked.loc[
                ranked["is_low_coverage"], "date"
            ].tolist(),
            "valid_percent_range": [
                float(data["valid_percent"].min()),
                float(data["valid_percent"].max()),
            ],
            "caution": (
                "La serie es observacional y el indicador satelital es una estimación. "
                "Lluvia, temperatura o nutrientes son hipótesis, no causas probadas aquí."
            ),
        }
    (OUTPUTS_DIR / "temporal_interpretation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def run_analysis() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Genera todos los entregables estadísticos y visuales del ejercicio 4."""

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    temporal, critical = build_temporal_tables()
    for lake_key, group in temporal.groupby("lake", sort=True):
        _plot_lake_series(group, lake_key)
    _plot_comparison(temporal)
    for lake_key in LAKES:
        top = critical[critical["lake"] == lake_key].sort_values("rank_desc").iloc[0]
        _plot_representative_map(lake_key, str(top["date"]))
    interpretation = _interpret(temporal, critical)
    LOGGER.info("Análisis temporal y figuras completados")
    return temporal, critical, interpretation

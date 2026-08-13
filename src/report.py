"""Genera el informe de avance en Markdown y PDF profesional."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import matplotlib.font_manager as font_manager
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .config import DOCS_DIR, FIGURES_DIR, LAKES, OUTPUTS_DIR

CYAN = colors.HexColor("#00A9C0")
DARK = colors.HexColor("#183642")
PALE = colors.HexColor("#EAF6F8")
GREY = colors.HexColor("#52646D")


def _register_fonts() -> tuple[str, str]:
    regular = font_manager.findfont("DejaVu Sans")
    bold = font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans", weight="bold"))
    pdfmetrics.registerFont(TTFont("ReportSans", regular))
    pdfmetrics.registerFont(TTFont("ReportSans-Bold", bold))
    return "ReportSans", "ReportSans-Bold"


def _styles() -> dict[str, ParagraphStyle]:
    regular, bold = _register_fonts()
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.3,
            leading=13.4,
            textColor=DARK,
            spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.4,
            leading=10,
            textColor=GREY,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName=bold,
            fontSize=23,
            leading=28,
            textColor=DARK,
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Heading2"],
            fontName=regular,
            fontSize=13,
            leading=18,
            textColor=CYAN,
            alignment=TA_CENTER,
        ),
        "h1": ParagraphStyle(
            "Heading1",
            parent=base["Heading1"],
            fontName=bold,
            fontSize=15,
            leading=18,
            textColor=CYAN,
            spaceBefore=10,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "Heading2",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=11,
            leading=14,
            textColor=DARK,
            spaceBefore=8,
            spaceAfter=5,
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.5,
            leading=10,
            alignment=TA_CENTER,
            textColor=GREY,
            spaceAfter=9,
        ),
        "table": ParagraphStyle(
            "Table",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=6.6,
            leading=8,
            alignment=TA_LEFT,
        ),
        "table_bold": ParagraphStyle(
            "TableBold",
            parent=base["BodyText"],
            fontName=bold,
            fontSize=6.6,
            leading=8,
            textColor=colors.white,
        ),
    }


def _header_footer(canvas, document) -> None:
    canvas.saveState()
    width, height = letter
    canvas.setFillColor(CYAN)
    canvas.rect(0, height - 0.38 * inch, width, 0.38 * inch, stroke=0, fill=1)
    canvas.setFont("ReportSans-Bold", 8)
    canvas.setFillColor(colors.white)
    canvas.drawString(0.62 * inch, height - 0.25 * inch, "UVG | CC3084 Data Science | Laboratorio 4")
    canvas.setFont("ReportSans", 7.5)
    canvas.setFillColor(GREY)
    canvas.drawRightString(width - 0.6 * inch, 0.35 * inch, f"Página {document.page}")
    canvas.restoreState()


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def _figure(path: Path, caption: str, styles: dict) -> list:
    image = Image(str(path))
    max_width, max_height = 7.1 * inch, 4.35 * inch
    scale = min(max_width / image.imageWidth, max_height / image.imageHeight)
    image.drawWidth = image.imageWidth * scale
    image.drawHeight = image.imageHeight * scale
    image.hAlign = "CENTER"
    return [image, _p(caption, styles["caption"])]


def _summary_table(group: pd.DataFrame, styles: dict) -> Table:
    headers = ["Fecha", "Promedio", "Mediana", "Desv. est.", "Válido"]
    data = [[_p(value, styles["table_bold"]) for value in headers]]
    for _, row in group.sort_values("date").iterrows():
        values = [
            str(row["date"]),
            f"{row['mean_cyano']:.2f}",
            f"{row['median_cyano']:.2f}",
            f"{row['std_cyano']:.2f}",
            f"{row['valid_percent']:.1f}%",
        ]
        data.append([_p(value, styles["table"]) for value in values])
    table = Table(data, colWidths=[1.15 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch, 0.85 * inch], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), CYAN),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#AFC4CB")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _markdown(temporal: pd.DataFrame, critical: pd.DataFrame, interpretation: dict) -> str:
    partial_valid = float(
        temporal[(temporal["lake"] == "amatitlan") & (temporal["date"] == "2026-02-07")]
        .iloc[0]["valid_percent"]
    )
    lines = [
        "# Laboratorio 4 - Análisis de Datos Geoespaciales",
        "",
        "## Informe de avance: ejercicios 1 al 4",
        "",
        "**Curso:** CC3084 Data Science, Universidad del Valle de Guatemala  ",
        f"**Generado:** {date.today().isoformat()}  ",
        "**Audiencia:** ambientalistas sin conocimientos de programación",
        "",
        "## Resumen ejecutivo",
        "",
        "Se analizaron 22 adquisiciones reales Sentinel-2 (11 por lago) mediante APIs STAC. "
        "El procesamiento leyó únicamente ventanas de las bandas requeridas, aplicó el contorno de cada lago, "
        "control de nubes SCL y el algoritmo oficial Cyano Detection. El indicador numérico es una estimación "
        "satelital de clorofila-a asociada a floraciones de cianobacterias; no es una medición de laboratorio.",
        "",
        "## Metodología",
        "",
        "- **API:** Earth Search `sentinel-2-l1c` para bandas espectrales y Planetary Computer `sentinel-2-l2a` para SCL.",
        "- **Resolución:** grilla UTM 15N de 20 m; solo se leen las ventanas que intersectan el lago.",
        "- **NDVI:** `(B08-B04)/(B08+B04)`.",
        "- **NDWI:** `(B03-B08)/(B03+B08)`.",
        "- **Cyano Detection:** `NDCI=(B05-B04)/(B05+B04)` y "
        "`Chl-a=826.57*NDCI^3-176.43*NDCI^2+19*NDCI+4.071`, exactamente como en el script oficial.",
        "- **Máscaras:** contorno OSM; SCL 0, 1, 3, 7, 8, 9, 10 y 11; no-data; agua del script oficial; valores fuera del modelo 0-500 mg/m³.",
        "- **Importante:** los colores RGB del script oficial solo se usan como referencia visual; las estadísticas se calculan sobre el valor numérico de clorofila-a.",
        "",
        "## Resultados temporales",
        "",
    ]
    for lake_key in ("atitlan", "amatitlan"):
        group = temporal[temporal["lake"] == lake_key].sort_values("date")
        info = interpretation[lake_key]
        lines += [
            f"### {LAKES[lake_key].display_name}",
            "",
            "| Fecha | Promedio | Mediana | Desv. est. | Válido |",
            "|---|---:|---:|---:|---:|",
        ]
        for _, row in group.iterrows():
            lines.append(
                f"| {row['date']} | {row['mean_cyano']:.2f} | {row['median_cyano']:.2f} | "
                f"{row['std_cyano']:.2f} | {row['valid_percent']:.1f}% |"
            )
        lines += [
            "",
            f"![Serie temporal](../figures/temporal_{lake_key}.png)",
            "",
            f"El indicador {info['pattern']}. El máximo observado ocurrió el "
            f"**{info['maximum_date']}**, con promedio {info['maximum_mean']:.2f} mg/m³, "
            f"mediana {info['maximum_median']:.2f} y desviación espacial {info['maximum_std']:.2f}.",
            "",
            f"![Mapa representativo](../figures/map_cyano_{lake_key}.png)",
            "",
        ]
    lines += [
        "### Comparación entre lagos",
        "",
        "![Comparación temporal](../figures/temporal_comparison.png)",
        "",
        "Las fechas de adquisición no siempre coinciden entre lagos; la comparación muestra niveles relativos "
        "y variación temporal, pero no constituye un experimento causal.",
        "",
        "## Fechas críticas y criterio estadístico",
        "",
        "Se usaron dos criterios: z robusto basado en mediana/MAD (>=2) y límite superior de Tukey "
        "(Q3 + 1.5 IQR). Si el máximo no supera esos umbrales se reporta como máximo observado, no como pico estadístico. "
        "Una fecha con menos de 50% de cobertura fuente se marca como no comparable para inferir el lago completo.",
        "",
    ]
    for lake_key in ("atitlan", "amatitlan"):
        top = critical[critical["lake"] == lake_key].sort_values("rank_desc").head(3)
        lines.append(f"**{LAKES[lake_key].display_name}:** " + "; ".join(
            f"{row.date} (rango {int(row.rank_desc)}, promedio {row.mean_cyano:.2f}, {row.classification})"
            for row in top.itertuples()
        ))
        lines.append("")
    lines += [
        "## Limitaciones e interpretación responsable",
        "",
        "Atitlán 2025-04-13 y 2025-07-17 tienen solo 14.3% y 14.1% de cobertura fuente del polígono, respectivamente; "
        "se conservan por ser fechas obligatorias, pero sus promedios representan solo la fracción observada.\n\n"
        "El producto es un proxy de clorofila-a calibrado con datos sintéticos para *Microcystis aeruginosa*. "
        "El propio material oficial informa incertidumbre considerable; por eso no debe leerse como una concentración "
        "de laboratorio ni como confirmación toxicológica. Temperatura, lluvia, nutrientes, estancamiento o presión urbana "
        "son posibles explicaciones a comprobar con datos adicionales, no causas demostradas por estas imágenes.",
        "",
        f"La observación de Amatitlán del 2026-02-07 se conserva. El PDF advierte aproximadamente 57.1% para la "
        f"cobertura parcial de la imagen seleccionada; al mosaquear las dos teselas MGRS de la misma adquisición, "
        f"este procesamiento obtuvo {partial_valid:.1f}% válido dentro del polígono del lago. No se cambió la fecha ni "
        "se mezclaron adquisiciones.",
        "",
        "## Fuentes técnicas",
        "",
        "- [Cyano Detection oficial de Sentinel Hub](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/cyanobacteria_chla_ndci_l1c/)",
        "- [Sentinel-2 L2A y clases SCL](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html)",
        "- [API STAC de Planetary Computer](https://planetarycomputer.microsoft.com/docs/quickstarts/reading-stac/)",
        "- [OpenStreetMap / ODbL](https://www.openstreetmap.org/copyright)",
        "",
        "## Reproducibilidad",
        "",
        "Ejecute `python -m src.main` desde el entorno documentado en `README.md`. Los CSV, figuras, metadatos STAC "
        "y rasters GeoTIFF se generan automáticamente y son validados por `python -m src.validate`.",
        "",
    ]
    return "\n".join(lines)


def build_reports() -> tuple[Path, Path]:
    temporal = pd.read_csv(OUTPUTS_DIR / "cyano_temporal_stats.csv", dtype={"date": str})
    critical = pd.read_csv(OUTPUTS_DIR / "critical_dates.csv", dtype={"date": str})
    interpretation = json.loads(
        (OUTPUTS_DIR / "temporal_interpretation.json").read_text(encoding="utf-8")
    )
    partial_valid = float(
        temporal[(temporal["lake"] == "amatitlan") & (temporal["date"] == "2026-02-07")]
        .iloc[0]["valid_percent"]
    )
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    markdown_path = DOCS_DIR / "avance_lab4.md"
    markdown_path.write_text(
        _markdown(temporal, critical, interpretation), encoding="utf-8"
    )

    styles = _styles()
    pdf_path = DOCS_DIR / "avance_lab4.pdf"
    document = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.62 * inch,
        bottomMargin=0.58 * inch,
        title="Laboratorio 4 - Informe de avance ejercicios 1 al 4",
        author="CC3084 Data Science - UVG",
        subject="Análisis temporal de cianobacterias en Atitlán y Amatitlán",
    )
    story: list = [
        Spacer(1, 0.85 * inch),
        _p("Laboratorio 4", styles["title"]),
        _p("Análisis de Datos Geoespaciales", styles["subtitle"]),
        Spacer(1, 0.28 * inch),
        _p("Informe de avance - Ejercicios 1 al 4", styles["h1"]),
        Spacer(1, 0.35 * inch),
        _p("CC3084 Data Science | Universidad del Valle de Guatemala", styles["body"]),
        _p("Semestre II - 2026", styles["body"]),
        _p("Dirigido a ambientalistas sin conocimientos de programación", styles["body"]),
        Spacer(1, 0.5 * inch),
        _p(
            "22 adquisiciones Sentinel-2 | 2 lagos | 3 productos espectrales por fecha",
            styles["subtitle"],
        ),
        PageBreak(),
        _p("Resumen ejecutivo", styles["h1"]),
        _p(
            "Se procesaron las 11 fechas oficiales para el lago Atitlán y las 11 para el lago Amatitlán. "
            "Las conexiones STAC fueron reales y los rasters se leyeron desde activos Sentinel-2 L1C/L2A emparejados. "
            "Para reducir descarga y almacenamiento solo se solicitaron ventanas de las bandas necesarias. El indicador "
            "de cianobacteria es una estimación de clorofila-a: sirve para comparar patrones relativos, pero no sustituye "
            "un muestreo de laboratorio.",
            styles["body"],
        ),
        _p("1. Qué se estudió", styles["h1"]),
        _p(
            "Atitlán y Amatitlán son sistemas acuáticos de alta importancia ecológica, social y económica. Sentinel-2 "
            "observa la luz reflejada en varias longitudes de onda y permite reconocer cambios compatibles con vegetación, "
            "agua y pigmentos fotosintéticos. Este avance analiza cómo varió el indicador de cianobacteria entre fechas, "
            "acompañado por NDVI y NDWI como productos auxiliares.",
            styles["body"],
        ),
        _p("2. Datos y conexión", styles["h1"]),
        _p(
            "Se usó Earth Search para las bandas L1C requeridas por el script y Planetary Computer para SCL L2A. Cada "
            "consulta se restringió al polígono del lago, fecha exacta y satélite especificado en el PDF. Cuando el lago "
            "cruzó una frontera MGRS se mosaquearon únicamente las teselas de la misma adquisición. Los metadatos completos "
            "quedaron en outputs/stac y outputs/scene_manifest.csv.",
            styles["body"],
        ),
        _p("3. Metodología", styles["h1"]),
        _p("Índices espectrales", styles["h2"]),
        _p(
            "NDVI = (B08-B04)/(B08+B04) y NDWI = (B03-B08)/(B03+B08). Las divisiones con denominador cero "
            "se marcaron como no válidas; además se rechazaron valores fuera de [-1, 1].",
            styles["body"],
        ),
        _p("Indicador de cianobacteria", styles["h2"]),
        _p(
            "Se reprodujo el script oficial Cyanobacteria Chlorophyll-a NDCI de Sentinel Hub. Primero calcula "
            "NDCI=(B05-B04)/(B05+B04) y luego Chl-a=826.57·NDCI³-176.43·NDCI²+19·NDCI+4.071. "
            "El script original transforma ese resultado en colores; aquí el promedio se calculó sobre el valor numérico "
            "anterior a la paleta, nunca sobre RGB. La unidad reportada es mg/m³, numéricamente equivalente a µg/L.",
            styles["body"],
        ),
        _p("Máscaras y calidad", styles["h2"]),
        _p(
            "Se aplicó el contorno del cuerpo de agua obtenido de OpenStreetMap, la máscara de agua interna del script y "
            "la clasificación SCL. Se excluyeron: 0 no-data; 1 saturado/defectuoso; 3 sombra de nube; 7 no clasificado; "
            "8 nube media; 9 nube alta; 10 cirrus; y 11 nieve/hielo. Se registraron cobertura fuente, píxeles claros, agua "
            f"detectada, píxeles válidos y porcentaje enmascarado. La fecha 2026-02-07 de Amatitlán se mantuvo: el PDF "
            f"advierte ~57.1% para una cobertura parcial; al mosaquear las dos teselas MGRS de la misma adquisición se "
            f"obtuvo {partial_valid:.1f}% válido dentro del contorno. No se mezclaron fechas ni adquisiciones.",
            styles["body"],
        ),
        PageBreak(),
        _p("4. Resultados temporales", styles["h1"]),
    ]

    for lake_key in ("atitlan", "amatitlan"):
        group = temporal[temporal["lake"] == lake_key].sort_values("date")
        info = interpretation[lake_key]
        story += [
            _p(LAKES[lake_key].display_name, styles["h2"]),
            _summary_table(group, styles),
            Spacer(1, 0.12 * inch),
            *_figure(
                FIGURES_DIR / f"temporal_{lake_key}.png",
                "La línea muestra el promedio por fecha y el área semitransparente representa ±1 desviación estándar espacial.",
                styles,
            ),
            _p(
                f"La serie {info['pattern']}. El máximo observado fue {info['maximum_date']}: promedio "
                f"{info['maximum_mean']:.2f} mg/m³, mediana {info['maximum_median']:.2f} y desviación espacial "
                f"{info['maximum_std']:.2f}. El porcentaje válido varió entre {info['valid_percent_range'][0]:.1f}% "
                f"y {info['valid_percent_range'][1]:.1f}%.",
                styles["body"],
            ),
            *_figure(
                FIGURES_DIR / f"map_cyano_{lake_key}.png",
                "Mapa de la fecha con el promedio más alto. Las áreas grises están fuera del lago o fueron enmascaradas.",
                styles,
            ),
        ]

    story += [
        PageBreak(),
        _p("5. Comparación y fechas críticas", styles["h1"]),
        *_figure(
            FIGURES_DIR / "temporal_comparison.png",
            "Comparación de promedios. Las fechas no siempre coinciden, por lo que las líneas describen cada serie y no pares simultáneos.",
            styles,
        ),
        _p(
            "Los picos se evaluaron con dos reglas reproducibles: z robusto basado en mediana/MAD (umbral 2) y límite "
            "superior de Tukey (Q3 + 1.5 IQR). El máximo de cada lago siempre se informa, pero solo se denomina pico "
            "estadístico si supera al menos uno de esos umbrales. Las fechas con menos de 50% de cobertura fuente se "
            "marcan como no comparables para inferir el lago completo.",
            styles["body"],
        ),
    ]
    for lake_key in ("atitlan", "amatitlan"):
        top = critical[critical["lake"] == lake_key].sort_values("rank_desc").head(3)
        text = "; ".join(
            f"{row.date}: {row.mean_cyano:.2f} mg/m³ ({row.classification.replace('_', ' ')})"
            for row in top.itertuples()
        )
        story += [_p(LAKES[lake_key].display_name, styles["h2"]), _p(text + ".", styles["body"])]

    story += [
        _p("6. Interpretación y límites", styles["h1"]),
        _p(
            "Atitlán 2025-04-13 y 2025-07-17 cubren solo 14.3% y 14.1% del polígono. Se conservaron porque el PDF "
            "exige esas fechas, pero sus promedios describen únicamente el sector observado y no deben usarse para "
            "afirmar un pico de todo el lago.",
            styles["body"],
        ),
        _p(
            "Las series muestran variación temporal del indicador, no una demostración de causa. Temperatura, lluvia, "
            "nutrientes, estancamiento y presión urbana pueden estar asociados a floraciones, pero este avance no incluye "
            "esas variables. Cualquier explicación causal requiere contrastarlas con mediciones meteorológicas y de agua.",
            styles["body"],
        ),
        _p(
            "El algoritmo fue calibrado con datos sintéticos de Microcystis aeruginosa y el material oficial comunica "
            "incertidumbre elevada. Los resultados son útiles para priorizar fechas y zonas de muestreo, no para declarar "
            "toxicidad ni reemplazar análisis de laboratorio.",
            styles["body"],
        ),
        _p("7. Reproducibilidad y fuentes", styles["h1"]),
        _p(
            "El repositorio contiene configuración, scripts, pruebas, metadatos STAC, CSV, figuras y documentación. "
            "El pipeline completo se ejecuta con <b>python -m src.main</b> y se comprueba con "
            "<b>python -m src.validate</b>. Los GeoTIFF pesados se excluyen de Git, pero se regeneran desde la API.",
            styles["body"],
        ),
        _p(
            '<link href="https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/cyanobacteria_chla_ndci_l1c/">'
            "Sentinel Hub - Cyanobacteria Chlorophyll-a NDCI</link><br/>"
            '<link href="https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html">'
            "Copernicus Data Space - Sentinel-2 L2A y SCL</link><br/>"
            '<link href="https://planetarycomputer.microsoft.com/docs/quickstarts/reading-stac/">'
            "Planetary Computer - lectura STAC</link><br/>"
            '<link href="https://earth-search.aws.element84.com/v1">Earth Search - catálogo L1C</link><br/>'
            '<link href="https://www.openstreetmap.org/copyright">OpenStreetMap - atribución ODbL</link>',
            styles["body"],
        ),
    ]
    document.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return markdown_path, pdf_path

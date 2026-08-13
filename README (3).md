# Laboratorio 4 - Análisis de Datos Geoespaciales

Avance completo de los ejercicios 1 al 4 de CC3084 Data Science (UVG, Semestre II 2026). El proyecto consulta observaciones reales Sentinel-2, procesa exclusivamente las 11 fechas oficiales de cada lago y genera el indicador numérico de cianobacteria, NDVI, NDWI, estadísticas temporales, figuras e informe PDF. Las bandas espectrales provienen de L1C, como exige el título y calibración del script oficial; la clasificación SCL de la adquisición L2A correspondiente controla nubes y sombras.

## Estado

El resultado de la última ejecución se resume en [STATUS.md](STATUS.md). Ningún resultado sintético se presenta como observación: los CSV y mapas provienen de los GeoTIFF procesados desde escenas Sentinel-2.

## Productos y fórmulas

- NDVI: `(B08 - B04) / (B08 + B04)`.
- NDWI: `(B03 - B08) / (B03 + B08)`.
- Cyano Detection oficial: usa B02, B03, B04, B05, B07, B08, B8A, B11 y B12. El valor analítico conservado es la estimación de clorofila-a calculada con NDCI por el propio script.
- Calidad: SCL a 20 m, contorno poligonal del lago, máscara de agua del script oficial, no-data y rango válido del modelo.

Los colores RGB del evalscript oficial son una paleta de visualización y nunca se promedian. Consulte [docs/metodologia_cyano.md](docs/metodologia_cyano.md) para la adaptación numérica y sus límites.

## Fuente de datos y conexión

Se usan dos catálogos STAC públicos para productos de una misma adquisición:

- [Element 84 Earth Search](https://earth-search.aws.element84.com/v1), colección `sentinel-2-l1c`, para las nueve bandas TOA que ejecutan el script oficial.
- [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/docs/quickstarts/reading-stac/), colección `sentinel-2-l2a`, para validar fecha/satélite y leer SCL.

Esta implementación se eligió sobre openEO porque permite conexiones anónimas comprobables, búsqueda exacta por fecha/plataforma/geometría y lectura remota por ventanas. Los datos son productos Copernicus Sentinel-2; los servicios funcionan como catálogos y mecanismos de acceso.

No se descargan escenas completas. Rasterio lee únicamente las ventanas que intersectan el polígono de cada lago y el pipeline conserva solamente los productos derivados. La evidencia de conexión queda en `outputs/api_connection.json` y la selección de escenas en `outputs/scene_manifest.csv` y `outputs/stac/`.

La firma de activos usa tokens públicos temporales generados por Planetary Computer. No requiere cuenta, contraseña ni API key.

## Instalación

Requiere Python 3.11 o posterior y acceso a internet durante la etapa de datos.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env  # opcional; no contiene secretos
```

`.env` está ignorado por Git. Si se cambia a otro proveedor compatible, configure `STAC_API_URL` y `STAC_COLLECTION` sin versionar credenciales.

## Ejecución completa

```bash
python -m src.main
```

Equivalente:

```bash
make all
```

La ejecución es reanudable. Si una observación ya tiene los tres GeoTIFF, las máscaras y `metadata.json`, se reutiliza. Para regenerar rasters:

```bash
python -m src.main --stage data --force
```

Etapas individuales:

```bash
python -m src.main --stage data
python -m src.main --stage analysis
python -m src.main --stage report
python -m src.main --stage validate
```

## Qué hace cada etapa

1. Descarga y valida los contornos OSM de Atitlán y Amatitlán.
2. Conecta con STAC y verifica la colección Sentinel-2 L2A.
3. Busca en ambos catálogos solo la fecha y el satélite indicados en el PDF; mosaica las teselas MGRS de una misma adquisición si son necesarias.
4. Lee por ventana las nueve bandas L1C mínimas y SCL de L2A.
5. Convierte DN a reflectancia. Para baseline 04.00 o posterior aplica `(DN - 1000) / 10000` y recorta negativos a cero, reproduciendo `harmonizeValues=true` de Sentinel Hub.
6. Calcula el Cyano Detection oficial, NDVI y NDWI, controlando denominadores cero.
7. Enmascara exterior del lago, nubes, cirrus, sombras, no-data y valores inválidos.
8. Escribe GeoTIFF georreferenciados, métricas, tablas, detección robusta de picos y figuras PNG a 300 dpi.
9. Genera `docs/avance_lab4.md` y `docs/avance_lab4.pdf`.
10. Valida fechas, satélites, CRS, alineación, rangos, estadísticas, figuras, PDF y ausencia de secretos.

## Máscaras y tratamiento de calidad

Las clases SCL excluidas son:

| Código | Categoría |
|---:|---|
| 0 | No-data |
| 1 | Saturado o defectuoso |
| 3 | Sombra de nube |
| 7 | No clasificado / baja probabilidad |
| 8 | Nube de probabilidad media |
| 9 | Nube de probabilidad alta |
| 10 | Cirrus |
| 11 | Nieve o hielo |

Además se exige pertenencia al polígono OSM y a la máscara de agua calculada por la función `wbi` del script Cyano Detection. Cada fecha registra píxeles totales, cobertura fuente, píxeles claros, agua detectada, píxeles válidos y porcentaje enmascarado. Amatitlán 2026-02-07 se conserva expresamente aunque tenga cobertura parcial.

## Validación y pruebas

```bash
python -m pytest -q
python -m src.validate
```

La validación falla si aparece una fecha distinta, falta cualquiera de las 22 observaciones, un satélite no coincide, un raster no abre, los CRS/transformaciones no se alinean, NDVI/NDWI salen de `[-1,1]`, el indicador sale de `[0,500]`, hay arrays vacíos o faltan figuras/informe.

## Estructura

```text
.
├── data/
│   ├── aoi/                     # GeoJSON pequeños y versionables
│   └── processed/               # GeoTIFF reproducibles, ignorados por Git
│       └── <lago>/<fecha>/
│           ├── cyanobacteria.tif
│           ├── ndvi.tif
│           ├── ndwi.tif
│           ├── masks.tif
│           └── metadata.json
├── docs/
│   ├── avance_lab4.md
│   ├── avance_lab4.pdf
│   ├── metodologia_cyano.md
│   └── requisitos_pdf.md
├── figures/                     # PNG a 300 dpi
├── outputs/                     # CSV, STAC, evidencia API y validación
├── src/                         # Pipeline modular
├── tests/                       # Pruebas unitarias
├── .env.example
├── Makefile
└── requirements.txt
```

## Datos grandes y Git

`data/processed/`, `data/cache/`, `.venv/`, `tmp/` y `.env` están ignorados. Se versionan el código, AOI, metadatos STAC, CSV, figuras, documentación e informe. Para regenerar los rasters basta ejecutar la etapa `data` con internet.

## Fuentes principales

- [Sentinel Hub - Cyanobacteria Chlorophyll-a NDCI](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/cyanobacteria_chla_ndci_l1c/)
- [Copernicus Data Space - Sentinel-2 L2A y SCL](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html)
- [Copernicus - offset radiométrico de baseline 04.00](https://sentinels.copernicus.eu/-/deployment-of-sentinel-2-processing-baseline-04.00-and-provision-of-new-sample-products)
- [Planetary Computer - API STAC](https://planetarycomputer.microsoft.com/docs/quickstarts/reading-stac/)
- [OpenStreetMap - licencia y atribución](https://www.openstreetmap.org/copyright)

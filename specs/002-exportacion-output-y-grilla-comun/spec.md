# Spec 002: Grilla Común de Profundidad y Exportación a `output/` (Excel Maestro + CSV por Pozo + Registros en Tiempo)

## 1. Objetivo
Construir el pipeline de homogeneización y exportación hacia la carpeta `output/` que:
1. Genere y actualice incrementalmente un archivo Excel maestro (`output/well_metadata.xlsx`) con los metadatos regularizados de cada pozo (`File`, `Well`, `UWI`, `Start`, `Stop`, `Step`, `X`, `Y`, `Elevation`) y la matriz booleana (`yes` / `no`) de disponibilidad de todas las curvas (incluyendo repeticiones `:2`, `:3`...).
2. Genere en `output/wells/` un archivo CSV unificado por pozo (`<Well>_selected.csv`) sobre una grilla común de profundidad (`DEPTH`) calculada con **el paso más chico** entre los `.las` de profundidad del pozo, usando `NaN` para valores faltantes e incluyendo las columnas fijas `DEPTH`, `nombre_pozo`, `UWI`, `XWELL`, `YWELL`, `COTA` seguidas de todas las curvas del pozo.
3. Separe y exporte los registros medidos en función del tiempo (`TIME.S`, ej. estaciones de presión `XPT`) en una carpeta dedicada `output/time_logs/` (un resumen maestro `xpt_stations_summary.csv` con profundidad estacionaria y presiones finales + los CSV de series de tiempo por pozo).

---

## 2. Requerimientos Funcionales (RF)

- **RF-1 (Normalización Geodésica y de UWI post-LLM)**:
  - **Coordenadas `X` (`XWELL`) e `Y` (`YWELL`)**: En la Cuenca del Golfo San Jorge (Gauss-Krüger Faja 2), `X` (Este) corresponde al rango `~2.48x.xxx` e `Y` (Norte) al rango `~4.83x.xxx`. Si el encabezado `.LAS` o el LLM entregan `X` e `Y` invertidas (`X > 3.500.000` e `Y < 3.500.000`), Python debe intercambiarlas automáticamente y redondear `XWELL` / `YWELL` a entero (y `COTA` / `Elevation` a 2 decimales).
  - **Nombre del pozo (`Well`)**: Normalizar puntos espurios antes del guion (ej. `YPF.SC.CV.a-38` o `YPF.SC.CVa.-24` -> `YPF.SC.CVa-38`, `YPF.SC.CVa-24`).
  - **UWI de 14 caracteres**: Mantener en mayúsculas; si comienza con `AR01` y tiene 12 caracteres (ej. `AR0100008500`, `AR0100008327`, `AR0100008372`), completar con `00` a la derecha para estandarizar a 14 caracteres (`AR010000850000`, `AR010000832700`, `AR010000837200`).

- **RF-2 (Grilla Común de Profundidad por Pozo)**:
  - Para cada pozo, tomar únicamente los archivos `.LAS` clasificados como `DEPTH`.
  - Determinar el paso efectivo de cada archivo: si `STEP > 0` usar `STEP`; si `STEP == 0` en el encabezado (como ocurre en archivos Baker Atlas de `CVa-19` y `CVa-24`), calcular la mediana de `abs(diff(DEPTH))` sobre las filas válidas (`~0.1524 m`).
  - Construir la grilla común del pozo desde `Start = min(valid_depths)` hasta `Stop = max(valid_depths)` usando **`Step = min(effective_steps)`** (el paso más chico del pozo).
  - Interpolar linealmente cada curva únicamente dentro de su tramo válido `[valid_min_depth, valid_max_depth]` sin extrapolar fuera de sus extremos y respetando brechas internas mayores a `2 * file_step` como `NaN`.

- **RF-3 (Gestión de Curvas Repetidas `:2`, `:3`...)**:
  - Por defecto (`repeat_strategy="keep_all"`), conservar todas las apariciones de curvas repetidas renombradas con `:2`, `:3`, `:4` tanto dentro del mismo `.las` como entre distintos `.las` del mismo pozo.
  - Soportar adicionalmente `repeat_strategy="merge"` (fusión en una única columna priorizando la corrida de menor paso / mayor cobertura) para cuando el usuario decida unificarlas.

- **RF-4 (Exportación de CSV por Pozo en `output/wells/`)**:
  - Guardar `output/wells/<Well>_selected.csv` con las columnas iniciales fijas:
    `DEPTH,nombre_pozo,UWI,XWELL,YWELL,COTA` seguidas de las curvas del pozo.
  - Representar los valores faltantes explícitamente con la cadena `NaN`.

- **RF-5 (Exportación Incremental del Excel Maestro `output/well_metadata.xlsx`)**:
  - Columnas fijas iniciales: `File`, `Well`, `UWI`, `Start`, `Stop`, `Step`, `X`, `Y`, `Elevation`.
  - Columnas dinámicas subsiguientes: unión ordenada alfabéticamente de todas las curvas presentes en los pozos procesados (incluyendo `:2`, `:3`...), con valor `"yes"` si el pozo posee datos válidos en esa curva y `"no"` en caso contrario.
  - Si `output/well_metadata.xlsx` ya existe, incorporar o actualizar la fila del pozo sin perder los pozos previamente registrados.

- **RF-6 (Exportación de Registros en Tiempo en `output/time_logs/`)**:
  - Para todo archivo `.LAS` clasificado como `TIME` (`TIME.S`, ej. estaciones de presión `XPT`):
    1. Exportar el archivo limpio en `output/time_logs/<Well>/<las_stem>.csv` con `TIME,nombre_pozo,UWI,XWELL,YWELL,COTA,STATION_DEPTH_M` + curvas del registro en tiempo (`NaN` para nulos).
    2. Actualizar la tabla resumen `output/time_logs/xpt_stations_summary.csv` con una fila por estación (`Well`, `UWI`, `XWELL`, `YWELL`, `COTA`, `File`, `Station_Depth_m`, `Start_Time_s`, `Stop_Time_s`, `Duration_s`, `Valid_Samples` y presiones/temperaturas finales de estación).

---

## 3. Requerimientos No Funcionales (RNF)
- **RNF-1 (Inmutabilidad de `Input/`)**: `Input/` permanece estrictamente de solo lectura.
- **RNF-2 (Validación Visual)**: Generar en `tests/002-exportacion-output-y-grilla-comun/images/`:
  1. `excel_curve_availability_matrix.png`: mapa visual de la matriz `yes`/`no` exportada al Excel.
  2. `<Well>_unified_vs_raw.png` para cada pozo: comparación superpuesta entre los puntos originales de los `.LAS` y la curva resultante en el CSV unificado (`output/wells/<Well>_selected.csv`) para verificar que la interpolación al paso más chico no introduzca artefactos ni extrapolaciones.
  3. `xpt_time_stations_overview.png`: gráfico de las estaciones de presión en tiempo (`CVa-37` y `CVa-39`) en función de su profundidad estacionaria.

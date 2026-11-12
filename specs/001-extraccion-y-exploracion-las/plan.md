# Plan Técnico — Spec 001: Extracción de Encabezados, Exploración de Curvas y Validación Visual

## 1. Archivos a crear y responsabilidades

| Archivo | Responsabilidad | RF cubiertos |
| :--- | :--- | :--- |
| `scripts/las_explorer.py` | Escaneo de carpetas de pozo, lectura tolerante a `latin-1`/`cp1252` con `lasio` + extracción del texto crudo de encabezado antes de `~A`, clasificación `DEPTH` vs `TIME`, detección de curvas repetidas (`:2`, `:3`) y armado del bloque de encabezado por pozo. | RF-1, RF-2, RF-3, RF-4, RF-5 |
| `scripts/llm_extractor.py` | Gestión del motor local `model/bin/llama-server.exe` sobre GPU `Vulkan1` (`NVIDIA GeForce RTX 4060 Laptop GPU`, `-ngl 99`) con el modelo `model/gemma-4-E4B-it-Q4_K_M.gguf` y extracción estructurada en JSON (`nombre_pozo`, `uwi`, `coord_x`, `coord_y`, `cota`) + respaldo determinista en Python. | RF-6, RNF-2 |
| `scripts/visual_validator.py` | Generación de gráficos en `tests/001-extraccion-y-exploracion-las/images/`: (1) Diagrama de cobertura de profundidades/pasos (`STEP`) y estaciones en tiempo (`XPT`) por archivo `.las`, y (2) Gráficos multi-track de curvas en función de la profundidad destacando curvas repetidas (`:2`, `:3`). | RF-7 |
| `tests/001-extraccion-y-exploracion-las/test_spec_001.py` | Suite de pruebas con `pytest` que valida todos los RF sobre los 6 pozos de `Input/`, guarda el log completo en `exploration_report.log` / `exploration_summary.json` y genera las imágenes en `tests/001-extraccion-y-exploracion-las/images/`. | RF-1 a RF-7, RNF-1 |

## 2. Algoritmo y Decisiones Técnicas

### A. Separación de encabezado y lectura con `lasio` (RF-1, RF-2, RF-3)
- Se listan los archivos de la carpeta del pozo filtrando `path.suffix.lower() == ".las"`.
- Para el bloque del LLM: se lee el archivo línea por línea (intentando `utf-8` y cayendo a `latin-1`/`cp1252`) deteniéndose en cuanto una línea comienza con `~A` (case-insensitive). Así jamás se carga ni se envía la matriz numérica al LLM.
- Para el análisis de curvas y datos: se abre con `lasio.read(filepath, encoding="latin-1")` (o autodetectado), reemplazando el valor `NULL` (ej. `-999.25`) por `np.nan`.

### B. Clasificación `DEPTH` vs `TIME` (RF-4)
- Se inspecciona la primera curva (`las.curves[0]`) y el parámetro `STRT` en `~Well`:
  - Si `las.curves[0].mnemonic.upper() in ("TIME", "ETIM")` o la unidad de `STRT` es `S` / `MS` (segundos/milisegundos): se clasifica como `TIME`.
  - Si existe una curva de profundidad estacionaria (`PDEP_XPT`, `DEPT_SL` o `TDEP`), se calcula la mediana de profundidad en metros (convirtiendo `.1IN` a metros con factor $0.00254$ si no está `PDEP_XPT.M`).
  - En caso contrario, se clasifica como `DEPTH` y se calcula tanto el rango declarado (`STRT` a `STOP`) como el rango efectivo con datos no nulos.

### C. Detección de curvas repetidas `:2`, `:3` (RF-5)
- Se lleva un contador de mnemónicos por pozo entre todos los archivos `.las` de tipo `DEPTH` (excluyendo la curva índice `DEPT`).
- La primera aparición conserva su nombre (`GR`, `CAL`, `M2R6`); la segunda se registra como `GR:2`, la tercera `GR:3`, almacenando archivo de origen, unidad, descripción, `STEP` y rango `[min_depth, max_depth]`.

### D. Inferencia Local con `llama.cpp` en RTX 4060 (RF-6, RNF-2)
- `model/bin/llama-server.exe` se inicia apuntando a `model/gemma-4-E4B-it-Q4_K_M.gguf` con `--device Vulkan1 -ngl 99 --port 8089 --ctx-size 8192`.
- Se envía el bloque consolidado de encabezados (`~Well` y `~Parameter` de los `.las` del pozo) con temperatura `0.0` y esquema JSON (`response_format={"type": "json_object"}`) para extraer `nombre_pozo`, `uwi`, `coord_x`, `coord_y`, `cota` y `referencia_cota`.

## 3. Estrategia de Verificación
- Ejecutar `pytest tests/001-extraccion-y-exploracion-las/test_spec_001.py -s`.
- Verificar los logs en `tests/001-extraccion-y-exploracion-las/exploration_report.log` y los gráficos generados en `tests/001-extraccion-y-exploracion-las/images/`.

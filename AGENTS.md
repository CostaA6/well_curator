# AGENTS.md — Well Curator (.LAS Dataset Curation)

Aplicativo local en Python + LLM (`Gemma-4-E4B` GGUF en GPU RTX 4060) con interfaz gráfica sencilla para curar archivos `.las` por pozo, extrayendo metadatos (Pozo, UWI, X, Y, Cota), matriz de curvas en Excel y datasets homogeneizados (CSV/JSON).

## Stack y estructura
- **Python 3.14**: `lasio`, `pandas`, `numpy`, `openpyxl`, `matplotlib` (validación visual), UI ligera (ej. `streamlit`), inferencia local sobre RTX 4060 sin depender de LM Studio (`llama.cpp` / `llama-server.exe` en `model/bin/`).
- `Input/`: carpetas por pozo (`YPF.SC.CVa-*`) con múltiples `.las`/`.LAS` (solo lectura).
- `output/`: salidas curadas que se actualizan incrementalmente a medida que se procesan pozos:
  - `output/well_metadata.xlsx`: tabla maestra (`File`, `Well`, `UWI`, `Start`, `Stop`, `Step`, `X`, `Y`, `Elevation` + matriz alfabética de curvas con `yes`/`no`).
  - `output/wells/<Well>_selected.csv`: un archivo CSV por pozo sobre grilla común de profundidad (`DEPTH,nombre_pozo,UWI,XWELL,YWELL,COTA` + curvas con `NaN` para valores nulos).
  - `output/time_logs/`: registros medidos en función del tiempo (`xpt_stations_summary.csv` + CSVs por estación en `output/time_logs/<Well>/`).
- `model/`: modelo local `gemma-4-E4B-it-Q4_K_M.gguf` y binarios `model/bin/`.
- `scripts/`: `las_explorer.py`, `llm_extractor.py`, `well_exporter.py`, `visual_validator.py`.
- `docs/constitution.md`: principios innegociables del proyecto.
- `specs/NNN-nombre/` y `tests/NNN-nombre/` (con subcarpeta `images/` para inspección visual).

## Comandos
- Tests: `$env:PYTHONPATH="."; python -m pytest`

## Reglas de dominio / trampas conocidas (.LAS)
- **Separación de responsabilidades (Texto Crudo -> LLM | Curvas -> `lasio`)**:
  - **Encabezados (sin `lasio`)**: Python corta el texto crudo antes de `~A` con triple barrera de seguridad (1: `~A`/`~ASCII`/`~LOG_DATA`, 2: detector de fila numérica $\ge 3$ floats tras `~Curve`, 3: tope de 500 líneas) y se lo envía directo al LLM.
  - **Curvas numéricas (`lasio`)**: `lasio` se usa exclusivamente para leer las columnas de datos (`las.df()`) debajo de `~A`.
- **Metadatos y normalización geodésica**:
  - `X` (`XWELL`) es siempre la coordenada Este (`~2.481.xxx`) e `Y` (`YWELL`) es siempre la coordenada Norte (`~4.834.xxx`) en Gauss-Krüger Faja 2.
  - `UWI` en mayúsculas y normalizado a 14 caracteres (`AR01...00`).
  - `Elevation` / `COTA` en metros sobre nivel del terreno (`~292-294 m`).
- **Grilla común de profundidad**: usa **el paso más chico** entre los `.las` de profundidad del pozo (`0.0762 m` = 1/4 pie cuando Baker declara `STEP=0.0`, `0.1524 m` = 1/2 pie, o `0.5 m`), sin extrapolar fuera del rango válido de cada curva y usando `NaN` para nulos.
- **Curvas repetidas**: por defecto se conservan todas con sufijo `:2`, `:3`, `:4` (`repeat_strategy="keep_all"`), soportando también `repeat_strategy="merge"`.

## Memoria y SDD
- Al empezar, lee `MEMORY.md`, `docs/constitution.md` y la spec activa (`specs/NNN-*/`) antes de tocar código.
- Al terminar una tarea o fase, actualiza `MEMORY.md` (máx. ~50 líneas).

## Límites
- ✅ **Siempre**: separar encabezados en Python; generar gráficos de verificación en `tests/NNN-*/images/`; actualizar `MEMORY.md` al cerrar cada paso.
- ⚠️ **Pregunta antes**: añadir dependencias nuevas, cambiar formato de `output/`.
- 🚫 **Nunca**: modificar o borrar archivos originales dentro de `Input/`.

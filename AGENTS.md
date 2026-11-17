# AGENTS.md — Well Curator (.LAS Dataset Curation)

Aplicativo local en Python + LLM (agnóstico de modelo: `Gemma-4-E4B` GGUF en GPU RTX 4060 vía `llama.cpp`, Ollama/LM Studio o APIs externas OpenAI-compatible) para curar archivos `.las` por pozo en cualquier campo petrolero.

## Stack y estructura
- **Python 3.14**: `lasio`, `pandas`, `numpy`, `openpyxl`, `pyproj` (proyección geodésica POSGAR 2007 / UTM), `matplotlib` (validación visual), `urllib` (cliente OpenAI-compatible sin dependencias extra).
- `config/fields/`: perfiles de campo configurables (`generic.json`, `golfo_san_jorge_faja2.json`).
- `Input/`: carpetas por pozo con múltiples `.las`/`.LAS` (solo lectura).
- `output/`: salidas curadas que se actualizan incrementalmente:
  - `output/well_metadata.xlsx`: tabla maestra (`File`, `Well`, `UWI`, `Start`, `Stop`, `Step`, `X`, `Y`, `Elevation` + matriz alfabética `yes`/`no`).
  - `output/wells/<Well>_selected.csv`: CSV por pozo sobre grilla común en metros (`DEPTH,nombre_pozo,UWI,XWELL,YWELL,COTA` + curvas con `NaN`).
  - `output/time_logs/`: registros medidos en función del tiempo (`xpt_stations_summary.csv` + CSVs por estación).
  - `output/metadata_cache/`, `output/batch_run_report.json`, `output/batch_errors.log`: trazabilidad, caché e incrementalidad batch.
- `scripts/`: `las_explorer.py`, `field_profiles.py`, `llm_extractor.py`, `well_exporter.py`, `visual_validator.py`, `run_pipeline.py`.
- `docs/constitution.md`: principios innegociables del proyecto.
- `specs/NNN-nombre/` y `tests/NNN-nombre/` (con subcarpeta `images/`).

## Comandos
- Tests completos o por spec: `$env:PYTHONPATH="."; python -m pytest tests/003-escalabilidad-campos-y-modelos/test_spec_003.py -v`
- Pipeline CLI: `python scripts/run_pipeline.py --field-profile generic --incremental`

## Reglas de dominio / trampas conocidas (.LAS)
- **Separación de responsabilidades (Texto Crudo -> LLM | Curvas -> `lasio`)**: encabezado puro cortado con triple barrera (`~A`/`~ASCII`/`~LOG_DATA`, detector numérico tras `~Curve`, tope 500 líneas) directo al LLM; `lasio` exclusivo para `las.df()`.
- **Geodesia multi-campo (`field_profiles.py`)**: prioriza Gauss-Krüger si existe (corrigiendo X/Y invertidas); si solo hay `Lat/Lon`, `pyproj` proyecta automáticamente a POSGAR 2007 Fajas 1–7 (`EPSG:5343..5349`) en Argentina o UTM WGS84 fuera de Argentina (con `forced_epsg` opcional para bordes de faja).
- **Unidades y grilla común**: conversión automática `FT/FEET/F -> M` (`* 0.3048`), grilla al **paso más chico** del pozo sin extrapolación y `NaN` para nulos.
- **Curvas repetidas**: por defecto `repeat_strategy="keep_all"` (`:2`, `:3`, `:4`), soportando `repeat_strategy="merge"`.

## Memoria y SDD
- Al empezar, lee `MEMORY.md`, `docs/constitution.md` y la spec activa (`specs/NNN-*/`) antes de tocar código.
- Al terminar una tarea o fase, actualiza `MEMORY.md` (máx. ~50 líneas).

## Límites
- ✅ **Siempre**: separar encabezados en Python; generar gráficos de verificación en `tests/NNN-*/images/`; actualizar `MEMORY.md` al cerrar cada paso.
- ⚠️ **Pregunta antes**: añadir dependencias nuevas, cambiar formato de `output/`.
- 🚫 **Nunca**: modificar o borrar archivos originales dentro de `Input/`.

# AGENTS.md — Well Curator (.LAS Dataset Curation)

Aplicativo local en Python + LLM (agnóstico de modelo: `Gemma-4-E4B` GGUF en GPU RTX 4060 vía `llama.cpp`, Ollama/LM Studio o APIs externas OpenAI-compatible) con interfaz gráfica comercial (`PySide6` Qt6) para curar archivos `.las` por pozo en cualquier campo petrolero.

## Stack y estructura
- **Python 3.14**: `PySide6` (Qt6 + `QtWebEngineWidgets` + `Matplotlib QtAgg`), `folium` (mapas geográficos), `pyproj` (proyección POSGAR 2007 / UTM y reproyección inversa WGS84), `lasio`, `pandas`, `numpy`, `openpyxl`, `urllib`.
- `config/fields/`: perfiles de campo configurables (`generic.json`, `golfo_san_jorge_faja2.json`).
- `Input/`: carpetas por pozo originales (solo lectura).
- `workspace_imports/`: pozos cargados por *Drag & Drop* (carpetas, `.las` sueltos o `.zip`) desde la GUI sin tocar `Input/`.
- `output/`: salidas curadas incrementales (`well_metadata.xlsx`, `wells/<Well>_selected.csv`, `time_logs/`, `metadata_cache/`, `batch_run_report.json`, `batch_errors.log`).
- `scripts/`: `app_gui.py`, `ui_controller.py`, `las_explorer.py`, `field_profiles.py`, `llm_extractor.py`, `well_exporter.py`, `visual_validator.py`, `run_pipeline.py`.
- `docs/constitution.md`: principios innegociables del proyecto.
- `specs/NNN-nombre/` y `tests/NNN-nombre/` (con subcarpeta `images/`).

## Comandos
- Lanzar Interfaz Gráfica: `python scripts/app_gui.py`
- Pipeline CLI: `python scripts/run_pipeline.py --field-profile generic --incremental`
- Tests Spec 004 GUI: `$env:PYTHONPATH="."; $env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/004-interfaz-grafica-well-curator/test_spec_004.py -v`

## Reglas de dominio / trampas conocidas (.LAS)
- **Separación de responsabilidades (Texto Crudo -> LLM | Curvas -> `lasio`)**: encabezado puro cortado con triple barrera (`~A`/`~ASCII`/`~LOG_DATA`, detector numérico tras `~Curve`, tope 500 líneas) directo al LLM; `lasio` exclusivo para `las.df()`.
- **Geodesia multi-campo (`field_profiles.py`)**: prioriza Gauss-Krüger si existe (corrigiendo X/Y invertidas); si solo hay `Lat/Lon`, `pyproj` proyecta automáticamente a POSGAR 2007 Fajas 1–7 (`EPSG:5343..5349`) o UTM WGS84 (con `forced_epsg` opcional).
- **Unidades y grilla común**: conversión automática `FT/FEET/F -> M` (`* 0.3048`), grilla al **paso más chico** del pozo sin extrapolación y `NaN` para nulos.
- **Curvas repetidas**: por defecto `repeat_strategy="keep_all"` (`:2`, `:3`, `:4`), soportando `repeat_strategy="merge"` global o por curva en la GUI.

## Memoria y SDD
- Al empezar, lee `MEMORY.md`, `docs/constitution.md` y la spec activa (`specs/NNN-*/`) antes de tocar código.
- Al terminar una tarea o fase, actualiza `MEMORY.md` (máx. ~50 líneas).

## Límites
- ✅ **Siempre**: separar encabezados en Python; generar gráficos/capturas de verificación en `tests/NNN-*/images/`; actualizar `MEMORY.md` al cerrar cada paso.
- ⚠️ **Pregunta antes**: añadir dependencias nuevas, cambiar formato de `output/`.
- 🚫 **Nunca**: modificar o borrar archivos originales dentro de `Input/`.

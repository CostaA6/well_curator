# Tareas — Spec 002: Exportación a `output/` y Grilla Común

- [x] **T1**: Crear especificación SDD (`spec.md`, `plan.md`, `tasks.md`) en `specs/002-exportacion-output-y-grilla-comun/`.
- [x] **T2**: Actualizar `scripts/llm_extractor.py` para alinear la convención `X ~ 2.481.xxx` (Este) e `Y ~ 4.834.xxx` (Norte) y UWI de 14 caracteres.
- [x] **T3**: Implementar `scripts/well_exporter.py` con:
  - Normalización determinística de `Well`, `UWI` (14 chars), `X` (`~2.481.xxx`), `Y` (`~4.834.xxx`) y `Elevation` (`COTA`).
  - Construcción de grilla común de profundidad al paso más chico (`Step = min(effective_steps)`) con preservación de `NaN` fuera de rango y en brechas internas.
  - Exportación de `output/wells/<Well>_selected.csv` con `DEPTH,nombre_pozo,UWI,XWELL,YWELL,COTA` + curvas (soportando `keep_all` con `:2`, `:3` por defecto y opción `merge`).
  - Exportación y actualización incremental de `output/well_metadata.xlsx` (`File`, `Well`, `UWI`, `Start`, `Stop`, `Step`, `X`, `Y`, `Elevation` + curvas ordenadas con `yes`/`no`).
  - Exportación de registros en tiempo a `output/time_logs/` (`xpt_stations_summary.csv` + CSVs individuales por estación).
- [x] **T4**: Extender `scripts/visual_validator.py` y crear `tests/002-exportacion-output-y-grilla-comun/test_spec_002.py` para validar los archivos generados en `output/` y producir los gráficos de verificación en `tests/002-exportacion-output-y-grilla-comun/images/`.
- [x] **T5**: Ejecutar `pytest`, verificar manualmente los archivos en `output/`, actualizar `AGENTS.md` y `MEMORY.md`, y subir cambios a GitHub.

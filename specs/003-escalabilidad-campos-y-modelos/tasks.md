# Tareas — Spec 003: Escalabilidad Multi-Campo, Agnóstico al Modelo y Batch

- [x] **T1**: Redactar especificación SDD (`spec.md`, `plan.md`, `tasks.md`) en `specs/003-escalabilidad-campos-y-modelos/` y validar decisiones de diseño con el usuario.
- [x] **T2**: Crear `scripts/field_profiles.py` (con motor geodésico `pyproj` para prioridad Gauss-Krüger + proyección Lat/Lon -> POSGAR 2007 Fajas 1-7 o UTM) y los perfiles iniciales `config/fields/generic.json` y `config/fields/golfo_san_jorge_faja2.json`.
- [x] **T3**: Refactorizar `scripts/llm_extractor.py` para separar `LLMConfig` + `OpenAICompatibleLLMClient` de `LocalLlamaServer`, desacoplar el `SYSTEM_PROMPT` geográfico, agregar fallback de `json_schema -> json_object -> plain` + reintento de validación y mover los logs a `logs/llm_server.log`.
- [x] **T4**: Actualizar `scripts/las_explorer.py` y `scripts/well_exporter.py` para:
  - Detectar y convertir automáticamente profundidad de pies (`FT`/`FEET`/`F`) a metros (`* 0.3048`).
  - Compactar líneas vacías irrelevantes en `_compact_raw_header_for_llm`.
  - Aplicar normalización/proyección geodésica guiada por `FieldProfile` y guardar caché en `output/metadata_cache/`.
- [x] **T5**: Actualizar `scripts/run_pipeline.py` con CLI completo (`--field-profile`, `--provider`, `--incremental`, `--use-cache`, `--repeat-strategy`), aislamiento de errores en `output/batch_errors.log` y reporte `output/batch_run_report.json`.
- [x] **T6**: Crear la suite `tests/003-escalabilidad-campos-y-modelos/test_spec_003.py` (con pozos sintéticos en `FT` + `M`, proyección Lat/Lon, servidor mock OpenAI-compatible con fallback de schema, corrida en vivo sobre `Input/`, test batch incremental/tolerante a fallos y gráficos de verificación en `images/`).
- [x] **T7**: Ejecutar `pytest`, actualizar `AGENTS.md` y `MEMORY.md`, y subir cambios a GitHub.

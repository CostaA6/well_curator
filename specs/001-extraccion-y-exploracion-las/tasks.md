# Tareas — Spec 001: Extracción de Encabezados, Exploración de Curvas y Validación Visual

- [x] **T1. Implementar explorador de archivos `.las` y extractor de encabezados (`scripts/las_explorer.py`).** RF-1, RF-2, RF-3, RF-4, RF-5
  - Hecho cuando: escanea las 6 carpetas de `Input/`, ignora `.DLIS`/`.LIS`/`.PDS`, decodifica `latin-1`, separa encabezados antes de `~A`, distingue registros `DEPTH` vs `TIME` (`XPT`) y etiqueta curvas repetidas con `:2`, `:3`.
- [x] **T2. Implementar cliente de inferencia GPU local con `llama.cpp` y Gemma 4 (`scripts/llm_extractor.py`).** RF-6, RNF-2
  - Hecho cuando: levanta `model/bin/llama-server.exe` sobre `Vulkan1` (NVIDIA RTX 4060) con `model/gemma-4-E4B-it-Q4_K_M.gguf` y extrae `nombre_pozo`, `uwi`, `coord_x`, `coord_y` y `cota` en JSON para los 6 pozos.
- [x] **T3. Implementar generador de gráficos de validación visual (`scripts/visual_validator.py`).** RF-7
  - Hecho cuando: genera en `tests/001-extraccion-y-exploracion-las/images/` los gráficos de cobertura de profundidades/pasos (`STEP` + estaciones `XPT`) y los gráficos de curvas (incluyendo repetidas `:2`, `:3`) para cada pozo.
- [x] **T4. Crear y ejecutar suite de tests y logs de prueba (`tests/001-extraccion-y-exploracion-las/test_spec_001.py`).** RF-1 a RF-7
  - Hecho cuando: `python -m pytest` pasa al 100% en verde, generando `exploration_report.log`, `exploration_summary.json` y todas las imágenes en `tests/001-extraccion-y-exploracion-las/images/`.

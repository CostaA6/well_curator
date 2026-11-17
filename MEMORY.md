# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator`
  - Rama `feat/pure-header-llm-extraction`: Specs 001, 002 y 003 completadas y testeadas.
  - Rama activa `feat/spec-004-gui`: **Spec 004 implementada y testeada** (`scripts/app_gui.py` + `scripts/ui_controller.py`, `5/5 passed` con capturas GUI en `tests/004-interfaz-grafica-well-curator/images/`).

## Decisiones clave (y por qué)
- **LLM agnóstico (`scripts/llm_extractor.py`)**: `OpenAICompatibleLLMClient` (HTTP `/v1/chat/completions`) con fallback `json_schema -> json_object -> plain`, reintento JSON y reporte escrito `resumen_normalizacion` + `test_llm_connection`.
- **Geodesia híbrida (`scripts/field_profiles.py` con `pyproj 3.7.2`)**: prioriza Gauss-Krüger si existe (corrigiendo inversión X/Y en Fajas 1–7); si solo hay `Lat/Lon`, proyecta automáticamente a POSGAR 2007 Faja 1–7 (`EPSG:5343..5349`) o UTM WGS84. `ui_controller.inverse_project_to_wgs84` reproyecta a WGS84 para el mapa Folium.
- **Multi-unidad y escalabilidad batch (`las_explorer.py`, `run_pipeline.py`)**: conversión `FT -> M` (`* 0.3048`), compactación de `~Parameter` vacío, `--incremental`, caché JSON en `output/metadata_cache/` y fallos en `output/batch_errors.log`.
- **GUI Comercial `PySide6` (`scripts/app_gui.py`)**: 5 pestañas con Drag & Drop nativo (carpetas, `.las`, `.zip` -> `workspace_imports/`), revisión manual + decisión por curva repetida (`per_curve_repeat_strategy`), reporte escrito LLM, mapa Folium en `QWebEngineView` y visor `Matplotlib Qt` de hasta 6 tracks.

## Próximos pasos
- Prueba manual en vivo de la interfaz por parte del usuario (`python scripts/app_gui.py`) y merge de `feat/spec-004-gui` -> `main` si aprueba.

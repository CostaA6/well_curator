# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator`
  - Rama `feat/pure-header-llm-extraction`: Specs 001, 002 y 003 completadas y testeadas (`4/4 passed` Spec 003 en vivo).
  - Rama activa `feat/spec-004-gui`: Spec 004 redactada (`specs/004-interfaz-grafica-well-curator/`) lista para revisión de stack UI (`PySide6` vs `Streamlit`).

## Decisiones clave (y por qué)
- **LLM agnóstico (`scripts/llm_extractor.py`)**: `OpenAICompatibleLLMClient` (HTTP `/v1/chat/completions` sin SDK externos) con fallback `json_schema -> json_object -> plain` y reintento JSON; soporta `llama_cpp_local`, `openai_compatible_local` y `cloud_api`.
- **Geodesia híbrida (`scripts/field_profiles.py` con `pyproj 3.7.2`)**: prioriza Gauss-Krüger si existe (corrigiendo inversión X/Y en Fajas 1–7); si solo hay `Lat/Lon`, proyecta automáticamente a POSGAR 2007 Faja 1–7 (`EPSG:5343..5349`) o UTM WGS84, con `forced_epsg` opcional para bordes de faja.
- **Multi-unidad y escalabilidad batch (`las_explorer.py`, `run_pipeline.py`)**: conversión `FT -> M` (`* 0.3048`), compactación de `~Parameter` vacío, `--incremental`, caché JSON en `output/metadata_cache/` y fallos aislados en `output/batch_errors.log`.
- **Librerías UI ya instaladas**: `PySide6`, `PyQt6`, `streamlit`, `plotly` y `folium` están disponibles en el entorno Python 3.14 sin instalar nuevas dependencias.

## Próximos pasos
- Confirmar con el usuario el framework UI preferido para **Spec 004** (`PySide6` escritorio nativo vs `Streamlit` web local) e implementar `scripts/ui_controller.py` + `scripts/app_gui.py`.

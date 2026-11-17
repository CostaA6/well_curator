# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator` (rama `feat/pure-header-llm-extraction`).
- **Specs completadas y validadas**:
  - **Spec 001**: Corte puro del encabezado `.LAS` con triple barrera -> LLM | `lasio` solo para matriz numérica `~A`.
  - **Spec 002**: Grilla común al paso más chico (`min_step`) y exportación incremental a `output/` (`well_metadata.xlsx`, `wells/*.csv`, `time_logs/`).
  - **Spec 003** (`4/4 passed` en vivo + gráficos): Multi-campo (`FieldProfile` + proyección `pyproj`), agnóstico al LLM (`LLMConfig`), conversión `FT -> M` y batch escalable (`--incremental`, caché JSON, aislamiento de errores).

## Decisiones clave (y por qué)
- **LLM agnóstico (`scripts/llm_extractor.py`)**: `OpenAICompatibleLLMClient` (HTTP `/v1/chat/completions` sin SDK externos) con fallback `json_schema -> json_object -> plain` y reintento JSON; soporta `llama_cpp_local` (cualquier GGUF), `openai_compatible_local` (Ollama/LM Studio) y `cloud_api`.
- **Geodesia híbrida (`scripts/field_profiles.py` con `pyproj 3.7.2`)**:
  - Extracción dual (`coord_x`/`coord_y` y `latitud`/`longitud`). Prioriza Gauss-Krüger si existe (corrigiendo inversión X/Y en Fajas 1–7); si solo hay `Lat/Lon`, proyecta automáticamente a POSGAR 2007 Faja 1–7 (`EPSG:5343..5349`) o UTM WGS84, con `forced_epsg`/`forced_gk_faja` opcional para yacimientos en borde de faja.
  - Mantiene las 9 columnas fijas en `well_metadata.xlsx` y guarda trazabilidad CRS completa en `output/metadata_cache/` y `batch_run_report.json`.
- **Multi-unidad y escalabilidad batch (`las_explorer.py`, `run_pipeline.py`)**:
  - Archivos indexados en `FT`/`FEET`/`F` se escalan a metros (`* 0.3048`) antes de armar la grilla común.
  - Compactación elimina líneas de `~Parameter` vacías (`R14 . :...`) manteniendo intactos valores como `FL1 . Y: ...`.
  - `--incremental` saltea pozos ya exportados; `--use-cache` re-exporta sin LLM; fallos por pozo quedan aislados en `output/batch_errors.log`.

## Próximos pasos
- Evaluar **Spec 004**: Interfaz gráfica (`Streamlit`) para seleccionar perfil de campo, elegir proveedor/modelo LLM, cargar carpetas `.las` y ejecutar/re-exportar `output/`.

# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator`
  - Rama activa `feat/spec-004-gui`: **Spec 004 completa y validada** (`scripts/app_gui.py` + `scripts/ui_controller.py`, `6/6 passed` + capturas GUI y mapa mundi CRS en `tests/004-interfaz-grafica-well-curator/images/`).

## Decisiones clave (y por qué)
- **LLM agnóstico (`scripts/llm_extractor.py`)**: `OpenAICompatibleLLMClient` (HTTP `/v1/chat/completions`) con fallback `json_schema -> json_object -> plain`, reintento JSON y reporte escrito `resumen_normalizacion` + `test_llm_connection`.
- **Geodesia multi-campo (`field_profiles.py` + `ui_controller.py` con `pyproj 3.7.2`)**: prioriza Gauss-Krüger si existe; si solo hay `Lat/Lon`, proyecta a POSGAR 2007 Fajas 1–7 o UTM WGS84. Catálogo completo EPSG (5.291 CRS proyectados vía `pyproj.database`) + diálogo **`CRSSelectorDialog`** estilo QGIS con Mapa Mundi clickable (`AreaOfInterest`) y recuadro `area_of_use.bounds`.
- **Proyecto vacío y persistencia (`RF-7`)**: `app_gui.py` inicia siempre como proyecto nuevo vacío (sin pozos precargados de `Input/`), con **Nuevo / Abrir / Guardar Proyecto (`*.wellcurator.json`)** y staging separado en `workspace_imports/`.
- **GUI Comercial `PySide6`**: 5 pestañas con Drag & Drop nativo (carpetas, `.las`, `.zip`), revisión manual + decisión por curva repetida (`per_curve_repeat_strategy`), reporte escrito LLM, mapa Folium en `QWebEngineView` y visor `Matplotlib Qt` de hasta 6 tracks.

## Próximos pasos
- Prueba manual en vivo de la interfaz por parte del usuario (`python scripts/app_gui.py`) y merge de `feat/spec-004-gui` -> `main` si aprueba.

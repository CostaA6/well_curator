# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator`
  - Rama activa `feat/spec-004-gui`: **Spec 004 completa, blindada y validada** (`6/6 passed` Spec 004 + regresiones Specs 001–003 OK).

## Decisiones clave (y por qué)
- **Aislamiento estricto por pozo (1 pozo = 1 contexto limpio)**:
  - `LocalLlamaServer` arranca con `--no-cache-prompt -sps 0.0` (KV cache limpia entre pozos) y cada llamada HTTP es stateless `[System, Pozo_N]`.
  - `_fit_header_block_to_ctx` compacta en 2 niveles preservando `~Well` y líneas críticas (`LOC/FL1/LATI/LONG/EPD`).
  - `group_las_files_by_well` y `stage_grouped_well_drop_items` auto-separan por identidad `WELL` si el usuario suelta `.las` o `.zip` con pozos mezclados, y `app_gui.py` alerta por mezcla, UWI repetido o XY idénticas antes de exportar.
- **Geodesia multi-campo (`pyproj 3.7.2`)**: prioriza Gauss-Krüger; proyecta `Lat/Lon` a POSGAR 2007 Fajas 1–7 o UTM WGS84. Incluye catálogo completo EPSG (5.291 CRS) y diálogo **`CRSSelectorDialog`** estilo QGIS con Mapa Mundi clickable.
- **GUI Comercial `PySide6`**: arranca como proyecto nuevo vacío (`Nuevo/Abrir/Guardar *.wellcurator.json`), Drag & Drop nativo (`workspace_imports/`), caché desactivada por defecto (`LLM EN VIVO`), revisión manual + reporte `resumen_normalizacion`, mapa Folium y visor de hasta 6 tracks.

## Próximos pasos
- Prueba final en vivo de `python scripts/app_gui.py` por parte del usuario (limpiando `workspace_imports/` previo con el botón en Pestaña 1) y merge de `feat/spec-004-gui` -> `main`.

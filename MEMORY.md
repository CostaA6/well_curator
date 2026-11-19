# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator`
  - Rama activa `feat/spec-004-gui`: **Spec 004 completa (T1–T10)** con **RF-11** validado (`9/9 passed` Spec 004).

## Decisiones clave (y por qué)
- **Trazabilidad Dual (`Paso Original` vs `Paso Nuevo`) y Aislamiento de Salida (RF-11)**:
  - `FIXED_EXCEL_COLS` en `well_exporter.py` incluye `Original Step` y `New Step` (con migración limpia si lee un Excel legado con `Step`). Pestaña 3 (`tbl_review`) y Pestaña 4 (`tbl_grid_provenance`) exponen `Paso Orig. [m]` y `Paso Nuevo [m]`.
  - Cuando **Modo Incremental** está desactivado, `ExportWorker` ejecuta `clean_output_artifacts(output_dir)` antes de exportar y `_plot_excel_matrix` filtra a los pozos de la sesión para evitar residuos de corridas anteriores.
- **Vaciado de Memoria al Iniciar + Pestaña 4 Dedicada de Homogeneización y QC (RF-10)**:
  - Al iniciar (`WellCuratorMainWindow` / `_init_empty_project`) se limpian todas las tablas, lienzos Matplotlib, vista de mapa y las carpetas residuales en `workspace_imports/` no referenciadas por `projects/*.wellcurator.json` (`clean_unreferenced_workspace_imports`), bloqueando la lectura pasiva de `output/` previo salvo exportación en la sesión o apertura manual en Pestaña 6.
  - Nueva Pestaña 4 (`4. Homogeneización de Profundidad (QC)`) muestra procedencia exacta (`inspect_depth_grid_provenance`: Header `~Well` vs mediana `Δz` en `~A`, `FT->M`, origen de `Start/Stop/Step`) y visor QC visual dual completo + zoom punto a punto (`prepare_interpolation_qc_data`).
- **Regularización de Grilla Común Pre-Exportación (RF-9)**:
  - `well_exporter.py` (`compute_batch_depth_grid_bounds`, `build_unified_well_depth_df`), `run_pipeline.py` (`--grid-mode`, `--grid-step`) y Pestaña 3 soportan `per_well` (nativo), `common_step` (mismo paso elegido entre pozos) y `global_grid` (mismo `[Global Start, Global Stop, Step]` e idéntico vector `DEPTH` en todos los CSVs exportados, manteniendo `NaN` fuera del rango medido sin extrapolar).
  - Selector de carpeta `output/` sincronizado en Pestañas 2, 3, 5, badge superior y guardado/carga `.wellcurator.json`.
- **Aislamiento estricto por pozo (1 pozo = 1 contexto limpio)**:
  - `LocalLlamaServer` arranca con `--no-cache-prompt -sps 0.0` (KV cache limpia entre pozos) y cada llamada HTTP es stateless `[System, Pozo_N]`.
  - `_fit_header_block_to_ctx` compacta en 2 niveles preservando `~Well` y líneas críticas (`LOC/FL1/LATI/LONG/EPD`).
  - `group_las_files_by_well` y `stage_grouped_well_drop_items` auto-separan por identidad `WELL` si el usuario suelta `.las` o `.zip` con pozos mezclados, y `app_gui.py` alerta por mezcla, UWI repetido o XY idénticas antes de exportar.
- **Geodesia multi-campo (`pyproj 3.7.2`)**: prioriza Gauss-Krüger; proyecta `Lat/Lon` a POSGAR 2007 Fajas 1–7 o UTM WGS84. Incluye catálogo completo EPSG (5.291 CRS) y diálogo **`CRSSelectorDialog`** estilo QGIS con Mapa Mundi clickable.
- **GUI Comercial `PySide6`**: arranca como proyecto nuevo vacío (`Nuevo/Abrir/Guardar *.wellcurator.json`), Drag & Drop nativo (`workspace_imports/`), caché desactivada por defecto (`LLM EN VIVO`), revisión manual + reporte `resumen_normalizacion`, mapa Folium y visor de hasta 6 tracks.

## Próximos pasos
- Prueba final en vivo de `python scripts/app_gui.py` por parte del usuario (limpiando `workspace_imports/` previo con el botón en Pestaña 1) y merge de `feat/spec-004-gui` -> `main`.

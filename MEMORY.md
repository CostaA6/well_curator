# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator` (rama `feat/pure-header-llm-extraction`).
- **Specs completadas y validadas**:
  - **Spec 001** (`5/5 passed`): Corte puro del encabezado `.LAS` en Python con triple barrera -> `Gemma-4-E4B` en GPU (`Vulkan1` RTX 4060) y `lasio` exclusivo para leer la matriz numérica `~A`.
  - **Spec 002** (`3/3 passed`): Grilla común al paso más chico (`min_step`) y exportación incremental hacia `output/`:
    - `output/well_metadata.xlsx`: 6 pozos x 119 columnas de curvas (`yes`/`no`).
    - `output/wells/<Well>_selected.csv`: CSV por pozo con `DEPTH,nombre_pozo,UWI,XWELL,YWELL,COTA` + curvas con `NaN`.
    - `output/time_logs/`: `xpt_stations_summary.csv` (18 estaciones XPT de `CVa-37` y `CVa-39`) + CSVs individuales en tiempo.

## Decisiones (y por qué)
- **Texto crudo del header para el LLM + `lasio` solo para curvas**: evita el bug de `lasio` con doble `:` en `~Parameter` (`FL1 . Y: 2.482.090,63 : Field Location 1`).
- **Normalización Geodésica y de UWI**:
  - En la Cuenca del Golfo San Jorge (Faja 2), `X` (`XWELL`) es Este (`~2.481.xxx`) e `Y` (`YWELL`) es Norte (`~4.834.xxx`). Varios `.las` invierten X/Y; `normalize_well_metadata` las corrige automáticamente.
  - Los `UWI` `AR01` de 12 caracteres se completan con `00` a 14 caracteres (`AR010000850000`, `AR010000832700`, `AR010000837200`).
- **Grilla común al paso más chico (`Step = min(effective_steps)`)**:
  - En archivos Baker Atlas (`CVa-19`, `CVa-24`) donde el header dice `STEP = 0.0`, el paso físico real entre filas es `1/4 pie = 0.0762 m` (`compute_effective_step` lo detecta automáticamente).
  - Los valores faltantes fuera del tramo medido de cada curva o en brechas internas se exportan como `NaN`.
- **Curvas repetidas**: por defecto se exportan todas con sufijo `:2`, `:3`, `:4` (`repeat_strategy="keep_all"`), manteniendo lista la opción `repeat_strategy="merge"` para la interfaz de usuario.

## Próximos pasos
- **Spec 003**: Interfaz gráfica ligera (ej. `Streamlit`) para crear/cargar un nuevo pozo arrastrando archivos `.LAS`, elegir el tratamiento de curvas repetidas (`keep_all` vs `merge`) y actualizar `output/` con un clic.

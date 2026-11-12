# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Repositorio GitHub**: `https://github.com/CostaA6/well_curator`
  - Rama `main`: versión base inicial de Spec 001.
  - Rama activa `feat/pure-header-llm-extraction`: Spec 001 v2 con corte puro de encabezado (triple barrera) hacia el LLM y `lasio` reservado solo para la tabla numérica de curvas (`5/5 tests passed`).
- **Motor GPU local**: `model/bin/llama-server.exe` (`Vulkan1`: NVIDIA GeForce RTX 4060 Laptop GPU) ejecutando `model/gemma-4-E4B-it-Q4_K_M.gguf` a ~60 tokens/seg.

## Decisiones (y por qué)
- **Texto crudo del header para el LLM + `lasio` solo para curvas**: `lasio` es excelente leyendo la matriz numérica `~A`, pero falla en `~Parameter` cuando el operador usa múltiples `:` (ej. `FL1 . Y: 2.482.090,63 : Field Location 1`). Por eso Python corta el texto plano antes de `~A` sin usar `lasio` y se lo entrega directo a `Gemma-4-E4B`.
- **Triple barrera para detectar el fin del encabezado (`extract_raw_header_before_ascii`)**:
  1. Sección `~A` / `~ A` / `~ASCII` / `~LOG_DATA` (estándar CWLS LAS 1.2/2.0/3.0, presente en los 32 `.las` de `Input/`).
  2. Detector de fila numérica ($\ge 3$ floats seguidos sin `:`) luego de `~Curve`, por si un archivo de campo omitió `~A`.
  3. Tope de seguridad de `500` líneas por encabezado.
- **Regularización en el LLM**: el agente devuelve JSON con `nombre_pozo` limpio, `uwi` en mayúsculas, `coord_x`, `coord_y`, `cota`, `referencia_cota` y `curvas_detectadas` leídas directamente de `~Curve`.

## Aprendizajes y errores a evitar
- **Contradicciones reales en `Input/` resueltas por Gemma-4-E4B**:
  - En `CVa-38`, el archivo CBL tiene `UWI = SAI-369` y los otros dos tienen `AR0100008327` (el modelo elige `AR0100008327`).
  - En `CVa-39`, el archivo de Calibre tiene `UWI = AR010000` (truncado) y `Y: 2.4821.911` (error de tipeo); el modelo toma `AR0100008372` y `X=4834487.45, Y=2481883.31` de las corridas definitivas.

## Próximos pasos
- Revisar la rama `feat/pure-header-llm-extraction` (y hacer merge a `main` si el usuario aprueba).
- Iniciar **Spec 002**: resolución de curvas repetidas, grilla común de profundidad (`DEPT`) y exportación a Excel + CSV/JSON.

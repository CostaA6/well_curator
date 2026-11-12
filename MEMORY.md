# MEMORY.md — Well Curator
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- **Spec 001 implementada y validada (5/5 tests en verde)**:
  - Motor GPU local instalado en `model/bin/llama-server.exe` (`llama.cpp` b12321 Vulkan x64), corriendo `model/gemma-4-E4B-it-Q4_K_M.gguf` sobre `Vulkan1` (**NVIDIA GeForce RTX 4060 Laptop GPU**) a **~60 tokens/seg** (eval de prompt a ~2,400 t/s).
  - Módulos creados: `scripts/las_explorer.py`, `scripts/llm_extractor.py`, `scripts/visual_validator.py`.
  - Suite de pruebas y salidas generadas en `tests/001-extraccion-y-exploracion-las/`:
    - Logs: `exploration_report.log`, `exploration_summary.json`, `llama_server.log`.
    - Gráficos: 12 imágenes PNG (`*_coverage.png` y `*_curves.png`) en `tests/001-extraccion-y-exploracion-las/images/`.

## Decisiones (y por qué)
- **Motor GPU `llama.cpp` Vulkan (`Vulkan1`)**: evita instalar los 8 GB de CUDA Toolkit y compilar C++ en Python 3.14, logrando ~60 t/s directamente sobre la RTX 4060 sin depender de LM Studio.
- **Compactación de encabezados XPT**: en pozos con muchas estaciones en tiempo (`CVa-37` tiene 12 XPT y `CVa-39` tiene 6 XPT), solo se envía al LLM el encabezado del primer XPT y la lista de profundidades estacionarias (`PDEP_XPT.M`), ahorrando miles de tokens.
- **Ventana de contexto en `llama-server`**: configurada en `-c 16384 -np 1` con `max_tokens: 2048` y `json_schema` estricto para que el razonamiento de Gemma 4 nunca se trunque antes de emitir el JSON.

## Aprendizajes y errores a evitar
- **Bug de doble `:` en `lasio` (`~Parameter`)**: en Baker Atlas (`FL1 . Y: 2.482.090,63 : Field Location 1`), `lasio` parte en el primer `:` y esconde el número dentro de `item.descr`. Se resolvió en `scripts/las_explorer.py` haciendo `rsplit(":", 1)`.
- **`subprocess.Popen` bajo `pytest` en Windows**: pasar siempre `stdin=subprocess.DEVNULL` al lanzar `llama-server.exe` para evitar `OSError: [WinError 50]`.
- **Picos de zapato en gráficos de curvas**: curvas como `RHOZ` o `DPHZ` en `CVa-39` tienen valores espurios al apagar la herramienta (`-100000`); se aplicó recorte visual por percentil `1%–99%` en `plot_well_curves_preview`.

## Próximos pasos
- Revisar con el usuario los gráficos y logs de la Spec 001.
- Iniciar **Spec 002**: resolución de curvas repetidas, construcción de la grilla común de profundidad (`DEPT`) y exportación de datasets (Excel maestro + CSV/JSON por pozo).

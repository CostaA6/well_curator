# AGENTS.md — Well Curator (.LAS Dataset Curation)

Aplicativo local en Python + LLM (`Gemma-4-E4B` GGUF en GPU RTX 4060) con interfaz gráfica sencilla para curar archivos `.las` por pozo, extrayendo metadatos (Pozo, UWI, X, Y, Cota), matriz de curvas en Excel y datasets homogeneizados (CSV/JSON).

## Stack y estructura
- **Python 3.14**: `lasio`, `pandas`, `numpy`, `openpyxl`, `matplotlib` (validación visual), UI ligera (ej. `streamlit`), inferencia local sobre RTX 4060 sin depender de LM Studio (`llama.cpp` / `llama-server` o `llama-cpp-python`).
- `Input/`: carpetas por pozo (`YPF.SC.CVa-*`) con múltiples `.las`/`.LAS` (solo lectura).
- `model/`: modelo local `gemma-4-E4B-it-Q4_K_M.gguf`.
- `scripts/`: módulos de extracción de encabezados, clasificación (profundidad vs. tiempo), inferencia LLM, grilla común y exportación.
- `docs/constitution.md`: principios innegociables del proyecto.
- `specs/NNN-nombre/`: especificaciones SDD (`spec.md`, `plan.md`, `tasks.md`).
- `tests/NNN-nombre/`: tests de cada spec + subcarpeta `images/` con gráficos de curvas y rangos de profundidad para inspección visual.

## Comandos
- Tests: `$env:PYTHONPATH="."; python -m pytest`

## Reglas de dominio / trampas conocidas (.LAS)
- **No enviar datos crudos al LLM**: Python (`lasio`/parser) agrupa por carpeta/archivo y arma un bloque de texto solo con encabezados (`~Version`, `~Well`, `~Parameter`, `~Curve`) antes de `~A`.
- **Metadatos objetivo**: Nombre del pozo, UWI, Coordenada X, Coordenada Y, **Cota (Z / EPD / EGL)** y disponibilidad de curvas.
- **Codificación y bug de `:` en `lasio`**: abrir `.las` con tolerancia a `latin-1`/`cp1252`. En `~Parameter`, cuando un valor incluye `:` (ej. `FL1 . Y: 2.482.090,63 : Field Location 1`), `lasio` corta en el primer `:` y mete el número en `descr`; siempre recomponer haciendo `rsplit(":", 1)`.
- **Registros en Tiempo vs. Profundidad**: existen `.las` indexados en tiempo (`STRT.S` / curva `TIME.S`, ej. estaciones de presión `XPT`). Deben detectarse y separarse de los registros en profundidad (`DEPT.M`) antes de armar la grilla común.
- **Curvas repetidas**: distinguir con `:2`, `:3` (incluso dentro del mismo `.las`) y permitir al usuario decidir cómo tratarlas (conservar, fusionar o descartar tramo repetido).

## Memoria y SDD
- Al empezar, lee `MEMORY.md`, `docs/constitution.md` y la spec activa (`specs/NNN-*/`) antes de tocar código.
- Al terminar una tarea o fase, actualiza `MEMORY.md` (máx. ~50 líneas).

## Límites
- ✅ **Siempre**: separar encabezados en Python; generar gráficos de verificación en `tests/NNN-*/images/`; actualizar `MEMORY.md` al cerrar cada paso.
- ⚠️ **Pregunta antes**: añadir dependencias nuevas, definir resolución de curvas repetidas o grilla de profundidad, cambiar formato de salida.
- 🚫 **Nunca**: modificar o borrar archivos originales dentro de `Input/`.

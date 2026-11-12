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
- **Separación de responsabilidades (Texto Crudo -> LLM | Curvas -> `lasio`)**:
  - **Encabezados (sin `lasio`)**: Python corta el texto crudo antes de `~A` con triple barrera de seguridad (1: `~A`/`~ASCII`/`~LOG_DATA`, 2: detector de fila numérica $\ge 3$ floats tras `~Curve`, 3: tope de 500 líneas) y se lo envía directo al LLM. Así evitamos que `lasio` trunque valores con doble `:` como `FL1 . Y: 2.482.090,63 : Field Location 1`.
  - **Curvas numéricas (`lasio`)**: `lasio` se usa exclusivamente para leer las columnas de datos (`las.df()`) y unidades debajo de `~A`.
- **Metadatos objetivo**: Nombre del pozo, UWI (regularizado en mayúsculas), Coordenada X, Coordenada Y, **Cota (Z / EPD / EGL)** y disponibilidad de curvas.
- **Codificación**: abrir `.las` con tolerancia a `utf-8`, `cp1252` y `latin-1`.
- **Registros en Tiempo vs. Profundidad**: existen `.las` indexados en tiempo (`STRT.S` / curva `TIME.S`, ej. estaciones de presión `XPT`). Deben detectarse y separarse de los registros en profundidad (`DEPT.M`) antes de armar la grilla común.
- **Curvas repetidas**: distinguir con `:2`, `:3` (incluso dentro del mismo `.las`) y permitir al usuario decidir cómo tratarlas (conservar, fusionar o descartar tramo repetido).

## Memoria y SDD
- Al empezar, lee `MEMORY.md`, `docs/constitution.md` y la spec activa (`specs/NNN-*/`) antes de tocar código.
- Al terminar una tarea o fase, actualiza `MEMORY.md` (máx. ~50 líneas).

## Límites
- ✅ **Siempre**: separar encabezados en Python; generar gráficos de verificación en `tests/NNN-*/images/`; actualizar `MEMORY.md` al cerrar cada paso.
- ⚠️ **Pregunta antes**: añadir dependencias nuevas, definir resolución de curvas repetidas o grilla de profundidad, cambiar formato de salida.
- 🚫 **Nunca**: modificar o borrar archivos originales dentro de `Input/`.

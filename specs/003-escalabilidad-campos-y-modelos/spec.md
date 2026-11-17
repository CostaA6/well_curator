# Spec 003: Escalabilidad Multi-Campo, Agnóstico al Modelo LLM y Procesamiento Batch

## 1. Objetivo
Evolucionar el flujo de `well_curator` para que sea una herramienta productiva utilizable en **cualquier campo petrolero**, **con cualquier modelo LLM** (local o remoto según necesidad del cliente) y **escalable a lotes de decenas o cientos de pozos**, manteniendo intactos los principios de la Constitución (inmutabilidad de `Input/`, corte puro del encabezado antes de `~A` para el LLM, `lasio` exclusivo para curvas numéricas y validación dual).

---

## 2. Requerimientos Funcionales (RF)

### Eje A — Abstracción Agnóstica al Modelo LLM
- **RF-1 (Proveedor LLM Desacoplado y Configurable)**:
  - Separar la lógica de comunicación HTTP (`/v1/chat/completions`) del lanzador local `LocalLlamaServer`.
  - Soportar mediante una configuración declarativa (`LLMConfig`) tres modos intercambiables:
    1. **`llama_cpp_local`**: inicia y gestiona `llama-server.exe` con cualquier archivo `.gguf` elegido por el usuario/cliente (`model_path`, `gpu_device`, `ctx_size`, `n_gpu_layers`, `max_tokens`).
    2. **`openai_compatible_local`**: se conecta a un servidor local ya activo (ej. `Ollama`, `LM Studio`, `vLLM`, `llama-server` externo) vía `base_url` y `model_name`.
    3. **`cloud_api`**: se conecta a cualquier endpoint compatible con OpenAI (ej. OpenAI, Gemini OpenAI endpoint, Groq, OpenRouter) vía `base_url`, `model_name` y `api_key` leída desde variable de entorno o parámetro seguro (nunca hardcodeada en el repositorio).
- **RF-2 (Estrategia Adaptativa de Salida Estructurada y Razonamiento)**:
  - Soportar modelos con y sin tokens de razonamiento (`content` vs `reasoning_content`).
  - Intentar extracción con `response_format` tipo `json_schema` (`strict: True`); si el backend/modelo seleccionado no lo soporta (HTTP 400/422), hacer fallback automático a `"type": "json_object"` o extracción JSON guiada por prompt.
  - Validar la estructura contra el esquema requerido en Python y ejecutar hasta **1 reintento correctivo** si el JSON devuelto es inválido o incompleto.
- **RF-3 (Logs Desacoplados de Inferencia)**:
  - Mover el archivo de log del servidor LLM desde `tests/001-.../` hacia una ruta operativa configurable (por defecto `logs/llm_server.log`).

### Eje B — Portabilidad Multi-Campo (`FieldProfile`)
- **RF-4 (Perfiles de Campo Configurables y Proyección Geodésica con `pyproj`)**:
  - Incorporar configuraciones de perfil por yacimiento/cliente (archivos JSON en `config/fields/`, ej. `generic.json` y `golfo_san_jorge_faja2.json`), seleccionables al ejecutar el flujo.
  - Un `FieldProfile` define de forma desacoplada:
    - **`prompt_domain_hints`**: instrucciones específicas de la cuenca (pueden quedar vacías en `generic` para que el `SYSTEM_PROMPT` base sea 100% neutro geográficamente).
    - **`prioritize_gauss_kruger` y proyección automática (`pyproj`)**:
      1. El LLM extrae tanto coordenadas planas (`coord_x`, `coord_y`) como geográficas (`latitud`, `longitud` en grados decimales, convirtiendo formatos DMS si hiciera falta).
      2. **Prioridad a Gauss-Krüger**: si el encabezado ya trae coordenadas Gauss-Krüger válidas (en `generic` detectando Fajas 1..7 de Argentina o según `coord_x_range`/`coord_y_range` del perfil), se usan directamente sin reproyectar y se corrige automáticamente el intercambio X/Y si vinieron cruzadas.
      3. **Proyección automática desde Lat/Lon**: si el pozo solo trae coordenadas geográficas (`latitud`, `longitud`), `pyproj` proyecta desde `EPSG:4326`:
         - Si el perfil fija `forced_epsg` o `forced_gk_faja` (1..7, para evitar cortes en bordes de faja), proyecta a ese CRS fijo.
         - En modo automático dentro de Argentina (`lon` en `[-73.5°, -52.5°]`), deduce la **Faja Gauss-Krüger** (`1..7`) por meridiano y proyecta a POSGAR 2007 (`EPSG:5343..5349`).
         - Fuera de Argentina, proyecta automáticamente a **UTM WGS84** (`EPSG:326xx` norte / `EPSG:327xx` sur).
      4. `coord_decimals`: precisión configurable (`0` para coordenadas planas en metros por defecto).
    - **`uwi_normalization`**: modo `"uppercase_strip"` por defecto en `generic`, o regla configurable por patrón (ej. `AR\d{10}` → sufijo `00` solo en `golfo_san_jorge_faja2`).
    - **`well_name_cleanup_patterns`**: lista opcional de regex de limpieza de nombres.
    - **`target_depth_unit`**: unidad objetivo para la grilla común (por defecto `"M"` metros).
    - **`time_log_summary_mnemonics`**: lista extensible de curvas clave a resumir en `output/time_logs/` (soportando XPT, MDT, RFT o detección automática de canales con presión/temperatura).
- **RF-5 (Conversión Automática de Unidades de Profundidad `FT → M`)**:
  - Detectar la unidad de la curva índice de profundidad en cada `.LAS` (`M`, `METERS`, `FT`, `F`, `FEET`).
  - Si un archivo viene indexado en pies (`FT` / `FEET` / `F`) y `target_depth_unit == "M"`, convertir automáticamente la columna índice, `STRT`, `STOP`, `STEP` y `stationary_depth_m` multiplicando por `0.3048` antes de construir la grilla común del pozo, sin modificar jamás el archivo fuente en `Input/`.

### Eje C — Escalabilidad a Lotes Grandes (50–500+ Pozos)
- **RF-6 (Compactación Inteligente de Encabezados para Ventana de Contexto)**:
  - Cuando un pozo tenga múltiples `.las`, compactar el bloque enviado al LLM eliminando líneas de parámetros sin valor (`STAT . :`), comentarios decorativos y encabezados redundantes de estaciones de tiempo idénticas, preservando íntegras las secciones `~Well`, las líneas con datos de ubicación/cota/UWI y las curvas de `~Curve`, estimando presupuesto de tokens según el `ctx_size` del modelo configurado.
- **RF-7 (Modo Incremental, Caché de Metadatos y Tolerancia a Fallos)**:
  - **Modo incremental (`skip_existing`)**: permitir reanudar una corrida consultando los pozos ya exportados en `output/well_metadata.xlsx` y `output/wells/` para procesar solo los pozos nuevos o pendientes.
  - **Caché de metadatos por pozo**: guardar opcionalmente el JSON validado de cada pozo en `output/metadata_cache/<Well>_metadata.json` para poder re-exportar CSV/Excel (ej. cambiando `repeat_strategy` de `keep_all` a `merge`) en segundos sin re-invocar al LLM.
  - **Aislamiento de fallos por pozo**: si un archivo `.las` está corrupto o falla la inferencia en el pozo `N`, registrar el error en `output/batch_errors.log` y generar un resumen de corrida (`output/batch_run_report.json`), continuando automáticamente con el pozo `N+1`.

---

## 3. Requerimientos No Funcionales (RNF)
- **RNF-1 (Cero dependencias externas nuevas obligatorias)**: mantener el cliente HTTP sobre `urllib.request` de la librería estándar de Python para que la herramienta siga siendo liviana e instalable offline.
- **RNF-2 (Compatibilidad hacia atrás)**: al ejecutar el pipeline con el perfil `golfo_san_jorge_faja2` sobre los 6 pozos actuales de `Input/`, los CSVs en `output/wells/` y `output/well_metadata.xlsx` deben mantener compatibilidad total con las validaciones de Spec 001 y Spec 002.
- **RNF-3 (Validación Dual en `tests/003-escalabilidad-campos-y-modelos/`)**:
  - Tests automatizados cubriendo: conversión `FT → M`, perfil `generic` con coordenadas Lat/Long decimales y UWI no argentino, selector/fallback de proveedor LLM (incluyendo servidor HTTP mock OpenAI-compatible que rechaza `json_schema` estricto y usa fallback), compactación de headers largos, tolerancia a fallos por pozo y reanudación incremental.
  - Gráficos visuales en `tests/003-escalabilidad-campos-y-modelos/images/` verificando alineación de curvas mezcladas en pies (`FT`) y metros (`M`) sobre una única grilla común en metros y reporte de corrida batch.

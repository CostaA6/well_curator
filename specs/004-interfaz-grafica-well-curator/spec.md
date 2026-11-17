# Spec 004: Interfaz Gráfica Comercial — Well Curator GUI

## 1. Objetivo
Construir una interfaz gráfica interactiva de aspecto **clásico-moderno de software técnico/comercial** (inspirado en software de ingeniería petrolera como Petrel/Techlog) para operar de punta a punta el flujo de `well_curator`: carga flexible de pozos (drag & drop de archivos, `.zip` o carpetas batch), configuración de parámetros y selección agnóstica del modelo LLM, ejecución supervisada con **reporte explicativo escrito por el LLM**, mapa geográfico de ubicación, y un módulo dedicado de **validación visual y graficado interactivo de registros (logs)**.

---

## 2. Requerimientos Funcionales (RF)

### RF-1 — Flujo por Pestañas y Diseño Clásico-Moderno
La aplicación presentará una barra superior/lateral de estado del sistema (proveedor LLM conectado, estado de GPU/VRAM, perfil de campo activo y ruta `output/`) y un flujo principal organizado en **5 pestañas secuenciales**:
1. **Importar Pozos / Lotes**
2. **Configuración (Campo, Curado y Modelo LLM)**
3. **Procesamiento y Reporte de Normalización LLM**
4. **Validación, Mapa Geográfico y Visor de Logs**
5. **Resultados y Exportación (`output/`)**

---

### RF-2 — Pestaña 1: Importación Flexible (`+ Añadir Pozo` y `Lote de Carpetas`)
Debe permitir incorporar datos de entrada mediante tres mecanismos sin modificar nunca los archivos originales:
1. **Drag & Drop de Pozo Individual ("Añadir Pozo")**:
   - Soltar directamente desde el Explorador de Windows **múltiples archivos `.las`/`.LAS` sueltos** pertenecientes a un pozo (ingresando o auto-sugiriendo el nombre del pozo destino), una **carpeta completa de pozo**, o un archivo **`.zip`** comprimido con sus `.las`.
   - Los archivos ingresados por *Drag & Drop* o selector individual se organizan en una carpeta dedicada **`workspace_imports/<Pozo>/`**, manteniendo `Input/` completamente intacta y conservando los archivos importados para reprocesamientos futuros.
2. **Importación de Directorio Batch ("Importar Fichero de Carpetas")**:
   - Seleccionar una carpeta raíz con subcarpetas por pozo (ej. `Input/` o equivalente en disco del cliente).
3. **Pre-escaneo instantáneo en Python**:
   - Mostrar tabla resumen previa antes de invocar al LLM: archivos detectados, ignorados no-`.las`, clasificación `DEPTH` vs `TIME`, unidad original (`M` vs `FT`), `STRT–STOP`, paso detectado y posibles curvas repetidas (`:2`, `:3`).

---

### RF-3 — Pestaña 2: Configuración de Parámetros y Selector de Modelo LLM
1. **Selector Agnóstico de Modelo LLM (`LLMConfig`)**:
   - Elegir proveedor:
     - `llama_cpp_local`: selector visual de archivos `.gguf` disponibles en `model/`, dispositivo (`Vulkan1` RTX 4060, `CUDA`, `CPU`), `ctx_size` y `max_tokens`.
     - `openai_compatible_local`: presets rápidos para `Ollama` / `LM Studio` / servidor propio (`base_url`, `model_name`) + botón **"Probar conexión"**.
     - `cloud_api`: endpoint (`OpenAI`, `Gemini`, `Groq`, custom), `model_name` y entrada enmascarada de `api_key` (o lectura desde variable de entorno).
2. **Perfil de Campo y Geodesia (`FieldProfile`)**:
   - Selector de perfil (`generic`, `golfo_san_jorge_faja2`, etc.), opción de priorizar Gauss-Krüger vs proyectar `Lat/Lon` automáticamente (`pyproj`), y opción explícita de **forzar Faja GK / EPSG** para campos en borde de faja.
3. **Parámetros de Curado y Grilla**:
   - Estrategia de curvas repetidas (`keep_all` con `:2`, `:3` por defecto vs `merge`).
   - Estrategia de grilla (`paso más chico` del pozo) y unidad de salida (`M`).
   - Opciones batch: modo incremental (`skip_existing`), uso de caché (`use_cache`) y ruta de salida `output/`.

---

### RF-4 — Pestaña 3: Ejecución, Auditoría de Nombres y Reporte Escrito por el LLM
1. **Ejecución en vivo con progreso**: barra de avance por pozo, tiempo estimado y consola de eventos en tiempo real (con aislamiento de fallos en `output/batch_errors.log`).
2. **Tabla comparativa de Normalización y Revisión Supervisada**: mostrar por pozo:
   - `Carpeta/Archivo origen` → `Nombre Pozo normalizado`, `UWI`, `X`, `Y`, `Cota`, `CRS aplicado`, `Step`.
   - **Edición manual opcional antes de exportar**: el usuario puede editar directamente cualquier campo extraído (`Nombre`, `UWI`, `X`, `Y`, `Cota`) y ajustar el tratamiento de curvas repetidas (global o curva por curva: mantener `:2`, `:3` o fusionar en una sola) antes de confirmar la escritura en `output/`.
3. **Reporte Escrito por el LLM (`resumen_normalizacion`)**:
   - Extender el esquema/prompt del extractor para que el LLM redacte un **informe breve y claro en lenguaje natural** por cada pozo explicando:
     - Qué archivos analizó y si hubo conflictos entre ellos (ej. UWI vacío/truncado, códigos internos temporales, errores de tipeo).
     - De dónde extrajo las coordenadas y cota (`LOC`, `FL1`, `LATI`/`LONG`) y qué regularización se aplicó (inversión X/Y o proyección `pyproj` desde Lat/Lon).
     - Qué curvas repetidas o registros en tiempo (`XPT`/`TIME.S`) detectó.

---

### RF-5 — Pestaña 4: Validación Visual, Mapa Geográfico y Visor Interactivo de Logs
Replicar y hacer interactivas dentro de la app todas las validaciones visuales desarrolladas en las Specs 001, 002 y 003:
1. **Mapa Geográfico Interactivo**:
   - Reproyectar internamente las coordenadas planas curadas `(X, Y, CRS)` hacia `EPSG:4326` (`Lat/Lon`) mediante `pyproj` para posicionar todos los pozos sobre un mapa geográfico real (satelital / topográfico / OpenStreetMap).
   - Mostrar popup/etiqueta con Nombre normalizado, UWI, coordenadas planas, CRS (`EPSG`), rango `Start–Stop` y cantidad de curvas; distinguir visualmente pozos GK directos vs proyectados desde Lat/Lon.
2. **Visor Interactivo de Registros (Logs)**:
   - Selector de pozo y selector múltiple de hasta **6 curvas en tracks paralelos** (incluyendo repeticiones `:2`, `:3`).
   - Gráfico multi-track de curvas vs profundidad con barra de zoom/pan interactivo, modo **Validación Crudo vs Grilla Común** (superponiendo puntos originales `.las` sobre la curva exportada en CSV para verificar ausencia de artefactos/extrapolaciones y brechas `NaN`), y soporte de visualización para curvas `FT → M`.
3. **Panel de Cobertura, Matriz y Estaciones en Tiempo**:
   - Gráfico de cobertura por archivo `.las` (`STRT–STOP`, paso y estaciones `TIME.S` / `XPT`).
   - Mapa visual interactivo de la matriz `yes`/`no` de `well_metadata.xlsx`.
   - Gráfico de estaciones de presión en tiempo (`xpt_stations_summary.csv`: presión estabilizada y duración vs profundidad estacionaria).

---

### RF-6 — Pestaña 5: Explorador de Salidas y Descarga (`output/`)
- Vista directa de `output/well_metadata.xlsx`, tabla de `output/wells/<Well>_selected.csv`, `output/time_logs/` y `output/batch_errors.log`.
- Botones para exportar/descargar archivos individuales o abrir la carpeta `output/` en el explorador del sistema.

---

### RF-7 — Proyecto Nuevo Vacío por Defecto y Guardar / Cargar Proyectos (`.wellcurator.json`)
- **Inicio limpio**: Al abrir la aplicación, inicia como un **proyecto nuevo vacío** (sin pozos precargados de `Input/` ni de `workspace_imports/`). Los directorios existentes en disco permanecen intactos y solo se incorporan si el usuario los añade explícitamente o abre un proyecto guardado.
- **Gestión de Proyectos en Barra Superior y Pestaña 1**:
  - **Nuevo Proyecto**: limpia el estado en memoria de la UI para comenzar desde cero.
  - **Guardar Proyecto (`*.wellcurator.json`)**: persiste la configuración del proyecto (perfil de campo, EPSG seleccionado, configuración no secreta del LLM, estrategia de curvas repetidas, rutas de pozos importados, metadatos LLM/ediciones manuales, overrides por curva y carpeta `output/`).
  - **Abrir / Cargar Proyecto (`*.wellcurator.json`)**: reconstruye todo el estado en las 5 pestañas para retomar exactamente donde el usuario había dejado su trabajo.

---

### RF-8 — Selector Completo de Proyecciones (`pyproj.database`) + Mapa Mundi Interactivo estilo QGIS
- **Catálogo EPSG completo**: en lugar de limitarse a `generic` vs `Faja 2`, consultar directamente la base geodésica completa de `pyproj.database.query_crs_info` (~5.291 CRS proyectados EPSG del mundo: POSGAR 2007, POSGAR 94, Campo Inchauspe, WGS 84 / UTM zonas 1–60 Norte/Sur, MAGNA-SIRGAS, SIRGAS 2000, NAD83, etc.).
- **Desplegable buscable en Pestaña 2**: permite elegir `Automático` o buscar/seleccionar cualquier sistema EPSG por código o nombre.
- **Visor / Selector en Mapa Mundi estilo QGIS (`CRSSelectorDialog`)**:
  - Diálogo con mapa mundi interactivo offline (con modos de vista `Mundo Completo`, `Sudamérica / Argentina Fajas 1–7` y `Norteamérica`, grilla de husos UTM y Fajas GK).
  - Al hacer **clic en cualquier punto `(lon, lat)` del mapa**, filtra instantáneamente mediante `pyproj.aoi.AreaOfInterest` los CRS proyectados válidos en esa ubicación exacta.
  - Al seleccionar un CRS de la lista, dibuja en rojo su **`area_of_use.bounds`** sobre el mapa mundi (exactamente como QGIS) y muestra EPSG, nombre oficial y región de uso antes de aplicarlo al proyecto.

---

## 3. Requerimientos No Funcionales (RNF)
- **RNF-1 (Cero bloqueo de UI)**: la inferencia del LLM y la exportación deben ejecutarse en hilos/procesos separados para que la interfaz responda durante el procesamiento de lotes largos.
- **RNF-2 (Inmutabilidad de `Input/`)**: los archivos originales jamás se modifican ni mueven; cualquier staging de drag & drop opera en una carpeta temporal o de proyecto dedicada sin tocar `Input/`.
- **RNF-3 (Cero dependencias nuevas inesperadas)**: usar únicamente librerías ya presentes en el entorno (`PySide6` o `Streamlit` + `plotly` / `folium` / `matplotlib`) previa confirmación de arquitectura con el usuario.
- **RNF-4 (Validación en `tests/004-interfaz-grafica-well-curator/`)**: pruebas unitarias e de integración de los controladores de UI (staging de `.zip`/archivos sueltos/carpetas, generación de reporte LLM, reproyección inversa a WGS84 para el mapa, construcción de datos para gráficos de validación) + capturas/gráficos en `images/`.

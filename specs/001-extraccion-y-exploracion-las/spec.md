# Spec 001 — Extracción de Encabezados, Exploración de Curvas y Validación Visual (.LAS)

Estado: implementada

## Contexto y objetivo
En la carpeta `Input/` existen múltiples carpetas de pozos (`YPF.SC.CVa-*`), cada una con varios archivos `.las`/`.LAS` (y archivos de otros formatos como `.DLIS`, `.LIS`, `.PDS`). Antes de consolidar la matriz final, necesitamos un módulo en Python que explore cada pozo, separe los encabezados (todo lo previo a `~A`) en un bloque limpio listo para enviar al modelo local (`Gemma-4-E4B` vía `llama.cpp`), clasifique si cada archivo está indexado en **Profundidad** (`DEPT`) o en **Tiempo** (`TIME`, ej. estaciones de presión `XPT`), detecte curvas repetidas (`:2`, `:3`), extraiga los metadatos con el LLM (Nombre, UWI, X, Y, Cota) y genere logs de prueba y gráficos de validación visual (cobertura de profundidades y curvas) en `tests/001-extraccion-y-exploracion-las/images/`.

## Usuarios / actores
- **Petrofísico / Ingeniero de Reservorios / Geocientífico**: necesita auditar qué archivos `.las` tiene cada pozo, extraer metadatos confiables sin revisar encabezados a mano, distinguir registros en profundidad vs. tiempo y visualizar la cobertura de curvas y tramos repetidos.

## Historias de usuario
- **HU-1**: Como usuario, quiero que el sistema escanee las carpetas de pozos e ignore archivos no-LAS (`.DLIS`, `.LIS`, `.PDS`), abriendo los `.las`/`.LAS` sin fallar por codificación (`latin-1`/`cp1252`).
- **HU-2**: Como usuario, quiero que Python extraiga únicamente los encabezados (`~Version`, `~Well`, `~Parameter`, `~Curve`) de todos los `.las` de un pozo y arme un bloque de texto estructurado para no enviar miles de líneas numéricas al LLM.
- **HU-3**: Como usuario, quiero que el sistema identifique automáticamente si cada `.las` está medido en **Profundidad** (`DEPT.M` / `DEPT.FT`) o en **Tiempo** (`TIME.S` / `TIME.MS`), rescatando la profundidad puntual de estación (`PDEP_XPT` / `TDEP`) cuando exista.
- **HU-4**: Como usuario, quiero detectar curvas repetidas dentro de un mismo `.las` y entre distintos `.las` de un mismo pozo, etiquetándolas con `:2`, `:3`, etc., para poder decidir su tratamiento.
- **HU-5**: Como usuario, quiero que el modelo local `Gemma-4-E4B` (corriendo sobre GPU RTX 4060 mediante `llama.cpp`) lea el bloque de encabezados del pozo y extraiga en formato JSON estructurado: `nombre_pozo`, `uwi`, `coord_x`, `coord_y` y `cota`.
- **HU-6**: Como usuario, quiero ver en `tests/001-extraccion-y-exploracion-las/` los logs detallados de la prueba y en `tests/001-extraccion-y-exploracion-las/images/` gráficos por pozo que muestren los rangos de profundidad (`STRT`-`STOP`), los pasos (`STEP`), los tramos repetidos y la visualización de las curvas principales.

## Requisitos funcionales (criterios de aceptación en EARS)
- **RF-1**: CUANDO se escanea una carpeta de pozo, EL SISTEMA deberá listar todos los archivos `.las` y `.LAS` e ignorar silenciosamente cualquier otra extensión (`.DLIS`, `.LIS`, `.PDS`).
- **RF-2**: SI un archivo `.las` contiene caracteres en codificación `latin-1` o `cp1252` (ej. `CAÑADÓN VASCO`), ENTONCES EL SISTEMA deberá decodificarlo correctamente sin lanzar `UnicodeDecodeError`.
- **RF-3 (Corte puro con Doble Seguro)**: CUANDO se procesa un archivo `.las`, EL SISTEMA deberá extraer el texto crudo del encabezado usando Python puro (sin pasar el encabezado por `lasio`) aplicando tres barreras de seguridad:
  1. Detenerse al encontrar la sección de datos (`~A`, `~ A`, `~ASCII` o `~LOG_DATA`).
  2. Detenerse si, tras haber pasado `~Curve`, aparece una fila puramente numérica ($\ge 3$ valores flotantes separados por espacios sin `:`), por si se omitió `~A`.
  3. Aplicar un tope máximo de seguridad (`500` líneas por encabezado).
  El bloque enviado al LLM contendrá el texto crudo original del encabezado.
- **RF-4**: CUANDO la primera curva de `~Curve` (o la unidad de `STRT`) corresponda a tiempo (`TIME`, unidad `.S` o `.MS`), EL SISTEMA deberá clasificar el archivo como `index_type = "TIME"` y, si existe una curva de profundidad de sonda (`PDEP_XPT` o `TDEP`), registrar su profundidad estacionaria media; en caso contrario lo clasificará como `index_type = "DEPTH"`.
- **RF-5**: SI un mnemónico de curva aparece más de una vez en el mismo `.las` o en múltiples `.las` de profundidad del mismo pozo, ENTONCES EL SISTEMA deberá etiquetar las ocurrencias subsiguientes como `<CURVA>:2`, `<CURVA>:3`, etc., usando `lasio` únicamente para leer la matriz numérica de curvas (`las.df()`) y sus rangos efectivos `[STRT, STOP]`.
- **RF-6**: CUANDO el bloque de encabezados crudos de un pozo se envía al motor local `llama.cpp` con el modelo `gemma-4-E4B-it-Q4_K_M.gguf`, EL SISTEMA deberá obtener un JSON válido con `nombre_pozo` (normalizado), `uwi` (en mayúsculas), `coord_x`, `coord_y`, `cota`, `referencia_cota` y `curvas_detectadas` (lista de curvas leídas por el agente directamente del bloque `~Curve` del encabezado).
- **RF-7**: CUANDO se ejecute la suite de exploración y testeo de la Spec 001, EL SISTEMA deberá generar:
  1. Un archivo de log detallado (`tests/001-extraccion-y-exploracion-las/exploration_report.log` y `.json`) con los encabezados agrupados, la clasificación `DEPTH` vs `TIME`, las curvas repetidas y la respuesta del LLM.
  2. Gráficos PNG en `tests/001-extraccion-y-exploracion-las/images/` por cada pozo mostrando: (a) mapa de cobertura de profundidades y pasos (`STEP`) por archivo `.las` (incluyendo la posición de estaciones `XPT`), y (b) gráfico de perfiles (tracks de curvas vs. profundidad) superponiendo tramos principales y repetidos.

## Requisitos no funcionales
- **RNF-1 (Solo lectura)**: Ningún archivo dentro de `Input/` puede ser modificado.
- **RNF-2 (Aceleración GPU local)**: `llama.cpp` (`llama-server.exe`) debe ejecutarse usando la GPU NVIDIA RTX 4060 sin depender de LM Studio.

## Casos límite
- Archivos `.las` donde el `UWI` está vacío en una corrida pero presente en otra del mismo pozo (ej. `YPF.SC.CVa-19`).
- Coordenadas `X` e `Y` invertidas en `LATI`/`LONG` (ej. `YPF.SC.CVa-37`) o divididas entre `LOC` y `~Parameter` `FL1`/`FL2` (ej. `YPF.SC.CVa-19`).
- Curvas repetidas dentro del mismo archivo (ej. `M2R6` en `655371479_CVa_19_combo.las`).
- Valores nulos representados como `-999.25` o `-999.2500` que deben enmascararse como `NaN` antes de graficar o calcular rangos efectivos.

## Fuera de alcance (para Spec 001)
- Exportación del archivo Excel final consolidado y la interfaz gráfica de usuario (serán objeto de las siguientes specs una vez validada la extracción y visualización).

## Criterios de finalización
- Todos los tests de `tests/001-extraccion-y-exploracion-las/` en verde con `pytest`.
- Logs de prueba y gráficos PNG generados en `tests/001-extraccion-y-exploracion-las/images/` para los 6 pozos de `Input/`.

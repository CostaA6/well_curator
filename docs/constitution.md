# Constitución — Well Curator

Principios innegociables. Toda spec, plan, tarea y script en `scripts/` debe cumplirlos.

1. **Los datos originales son sagrados**: la carpeta `Input/` es de **solo lectura**. Nunca se modifica, renombra ni elimina un archivo original.
2. **La spec manda (SDD Spec-Anchored)**: nada se implementa en `scripts/` si no está en la spec activa (`specs/NNN-*/`). Si falta una decisión o hay ambigüedad, se para y se pregunta al usuario.
3. **Python para estructura y datos, LLM local para semántica**: Python (`lasio`) agrupa los `.las` del pozo, separa encabezados (antes de `~A`), detecta el índice (`DEPT` vs `TIME`) y procesa curvas; el modelo local (`Gemma-4-E4B` en GPU) solo recibe el resumen de encabezados para extraer Nombre, UWI, X, Y y Cota.
4. **Autonomía local**: el aplicativo debe correr localmente aprovechando la GPU (RTX 4060) sin depender de servicios externos ni requerir que LM Studio esté abierto.
5. **Trazabilidad de curvas e índice**: los registros en tiempo (`TIME.S`) nunca se mezclan por error en la grilla de profundidad (`DEPT.M`), y las curvas repetidas (`:2`, `:3`) se exponen para que el usuario decida su tratamiento sin pérdida silenciosa de datos.
6. **Validación dual (Tests + Visual)**: cada spec `specs/NNN-nombre/` tiene su par en `tests/NNN-nombre/` con tests automatizados (`pytest`) y una carpeta `tests/NNN-nombre/images/` con gráficos de curvas y cobertura de profundidad para revisión visual humana.

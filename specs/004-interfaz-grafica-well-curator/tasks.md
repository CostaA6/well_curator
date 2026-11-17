# Tareas — Spec 004: Interfaz Gráfica Well Curator

- [x] **T1**: Crear rama `feat/spec-004-gui` y redactar especificación completa (`spec.md`, `plan.md`, `tasks.md`) en `specs/004-interfaz-grafica-well-curator/`.
- [ ] **T2**: Confirmar con el usuario el framework gráfico (`PySide6` escritorio nativo vs `Streamlit` web local) y detalles de comportamiento de staging/edición.
- [ ] **T3**: Extender `scripts/llm_extractor.py` para incluir el reporte escrito `resumen_normalizacion` en el esquema JSON del LLM (y soporte de verificación de conexión / listado de modelos en servidores OpenAI-compatibles).
- [ ] **T4**: Implementar `scripts/ui_controller.py` (staging seguro de drag & drop de archivos sueltos / `.zip` / carpetas, pre-escaneo, reproyección inversa a WGS84 para mapa geográfico, y extractor de series para visor de logs Crudo vs Grilla Común).
- [ ] **T5**: Implementar la interfaz gráfica de 5 pestañas (`scripts/app_gui.py`) con aspecto comercial clásico-moderno, ejecución asíncrona, reporte del LLM, mapa geográfico, visor interactivo de registros y explorador de `output/`.
- [ ] **T6**: Crear la suite de tests `tests/004-interfaz-grafica-well-curator/test_spec_004.py` + evidencias visuales en `images/`, actualizar `AGENTS.md` y `MEMORY.md`, y subir cambios a GitHub.

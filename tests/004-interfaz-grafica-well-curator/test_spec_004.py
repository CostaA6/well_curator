"""
Suite de pruebas automatizadas y validación visual para Spec 004 (PySide6 GUI):
- RF-2: Staging seguro de Drag & Drop (archivos .las sueltos, .zip con protección ZipSlip, carpetas) en workspace_imports/.
- RF-3: Inclusión de `resumen_normalizacion` en esquema LLM + `test_llm_connection`.
- RF-4: Edición manual supervisada + decisión por curva repetida (`per_curve_repeat_strategy`).
- RF-5: Reproyección inversa a WGS84 (`pyproj`), mapa geográfico Folium y series Crudo vs CSV Grilla Común.
- RF-1 / RNF-4: Prueba headless de `WellCuratorMainWindow` (PySide6 offscreen) + capturas visuales de las pestañas en `images/`.
"""
from __future__ import annotations

import json
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from scripts.field_profiles import load_field_profile
from scripts.las_explorer import explore_well_directory
from scripts.llm_extractor import BASE_SYSTEM_PROMPT, METADATA_JSON_SCHEMA, LLMConfig, _fit_header_block_to_ctx
from scripts.llm_extractor import test_llm_connection as probe_llm_connection
from scripts.ui_controller import (
    build_folium_well_map_html,
    discover_well_sources,
    get_crs_details,
    inverse_project_to_wgs84,
    list_popular_oilfield_crs,
    load_output_inventory,
    prepare_well_log_tracks,
    search_projected_crs,
    stage_well_drop_items,
)
from scripts.well_exporter import export_well_to_output

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUT_DIR = PROJECT_ROOT / "Input"
OUTPUT_DIR = PROJECT_ROOT / "output"
SPEC004_TEST_DIR = Path(__file__).resolve().parent
IMAGES_DIR = SPEC004_TEST_DIR / "images"


def test_rf2_staging_drag_and_drop_files_zip_and_folder(tmp_path: Path) -> None:
    """
    RF-2: Valida la importación Drag & Drop de:
      1) Archivos .las sueltos.
      2) Un archivo .zip (incluyendo rechazo de rutas inseguras '../').
      3) Una carpeta completa de pozo.
    """
    ws_root = tmp_path / "workspace_imports"

    # 1. Archivos sueltos desde Input/YPF.SC.CVa-19 (solo lectura)
    src_las = sorted((INPUT_DIR / "YPF.SC.CVa-19").glob("*.las"))[:2]
    dest1, files1 = stage_well_drop_items(src_las, target_well_name="DROP_SUELTOS", workspace_root=ws_root)
    assert dest1.exists()
    assert len(files1) == 2

    # 2. Archivo .zip con .las válido + intento de ZipSlip
    zip_path = tmp_path / "POZO_DESDE_ZIP.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(src_las[0], arcname=src_las[0].name)
        zf.writestr("../../evil.las", "IGNORED")
        zf.writestr("readme.txt", "NOT A LAS")

    dest2, files2 = stage_well_drop_items([zip_path], target_well_name="", workspace_root=ws_root)
    assert dest2.name == "POZO_DESDE_ZIP"
    assert files2 == [src_las[0].name]
    assert not (tmp_path / "evil.las").exists()

    # 3. Carpeta completa de pozo
    dest3, files3 = stage_well_drop_items([INPUT_DIR / "YPF.SC.CVa-25"], target_well_name="CVA25_IMPORTADO", workspace_root=ws_root)
    assert dest3.name == "CVA25_IMPORTADO"
    assert len(files3) == 1

    # 4. Protección contra soltar carpeta raíz multi-pozo (ej. Input/) en la zona de pozo único
    with pytest.raises(ValueError, match="subcarpetas de pozos distintos"):
        stage_well_drop_items([INPUT_DIR], target_well_name="LOTE_ENTERO", workspace_root=ws_root)

    # 5. Protección de presupuesto de contexto para pozos con muchos .LAS
    huge_block = (
        "================ ARCHIVO: run1.las ================\n~Well\nWELL. TEST : Well\n~Curve\nGR.GAPI : GR\n"
        + "================ ARCHIVO: run2.las ================\n~Well\nWELL. TEST : Well\n~Parameter\n"
        + "\n".join([f"PAR{i}.  {i}.0 : Parametro largo de calibracion {i}" for i in range(1500)])
        + "\n~Curve\nDT.US/F : Sonic\n"
    )
    fitted = _fit_header_block_to_ctx(huge_block, ctx_size=8192, max_tokens=2048)
    assert len(fitted) < len(huge_block)
    assert "~Well" in fitted and "DT.US/F" in fitted

    discovered = discover_well_sources([("workspace_imports/", ws_root)])
    names = {d["well_folder_name"] for d in discovered}
    assert {"DROP_SUELTOS", "POZO_DESDE_ZIP", "CVA25_IMPORTADO"} <= names


def test_rf3_report_schema_and_connection_probe() -> None:
    """
    RF-3 / RF-4: Verifica que el esquema JSON y el prompt incluyan `resumen_normalizacion`,
    y prueba `test_llm_connection` contra un endpoint `/v1/models` simulado y local.
    """
    assert "resumen_normalizacion" in METADATA_JSON_SCHEMA["properties"]
    assert "resumen_normalizacion" in METADATA_JSON_SCHEMA["required"]
    assert "resumen_normalizacion" in BASE_SYSTEM_PROMPT

    # Prueba local gguf
    ok_local, msg_local, ggufs = probe_llm_connection(LLMConfig(provider="llama_cpp_local"))
    assert ok_local is True
    assert len(ggufs) >= 1

    # Prueba servidor OpenAI-compatible simulado
    class _ModelsHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            if self.path.endswith("/models"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": "qwen2.5:14b"}, {"id": "gemma-4"}]}).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), _ModelsHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        cfg = LLMConfig(provider="openai_compatible_local", base_url=f"http://127.0.0.1:{srv.server_address[1]}")
        ok_srv, msg_srv, models = probe_llm_connection(cfg)
        assert ok_srv is True
        assert "qwen2.5:14b" in models
    finally:
        srv.shutdown()
        srv.server_close()


def test_rf4_manual_override_and_per_curve_repeat_strategy(tmp_path: Path) -> None:
    """
    RF-4: Verifica que el usuario pueda editar manualmente Nombre/X/Y/Cota y decidir
    por curva repetida (`BIT` con `:2`, pero `M2R6` fusionada `merge`) antes de exportar.
    """
    res, dfs = explore_well_directory(INPUT_DIR / "YPF.SC.CVa-19")
    res.llm_metadata = {
        "nombre_pozo": "CVa-19-EDITADO",
        "uwi": "AR010000694600",
        "coord_x": 2481800,
        "coord_y": 4834260,
        "cota": 295.0,
        "crs_used": "EPSG:5344",
        "coord_resolution_note": "Editado manualmente por el usuario",
        "resumen_normalizacion": "Reporte de prueba.",
    }
    out_dir = tmp_path / "out_manual"
    ws = export_well_to_output(
        well=res,
        well_dfs=dfs,
        output_dir=out_dir,
        repeat_strategy="keep_all",
        per_curve_repeat_strategy={"M2R6": "merge"},
        field_profile=load_field_profile("golfo_san_jorge_faja2"),
        manual_override=True,
    )
    df_csv = pd.read_csv(ws.depth_csv_path)
    assert df_csv["nombre_pozo"].iloc[0] == "CVa-19-EDITADO"
    assert int(df_csv["XWELL"].iloc[0]) == 2481800
    assert "BIT" in df_csv.columns and "BIT:2" in df_csv.columns
    assert "M2R6" in df_csv.columns and "M2R6:2" not in df_csv.columns


def test_rf5_inverse_geodesy_folium_map_and_log_tracks() -> None:
    """
    RF-5: Verifica la reproyección inversa `EPSG:5344 -> WGS84`, construye el mapa HTML Folium
    con los pozos reales de `output/` y prepara tracks de comparación Crudo vs Grilla Común.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    lat_lon = inverse_project_to_wgs84(2481797, 4834255, "EPSG:5344")
    assert lat_lon is not None
    assert -47.0 <= lat_lon[0] <= -46.0  # Santa Cruz / Golfo San Jorge Lat
    assert -70.0 <= lat_lon[1] <= -68.5  # Lon

    inv = load_output_inventory(OUTPUT_DIR)
    records = []
    for c in inv["cache_records"]:
        meta = c.get("metadata", {})
        records.append({
            "folder": c.get("well_folder_name"),
            "well_name": meta.get("nombre_pozo"),
            "uwi": meta.get("uwi"),
            "coord_x": meta.get("coord_x"),
            "coord_y": meta.get("coord_y"),
            "crs_used": meta.get("crs_used"),
            "coord_note": meta.get("coord_resolution_note"),
            "grid_step": c.get("grid_step"),
            "curves_count": len(meta.get("curvas_detectadas", [])),
        })

    map_html_path = IMAGES_DIR / "well_map_preview.html"
    html_str = build_folium_well_map_html(records, map_html_path)
    assert map_html_path.exists()
    assert "YPF.SC.CVa-19" in html_str

    res, dfs = explore_well_directory(INPUT_DIR / "YPF.SC.CVa-19")
    tracks = prepare_well_log_tracks(
        res,
        dfs,
        OUTPUT_DIR / "wells" / "YPF.SC.CVa-19_selected.csv",
        ["NEU", "DT", "CAL", "ZDEN"],
        max_tracks=6,
    )
    assert len(tracks) == 4
    assert tracks[0]["csv_depth"] is not None and len(tracks[0]["csv_depth"]) > 1000


def test_rf7_rf8_empty_project_save_load_and_qgis_crs_map(tmp_path: Path) -> None:
    """
    RF-7 / RF-8:
    1) Verifica que la GUI arranque como proyecto nuevo vacío (sin pozos precargados).
    2) Verifica el catálogo completo EPSG (pyproj.database) por búsqueda y por clic (AreaOfInterest).
    3) Prueba el diálogo `CRSSelectorDialog` estilo QGIS con zoom a Sudamérica/Argentina y clic en Faja 2.
    4) Prueba guardar (`*.wellcurator.json`), limpiar a vacío y recargar el proyecto completo.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Catálogo EPSG completo con pyproj
    popular = list_popular_oilfield_crs()
    assert any(p["epsg"] == "EPSG:5344" for p in popular)
    res_click = search_projected_crs(click_lon=-69.23, click_lat=-46.63, category="argentina")
    assert any(r["epsg"] == "EPSG:5344" for r in res_click)
    res_utm = search_projected_crs(query="UTM zone 13N", category="utm")
    assert any(r["epsg"] == "EPSG:32613" for r in res_utm)
    det = get_crs_details("EPSG:5344")
    assert det is not None and det["bounds"] is not None

    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from scripts.app_gui import APP_STYLESHEET, CRSSelectorDialog, WellCuratorMainWindow

    app = QApplication.instance() or QApplication([])
    for font_file in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
        fp = Path("C:/Windows/Fonts") / font_file
        if fp.exists():
            QFontDatabase.addApplicationFont(str(fp))
    app.setFont(QFont("Segoe UI", 9))
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    win = WellCuratorMainWindow(initial_output_dir=OUTPUT_DIR)
    win.show()
    app.processEvents()

    # 1. Arranque vacío por defecto: 0 pozos en Tab 1, Tab 3, Tab 4, Tab 5
    assert win.tbl_import.rowCount() == 0
    assert win.tbl_review.rowCount() == 0
    assert win.cmb_log_well.count() == 0
    assert win.tbl_out_excel.rowCount() == 0
    empty_png = IMAGES_DIR / "gui_tab1_empty_startup.png"
    win.grab().save(str(empty_png))
    assert empty_png.exists() and empty_png.stat().st_size > 10000

    # 2. Diálogo CRS estilo QGIS con clic en (-69.23, -46.63) -> POSGAR 2007 Faja 2 (EPSG:5344)
    dlg = CRSSelectorDialog(parent=win)
    dlg._set_map_bounds((-82, -45), (-58, -15))
    dlg.click_lon = -69.23
    dlg.click_lat = -46.63
    dlg.cmb_cat.setCurrentIndex(1)  # Argentina
    dlg._run_search()
    dlg._select_epsg_in_table("EPSG:5344")
    dlg._redraw_map()
    dlg.show()
    app.processEvents()
    assert dlg.selected_epsg == "EPSG:5344"
    crs_png = IMAGES_DIR / "gui_crs_world_map_selector.png"
    dlg.grab().save(str(crs_png))
    assert crs_png.exists() and crs_png.stat().st_size > 10000
    dlg.close()

    # 3. Aplicar EPSG al proyecto, añadir carpeta de pozo, guardar proyecto, limpiar y reabrir
    win._set_forced_epsg("EPSG:5344")
    win.tabs.setCurrentIndex(1)
    app.processEvents()
    tab2_png = IMAGES_DIR / "gui_tab2_config_crs.png"
    win.grab().save(str(tab2_png))
    assert tab2_png.exists() and tab2_png.stat().st_size > 10000

    win.chk_include_input.setChecked(True)
    win._run_prescan()
    assert win.tbl_import.rowCount() == 6

    proj_file = tmp_path / "cva_demo.wellcurator.json"
    win.save_project_to_path(proj_file)
    assert proj_file.exists()

    # Limpiar a nuevo proyecto vacío
    win._init_empty_project()
    assert win.tbl_import.rowCount() == 0
    assert len(win.prescanned_wells) == 0

    # Reabrir proyecto guardado -> restaura EPSG:5344 y los pozos
    win.load_project_from_path(proj_file)
    assert win._selected_forced_epsg() == "EPSG:5344"
    assert win.tbl_import.rowCount() >= 6
    win.close()


def test_rf1_pyside6_gui_headless_smoke_and_screenshots() -> None:
    """
    RF-1 / RNF-4: Instancia la interfaz PySide6 en modo offscreen, carga los pozos
    reales de `Input/` simulando que el usuario los activó, verifica las 5 pestañas y guarda capturas PNG en `images/`.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from scripts.app_gui import APP_STYLESHEET, WellCuratorMainWindow

    app = QApplication.instance() or QApplication([])
    for font_file in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
        fp = Path("C:/Windows/Fonts") / font_file
        if fp.exists():
            QFontDatabase.addApplicationFont(str(fp))
    app.setFont(QFont("Segoe UI", 9))
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    win = WellCuratorMainWindow(initial_output_dir=OUTPUT_DIR)
    win.show()
    assert win.tabs.count() == 5

    # Simular que el usuario marca cargar desde Input/ y ejecuta pre-escaneo
    win.chk_include_input.setChecked(True)
    win._run_prescan()
    inv = load_output_inventory(OUTPUT_DIR)
    cache_by_folder = {c["well_folder_name"]: c["metadata"] for c in inv["cache_records"]}
    for fname, (res, dfs) in win.prescanned_wells.items():
        if fname in cache_by_folder:
            meta = dict(cache_by_folder[fname])
            meta.setdefault(
                "resumen_normalizacion",
                f"Se analizaron {res.las_files_count} archivos .LAS de {fname}. Se validó UWI={meta.get('uwi')} y coordenadas Gauss-Krüger EPSG:5344.",
            )
            res.llm_metadata = meta
            win.llm_wells[fname] = (res, dfs)
            win.per_curve_overrides.setdefault(fname, {})

    win._populate_review_table()
    if win.tbl_review.rowCount() > 0:
        win.tbl_review.selectRow(0)
    win._refresh_all_visualizations()
    app.processEvents()

    # Capturas visuales de las pestañas clave para inspección humana
    win.tabs.setCurrentIndex(0)
    app.processEvents()
    tab1_png = IMAGES_DIR / "gui_tab1_import.png"
    win.grab().save(str(tab1_png))

    win.tabs.setCurrentIndex(2)
    app.processEvents()
    tab3_png = IMAGES_DIR / "gui_tab3_review_report.png"
    win.grab().save(str(tab3_png))

    win.tabs.setCurrentIndex(3)
    win.subtabs_viz.setCurrentIndex(1)  # Visor multi-track de Logs
    app.processEvents()
    tab4_png = IMAGES_DIR / "gui_tab4_logs_viewer.png"
    win.grab().save(str(tab4_png))

    win.subtabs_viz.setCurrentIndex(0)  # Mapa geográfico & Nombres
    app.processEvents()
    tab4_map_png = IMAGES_DIR / "gui_tab4_map_names.png"
    win.grab().save(str(tab4_map_png))

    assert tab1_png.exists() and tab1_png.stat().st_size > 10000
    assert tab3_png.exists() and tab3_png.stat().st_size > 10000
    assert tab4_png.exists() and tab4_png.stat().st_size > 10000
    assert tab4_map_png.exists() and tab4_map_png.stat().st_size > 10000

    win.close()

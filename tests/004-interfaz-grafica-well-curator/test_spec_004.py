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
    clean_unreferenced_workspace_imports,
    discover_well_sources,
    get_crs_details,
    inverse_project_to_wgs84,
    list_popular_oilfield_crs,
    load_output_inventory,
    prepare_well_log_tracks,
    search_projected_crs,
    stage_grouped_well_drop_items,
    stage_well_drop_items,
)
from scripts.well_exporter import (
    FIXED_EXCEL_COLS,
    clean_output_artifacts,
    compute_batch_depth_grid_bounds,
    export_well_to_output,
    inspect_depth_grid_provenance,
    prepare_interpolation_qc_data,
)

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

    # 5. Protección de presupuesto de contexto en 2 niveles conservando ~Well y líneas críticas
    huge_block = (
        "================ ARCHIVO: run1.las ================\n~Well\nWELL. TEST : Well\n~Curve\nGR.GAPI : GR\n"
        + "================ ARCHIVO: run2.las ================\n~Well\nWELL. TEST : Well\n~Parameter\n"
        + "FL1 . Y: 2.482.048,96 : Field Location 1\n"
        + "\n".join([f"PAR{i}.  {i}.0 : Parametro largo de calibracion {i}" for i in range(1500)])
        + "\n~Curve\nDT.US/F : Sonic\n"
    )
    fitted = _fit_header_block_to_ctx(huge_block, ctx_size=8192, max_tokens=2048)
    assert len(fitted) < len(huge_block)
    assert "~Well" in fitted and "DT.US/F" in fitted and "2.482.048,96" in fitted

    # 6. Auto-separación inteligente cuando se sueltan archivos mezclados de CVa-25 + CVa-38
    mixed_ws = tmp_path / "mixed_ws"
    cva25_files = sorted((INPUT_DIR / "YPF.SC.CVa-25").glob("*.las"))
    cva38_files = sorted((INPUT_DIR / "YPF.SC.CVa-38").glob("*.las"))
    staged_groups = stage_grouped_well_drop_items(cva25_files + cva38_files, workspace_root=mixed_ws)
    assert len(staged_groups) == 2
    by_folder = {d.name: files for d, files in staged_groups}
    assert "YPF.SC.CVa-25" in by_folder and len(by_folder["YPF.SC.CVa-25"]) == 1
    assert "YPF.SC.CVa-38" in by_folder and len(by_folder["YPF.SC.CVa-38"]) == 3

    # Verificar detección si una carpeta en disco ya venía mezclada
    bad_dir = tmp_path / "CARPETA_MEZCLADA"
    bad_dir.mkdir()
    import shutil as _sh
    for f in cva25_files + cva38_files[:1]:
        _sh.copy2(f, bad_dir / f.name)
    bad_res, _ = explore_well_directory(bad_dir)
    assert len(bad_res.detected_wells_in_folder) == 2

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
    assert win.tabs.count() == 6

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

    # Verificar alerta visual cuando dos pozos comparten UWI o coordenadas idénticas
    win.tabs.setCurrentIndex(2)
    app.processEvents()
    if win.tbl_review.rowCount() >= 2:
        dup_uwi = win.tbl_review.item(0, 2).text()
        orig_uwi_row1 = win.tbl_review.item(1, 2).text()
        win.tbl_review.item(1, 2).setText(dup_uwi)
        win._highlight_review_anomalies()
        assert not win.lbl_review_warning.isHidden()
        assert "UWI repetido" in win.lbl_review_warning.text()
        win.tbl_review.item(1, 2).setText(orig_uwi_row1)
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

    win.tabs.setCurrentIndex(4)  # Pestaña 5. Validación, Mapa y Logs
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


def test_rf9_cross_well_common_depth_grid_and_output_selector(tmp_path: Path) -> None:
    """
    RF-9:
    1. Verifica regularización de eje de profundidad pre-exportación en modos
       `common_step` (mismo paso, Start/Stop por pozo) y `global_grid`
       (grilla global idéntica [Global Start, Global Stop, Step] entre múltiples pozos
       sin extrapolación: NaN fuera del intervalo medido).
    2. Verifica en GUI PySide6 la sincronización del selector de carpeta `output/`
       entre Pestañas 2, 3 y 5, la previsualización de grilla y persistencia en proyecto.
    3. Genera gráfico comparativo `rf9_global_depth_grid_alignment.png` y captura Tab 5.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    profile = load_field_profile("golfo_san_jorge_faja2")

    well_names = ["YPF.SC.CVa-19", "YPF.SC.CVa-25", "YPF.SC.CVa-38"]
    loaded_items = []
    for wn in well_names:
        wdir = INPUT_DIR / wn
        assert wdir.exists(), f"Falta pozo real de prueba en Input/: {wn}"
        res, dfs = explore_well_directory(wdir, target_depth_unit="M")
        res.llm_metadata = {
            "nombre_pozo": wn,
            "uwi": f"AR010000{wn[-2:]}00",
            "coord_x": 2481000.0 + int(wn[-2:]) * 10,
            "coord_y": 4834000.0 + int(wn[-2:]) * 10,
            "cota": 250.0,
            "crs_used": "EPSG:5344",
        }
        loaded_items.append((res, dfs))

    bounds = compute_batch_depth_grid_bounds(loaded_items, target_step=None)
    assert bounds["min_step"] == pytest.approx(0.0762, abs=1e-4)
    assert bounds["max_step"] >= 0.15
    assert bounds["chosen_step"] == pytest.approx(0.0762, abs=1e-4)
    assert bounds["global_stop"] > bounds["global_start"]
    assert bounds["rows"] > 1000

    # A) Modo common_step (ej. 0.50 m conservando rango Start/Stop propio de cada pozo)
    out_common = tmp_path / "out_common_step"
    common_summaries = [
        export_well_to_output(
            well=res,
            well_dfs=dfs,
            output_dir=out_common,
            field_profile=profile,
            manual_override=True,
            target_grid_step=0.5,
        )
        for res, dfs in loaded_items
    ]
    for ws in common_summaries:
        assert ws.grid_step == pytest.approx(0.5, abs=1e-4)
        df_csv = pd.read_csv(ws.depth_csv_path)
        diffs = np.diff(df_csv["DEPTH"].to_numpy(dtype=float))
        assert np.allclose(diffs, 0.5, atol=1e-4)

    # B) Modo global_grid: todos los pozos comparten exactamente el mismo eje DEPTH (step=0.1524 m)
    global_bounds = compute_batch_depth_grid_bounds(loaded_items, target_step=0.1524)
    out_global = tmp_path / "out_global_grid"
    global_summaries = [
        export_well_to_output(
            well=res,
            well_dfs=dfs,
            output_dir=out_global,
            field_profile=profile,
            manual_override=True,
            target_grid_step=global_bounds["chosen_step"],
            target_grid_start=global_bounds["global_start"],
            target_grid_stop=global_bounds["global_stop"],
        )
        for res, dfs in loaded_items
    ]

    csv_dfs = [pd.read_csv(ws.depth_csv_path) for ws in global_summaries]
    base_depth = csv_dfs[0]["DEPTH"].to_numpy(dtype=float)
    assert len(base_depth) == int(global_bounds["rows"])
    for ws, df_w in zip(global_summaries, csv_dfs):
        assert ws.grid_start == pytest.approx(global_bounds["global_start"], abs=1e-4)
        assert ws.grid_stop == pytest.approx(global_bounds["global_stop"], abs=1e-4)
        assert ws.grid_step == pytest.approx(0.1524, abs=1e-4)
        assert np.array_equal(df_w["DEPTH"].to_numpy(dtype=float), base_depth)

    # Verificar que un pozo más corto tenga NaN fuera de su rango real (cero extrapolación)
    fixed_cols = {"DEPTH", "nombre_pozo", "UWI", "XWELL", "YWELL", "COTA"}
    picked_curves = []
    first_curve_valid = []
    for res, df_w in zip(loaded_items, csv_dfs):
        cand_cols = [c for c in df_w.columns if c not in fixed_cols and df_w[c].notna().any()]
        gr_like = [c for c in cand_cols if c.upper().startswith("GR") or c.upper().startswith("SP")]
        chosen_col = gr_like[0] if gr_like else cand_cols[0]
        picked_curves.append(chosen_col)
        valid_d = df_w.loc[df_w[chosen_col].notna(), "DEPTH"]
        first_curve_valid.append((float(valid_d.min()), float(valid_d.max())))
    assert len(first_curve_valid) == len(well_names)
    assert any(mn > global_bounds["global_start"] + 1.0 or mx < global_bounds["global_stop"] - 1.0 for mn, mx in first_curve_valid)

    # Gráfico de verificación visual en images/
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5.5), dpi=130)
    colors = ["#2563EB", "#16A34A", "#DC2626"]
    for idx, (wn, df_w, curve) in enumerate(zip(well_names, csv_dfs, picked_curves)):
        d_col = df_w["DEPTH"].to_numpy(dtype=float)
        y_col = df_w[curve].to_numpy(dtype=float)
        ax1.plot(y_col, d_col, lw=0.9, alpha=0.85, color=colors[idx], label=f"{wn} ({curve})")
        valid_mask = ~np.isnan(y_col)
        ax2.fill_betweenx(d_col, idx - 0.35, idx + 0.35, where=valid_mask, color=colors[idx], alpha=0.75)

    ax1.invert_yaxis()
    ax1.set_title("Curvas alineadas sobre Grilla Global Idéntica (step=0.1524 m)", fontweight="bold")
    ax1.set_xlabel("Valor curva (GR / SP)")
    ax1.set_ylabel("DEPTH común [m]")
    ax1.grid(True, linestyle=":")
    ax1.legend(fontsize=8)

    ax2.invert_yaxis()
    ax2.set_xticks(range(len(well_names)))
    ax2.set_xticklabels(well_names, rotation=15, fontsize=8)
    ax2.set_title(f"Cobertura real vs NaN ({len(base_depth):,} filas idénticas/pozo)", fontweight="bold")
    ax2.set_ylabel("DEPTH común [m]")
    ax2.grid(True, axis="y", linestyle=":")
    fig.tight_layout()
    plot_png = IMAGES_DIR / "rf9_global_depth_grid_alignment.png"
    fig.savefig(plot_png)
    plt.close(fig)
    assert plot_png.exists() and plot_png.stat().st_size > 10000

    # C) GUI PySide6: selector sincronizado de output/ en Tabs 2, 3, 5 + preview de grilla + persistencia
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

    win = WellCuratorMainWindow(initial_output_dir=out_global)
    win.show()
    app.processEvents()

    # Sincronización entre Tab 2, Tab 3, Tab 5 y badge superior
    assert Path(win.txt_output_dir.text()) == out_global
    assert Path(win.txt_export_output_dir.text()) == out_global
    assert Path(win.txt_tab5_output_dir.text()) == out_global
    # Seleccionar explícitamente la carpeta de salida carga el inventario en Tab 5
    win.set_output_dir(out_global)
    assert win.tbl_out_excel.rowCount() == len(well_names)

    # Cambiar a una carpeta vacía limpia tablas de Tab 5 y luego volver a out_global
    empty_out = tmp_path / "out_empty"
    empty_out.mkdir()
    win.set_output_dir(empty_out)
    assert win.tbl_out_excel.rowCount() == 0
    assert Path(win.txt_export_output_dir.text()) == empty_out

    win.set_output_dir(out_global)
    assert win.tbl_out_excel.rowCount() == len(well_names)

    # Cargar pozos en memoria y verificar preview global_grid en Tab 3
    for wn, (res, dfs) in zip(well_names, loaded_items):
        win.prescanned_wells[wn] = (res, dfs)
        win.llm_wells[wn] = (res, dfs)
    idx_global = win.cmb_grid_mode.findData("global_grid")
    idx_step = win.cmb_grid_step_preset.findData(0.1524)
    assert idx_global >= 0 and idx_step >= 0
    win.cmb_grid_mode.setCurrentIndex(idx_global)
    win.cmb_grid_step_preset.setCurrentIndex(idx_step)
    win._update_depth_grid_preview()
    assert "Grilla Global Idéntica" in win.lbl_grid_preview.text()
    assert "0.1524" in win.lbl_grid_preview.text()

    g_mode, t_step, t_start, t_stop, _b = win.get_export_depth_grid_spec()
    assert g_mode == "global_grid"
    assert t_step == pytest.approx(0.1524, abs=1e-4)
    assert t_start == pytest.approx(global_bounds["global_start"], abs=1e-4)
    assert t_stop == pytest.approx(global_bounds["global_stop"], abs=1e-4)

    # Guardar y recargar proyecto conserva modo de grilla, paso y carpeta de salida
    proj_path = tmp_path / "rf9_project.wellcurator.json"
    win.save_project_to_path(proj_path)
    win._init_empty_project()
    assert win.cmb_grid_mode.currentData() == "per_well"
    win.load_project_from_path(proj_path)
    assert win.cmb_grid_mode.currentData() == "global_grid"
    assert win._selected_target_grid_step() == pytest.approx(0.1524, abs=1e-4)
    assert Path(win.txt_tab5_output_dir.text()) == out_global

    win.tabs.setCurrentIndex(5)  # Pestaña 6. Salidas (output/)
    app.processEvents()
    tab5_png = IMAGES_DIR / "gui_tab5_output_selector.png"
    win.grab().save(str(tab5_png))
    assert tab5_png.exists() and tab5_png.stat().st_size > 10000
    win.close()


def test_rf10_clean_startup_memory_and_homogenization_qc_tab(tmp_path: Path) -> None:
    """
    RF-10:
    1. Verifica que al iniciar la aplicación toda la memoria y staging residual no referenciado
       se limpia automáticamente (no muestra residuos de ejecuciones previas ni lee output/ pasivo).
    2. Verifica `inspect_depth_grid_provenance` y `prepare_interpolation_qc_data` (origen de STEP,
       Start, Stop, conversión FT->M o mediana Δz cuando STEP=0, métricas QC crudo vs grilla).
    3. Verifica Pestaña 4 (`4. Homogeneización de Profundidad (QC)`) y captura `gui_tab4_homogenization_qc.png`.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Limpieza segura de workspace_imports no referenciados preservando proyectos guardados
    p_dir = tmp_path / "projects"
    i_dir = tmp_path / "workspace_imports"
    p_dir.mkdir()
    kept_well = i_dir / "Pozo_Guardado"
    stale_well = i_dir / "Pozo_Huerfano_Previo"
    kept_well.mkdir(parents=True)
    stale_well.mkdir(parents=True)
    (p_dir / "saved.wellcurator.json").write_text(
        json.dumps({"wells": [{"folder_path": str(kept_well)}]}),
        encoding="utf-8",
    )
    removed = clean_unreferenced_workspace_imports(projects_dir=p_dir, imports_dir=i_dir)
    assert removed == 1
    assert kept_well.exists()
    assert not stale_well.exists()

    # 2. Procedencia de parámetros y QC de interpolación sobre pozos reales
    well_names = ["YPF.SC.CVa-19", "YPF.SC.CVa-25", "YPF.SC.CVa-38"]
    loaded_items = []
    for wn in well_names:
        res, dfs = explore_well_directory(INPUT_DIR / wn, target_depth_unit="M")
        res.llm_metadata = {"nombre_pozo": wn}
        loaded_items.append((res, dfs))

    prov = inspect_depth_grid_provenance(loaded_items, target_step=None)
    assert prov["wells_count"] == 3
    assert prov["files_count"] >= 3
    origins_text = " | ".join(str(r["step_origin"]) for r in prov["file_rows"])
    assert "Header STEP=0" in origins_text
    assert "Header ~Well" in origins_text

    # Curva disponible en CVa-38 sobre grilla global 0.1524 m
    cva38_res, cva38_dfs = loaded_items[2]
    first_curve = sorted(cva38_res.all_assigned_depth_curves)[0]
    qc = prepare_interpolation_qc_data(
        well=cva38_res,
        well_dfs=cva38_dfs,
        curve_name=first_curve,
        target_grid_step=0.1524,
        target_grid_start=prov["bounds"]["global_start"],
        target_grid_stop=prov["bounds"]["global_stop"],
    )
    assert qc["grid_step"] == pytest.approx(0.1524, abs=1e-4)
    assert qc["raw_samples"] > 0
    assert qc["grid_total_rows"] == len(qc["grid_depths"])
    assert qc["zoom_stop"] > qc["zoom_start"]

    # 3. Verificación GUI: arranque limpio no lee OUTPUT_DIR viejo + Pestaña 4 QC visual
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
    app.processEvents()

    # Al iniciar, incluso apuntando a OUTPUT_DIR con datos previos, no filtra ejecuciones pasadas
    assert win._has_session_output is False
    assert win._collect_map_records() == []
    assert win.tbl_out_excel.rowCount() == 0
    assert win.tbl_grid_provenance.rowCount() == 0

    # Cargar pozos en memoria y abrir Pestaña 4: Homogeneización de Profundidad (QC)
    for wn, (res, dfs) in zip(well_names, loaded_items):
        win.prescanned_wells[wn] = (res, dfs)
        win.llm_wells[wn] = (res, dfs)

    idx_global = win.cmb_grid_mode.findData("global_grid")
    idx_step = win.cmb_grid_step_preset.findData(0.1524)
    win.cmb_grid_mode.setCurrentIndex(idx_global)
    win.cmb_grid_step_preset.setCurrentIndex(idx_step)
    win._refresh_homogenization_tab()

    assert win.tbl_grid_provenance.rowCount() >= 3
    assert "Origen del Paso" in win.lbl_grid_provenance.text()
    assert win.cmb_qc_well.count() == 3
    assert win.cmb_qc_curve.count() > 0

    win.tabs.setCurrentIndex(3)  # Pestaña 4. Homogeneización de Profundidad (QC)
    app.processEvents()
    tab4_qc_png = IMAGES_DIR / "gui_tab4_homogenization_qc.png"
    win.grab().save(str(tab4_qc_png))
    assert tab4_qc_png.exists() and tab4_qc_png.stat().st_size > 10000

    # Al pulsar Nuevo Proyecto se vuelve a vaciar todo completamente
    win._init_empty_project()
    assert win.tbl_grid_provenance.rowCount() == 0
    assert win.cmb_qc_well.count() == 0
    assert win.cmb_qc_curve.count() == 0
    assert win._collect_map_records() == []
    win.close()


def test_rf11_original_and_new_step_columns_and_clean_session_matrix(tmp_path: Path) -> None:
    """
    RF-11:
    1. Verifica que `well_metadata.xlsx` incluya `Original Step` y `New Step` distintos tras homogeneizar.
    2. Verifica que `clean_output_artifacts` (modo no incremental) evite que pozos de corridas
       previas queden pegados en `well_metadata.xlsx` y en la Matriz Maestra Excel de Pestaña 5.
    3. Verifica las columnas `Paso Orig. [m]` y `Paso Nuevo [m]` en Pestañas 3 y 4 y guarda `gui_tab5_matrix_rf11.png`.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    profile = load_field_profile("golfo_san_jorge_faja2")

    # Simular carpeta output/ contaminada con una ejecución previa de 4 pozos viejos
    out_dir = tmp_path / "output_rf11"
    stale_wells = []
    for old_w in ["YPF.SC.OLD-1", "YPF.SC.OLD-2"]:
        res_o, dfs_o = explore_well_directory(INPUT_DIR / "YPF.SC.CVa-38", target_depth_unit="M")
        res_o.llm_metadata = {
            "nombre_pozo": old_w,
            "uwi": "AR010000999900",
            "coord_x": 2481000.0,
            "coord_y": 4834000.0,
            "cota": 200.0,
            "crs_used": "EPSG:5344",
        }
        stale_wells.append(export_well_to_output(res_o, dfs_o, out_dir, field_profile=profile, manual_override=True))
    assert (out_dir / "well_metadata.xlsx").exists()
    assert len(pd.read_excel(out_dir / "well_metadata.xlsx")) == 2

    # Limpieza no incremental + exportación de 2 pozos reales con pasos nativos distintos (0.0762 y 0.1524) a paso común 0.50 m
    clean_output_artifacts(out_dir)
    selected_wells = ["YPF.SC.CVa-19", "YPF.SC.CVa-38"]
    loaded = []
    for idx, wn in enumerate(selected_wells):
        res, dfs = explore_well_directory(INPUT_DIR / wn, target_depth_unit="M")
        res.llm_metadata = {
            "nombre_pozo": wn,
            "uwi": f"AR010000{19 + idx}00",
            "coord_x": 2481500.0 + idx * 50,
            "coord_y": 4834500.0 + idx * 50,
            "cota": 290.0,
            "crs_used": "EPSG:5344",
        }
        loaded.append((res, dfs))

    summaries = [
        export_well_to_output(
            well=res,
            well_dfs=dfs,
            output_dir=out_dir,
            field_profile=profile,
            manual_override=True,
            target_grid_step=0.5,
        )
        for res, dfs in loaded
    ]
    df_ex = pd.read_excel(out_dir / "well_metadata.xlsx")
    assert list(df_ex.columns[:len(FIXED_EXCEL_COLS)]) == FIXED_EXCEL_COLS
    assert "Original Step" in df_ex.columns and "New Step" in df_ex.columns
    assert set(df_ex["Well"].astype(str)) == set(selected_wells)
    assert len(df_ex) == 2

    row19 = df_ex[df_ex["Well"] == "YPF.SC.CVa-19"].iloc[0]
    row38 = df_ex[df_ex["Well"] == "YPF.SC.CVa-38"].iloc[0]
    assert float(row19["Original Step"]) == pytest.approx(0.0762, abs=1e-4)
    assert float(row19["New Step"]) == pytest.approx(0.5, abs=1e-4)
    assert float(row38["Original Step"]) == pytest.approx(0.1524, abs=1e-4)
    assert float(row38["New Step"]) == pytest.approx(0.5, abs=1e-4)

    # GUI verification: tablas de Pestaña 3 y Pestaña 4 + Matriz Maestra en Pestaña 5
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

    win = WellCuratorMainWindow(initial_output_dir=out_dir, clean_staging_on_startup=False)
    win.show()
    app.processEvents()

    for wn, (res, dfs), ws in zip(selected_wells, loaded, summaries):
        win.prescanned_wells[wn] = (res, dfs)
        win.llm_wells[wn] = (res, dfs)
        win.export_summaries[wn] = ws
    win._has_session_output = True

    idx_cs = win.cmb_grid_mode.findData("common_step")
    idx_05 = win.cmb_grid_step_preset.findData(0.5)
    win.cmb_grid_mode.setCurrentIndex(idx_cs)
    win.cmb_grid_step_preset.setCurrentIndex(idx_05)
    win._populate_review_table()
    win._refresh_homogenization_tab()

    # Pestaña 3: 10 columnas con Paso Orig. [m] y Paso Nuevo [m]
    assert win.tbl_review.columnCount() == 10
    assert win.tbl_review.item(0, 7).text() == "0.0762"
    assert win.tbl_review.item(0, 8).text() == "0.5"
    assert win.tbl_review.item(1, 7).text() == "0.1524"
    assert win.tbl_review.item(1, 8).text() == "0.5"

    # Pestaña 4: 9 columnas con Paso Orig. [m] y Paso Nuevo [m]
    assert win.tbl_grid_provenance.columnCount() == 9
    assert win.tbl_grid_provenance.item(0, 7).text() == "0.5"

    # Pestaña 5: Matriz Maestra solo con los 2 pozos de la sesión (sin columnas Original/New Step como curvas)
    win._plot_excel_matrix()
    win.tabs.setCurrentIndex(4)
    win.subtabs_viz.setCurrentIndex(3)  # Matriz Maestra Excel (yes/no)
    app.processEvents()
    mat_png = IMAGES_DIR / "gui_tab5_matrix_rf11.png"
    win.grab().save(str(mat_png))
    assert mat_png.exists() and mat_png.stat().st_size > 10000
    win.close()

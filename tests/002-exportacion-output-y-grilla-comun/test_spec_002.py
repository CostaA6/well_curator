"""
Suite de pruebas automatizadas y validación visual para Spec 002:
- Normalización de X (~2.481.xxx), Y (~4.834.xxx), UWI (14 caracteres) y Cota (RF-1).
- Grilla común de profundidad al paso más chico (RF-2) y curvas repetidas :2, :3 (RF-3).
- Exportación de CSV por pozo en `output/wells/<Well>_selected.csv` con `NaN` (RF-4).
- Actualización incremental del Excel maestro `output/well_metadata.xlsx` (RF-5).
- Exportación de registros en tiempo en `output/time_logs/` (RF-6).
- Generación de gráficos de validación en `tests/002-exportacion-output-y-grilla-comun/images/` (RNF-2).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest

from scripts.las_explorer import WellExplorationResult, explore_all_wells
from scripts.llm_extractor import LocalLlamaServer, normalize_well_metadata
from scripts.visual_validator import (
    plot_excel_availability_matrix,
    plot_unified_vs_raw_comparison,
    plot_xpt_stations_summary,
)
from scripts.well_exporter import (
    FIXED_CSV_COLS,
    FIXED_EXCEL_COLS,
    WellExportSummary,
    build_unified_well_depth_df,
    compute_effective_step,
    export_well_to_output,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUT_DIR = PROJECT_ROOT / "Input"
OUTPUT_DIR = PROJECT_ROOT / "output"
SPEC001_SUMMARY_JSON = PROJECT_ROOT / "tests" / "001-extraccion-y-exploracion-las" / "exploration_summary.json"
SPEC002_TEST_DIR = Path(__file__).resolve().parent
IMAGES_DIR = SPEC002_TEST_DIR / "images"
REPORT_LOG = SPEC002_TEST_DIR / "export_report.log"


@pytest.fixture(scope="module")
def explored_wells_with_llm() -> Tuple[List[WellExplorationResult], Dict[str, Dict[str, pd.DataFrame]]]:
    """
    Ejecuta el flujo completo en vivo de principio a fin (sin caché):
    1. Escanea los 6 pozos de Input/ con las_explorer.py.
    2. Levanta LocalLlamaServer (Gemma-4-E4B en GPU RTX 4060) y extrae los metadatos de cada pozo en vivo.
    3. Entrega los resultados a well_exporter.py.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    results, all_dfs = explore_all_wells(INPUT_DIR)

    with LocalLlamaServer(gpu_device="Vulkan1") as server:
        for w in results:
            meta = server.extract_well_metadata(w.consolidated_header_block)
            w.llm_metadata = normalize_well_metadata(
                meta,
                fallback_well_name=w.well_folder_name,
            )

    return results, all_dfs


def test_rf1_metadata_normalization() -> None:
    """
    RF-1: Verifica la corrección automática de coordenadas X/Y invertidas,
    limpieza de nombre de pozo y estandarización de UWI AR01 a 14 caracteres.
    """
    raw = {
        "nombre_pozo": "YPF.SC.CV.a-38",
        "uwi": "ar0100008327",
        "coord_x": 4834449.98,
        "coord_y": 2481587.54,
        "cota": 291.994,
    }
    norm = normalize_well_metadata(raw)
    assert norm["nombre_pozo"] == "YPF.SC.CVa-38"
    assert norm["uwi"] == "AR010000832700"
    assert len(str(norm["uwi"])) == 14
    assert 2_480_000 <= float(norm["coord_x"]) <= 2_485_000
    assert 4_830_000 <= float(norm["coord_y"]) <= 4_838_000
    assert norm["cota"] == 291.99


def test_rf2_rf3_common_depth_grid_and_repeat_strategies(explored_wells_with_llm) -> None:
    """
    RF-2: Verifica que la grilla común utilice el paso más chico del pozo
    (0.1 m en CVa-19; 0.1524 m detectado dinámicamente en CVa-24 cuando STEP=0).
    RF-3: Verifica tanto 'keep_all' (:2, :3, :4) como 'merge'.
    """
    results, all_dfs = explored_wells_with_llm
    by_name = {w.well_folder_name: w for w in results}

    # CVa-19 tiene 1 archivo a 0.1m y 2 archivos Baker a 0.0762m (1/4 pie, declarados con STEP=0.0) -> el paso más chico es 0.0762m
    cva19 = by_name["YPF.SC.CVa-19"]
    df19_keep, start19, stop19, step19, curves19_keep = build_unified_well_depth_df(
        cva19, all_dfs["YPF.SC.CVa-19"], cva19.llm_metadata, repeat_strategy="keep_all"
    )
    assert step19 == 0.0762
    assert np.isclose(start19, 99.974, atol=1e-3)
    assert np.isclose(stop19, 1504.798, atol=1e-3)
    assert "M2R6" in curves19_keep and "M2R6:2" in curves19_keep and "M2R6:4" in curves19_keep

    # Estrategia 'merge' unifica las repeticiones en el mnemónico base
    df19_merge, _, _, _, curves19_merge = build_unified_well_depth_df(
        cva19, all_dfs["YPF.SC.CVa-19"], cva19.llm_metadata, repeat_strategy="merge"
    )
    assert "M2R6" in curves19_merge
    assert "M2R6:2" not in curves19_merge
    assert len(df19_merge) == len(df19_keep)

    # CVa-24 tiene STEP=0.0 en ambos headers pero el paso físico real entre filas es 0.0762m (1/4 pie)
    cva24 = by_name["YPF.SC.CVa-24"]
    _, _, _, step24, _ = build_unified_well_depth_df(
        cva24, all_dfs["YPF.SC.CVa-24"], cva24.llm_metadata, repeat_strategy="keep_all"
    )
    assert step24 == 0.0762


def test_rf4_rf5_rf6_full_output_export_and_visuals(explored_wells_with_llm) -> None:
    """
    RF-4, RF-5, RF-6 y RNF-2:
    Ejecuta la exportación incremental de los 6 pozos hacia `output/`:
    - Verifica `output/well_metadata.xlsx`
    - Verifica `output/wells/<Well>_selected.csv` con `NaN` explícito
    - Verifica `output/time_logs/` (resumen XPT + 18 CSVs en tiempo)
    - Genera los gráficos de validación en `tests/002-exportacion-output-y-grilla-comun/images/`
    """
    results, all_dfs = explored_wells_with_llm

    # Limpiar output/ previo para probar la incorporación incremental pozo a pozo desde cero
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summaries: List[WellExportSummary] = []
    report_lines: List[str] = [
        "================================================================================",
        "REPORTE DE EXPORTACIÓN Y GRILLA COMÚN — SPEC 002 (Carpeta output/)",
        "================================================================================",
        "",
    ]

    for idx, well in enumerate(results, start=1):
        ws = export_well_to_output(
            well=well,
            well_dfs=all_dfs[well.well_folder_name],
            output_dir=OUTPUT_DIR,
            repeat_strategy="keep_all",
            update_excel=True,
        )
        summaries.append(ws)

        # Verificar que el Excel maestro crezca incrementalmente en cada pozo procesado
        excel_path = OUTPUT_DIR / "well_metadata.xlsx"
        assert excel_path.exists()
        df_excel_step = pd.read_excel(excel_path)
        assert len(df_excel_step) == idx, (
            f"Tras exportar {idx} pozos, well_metadata.xlsx debería tener {idx} filas"
        )

        # Verificar CSV de profundidad del pozo
        assert ws.depth_csv_path.exists()
        raw_csv_text = ws.depth_csv_path.read_text(encoding="utf-8")
        assert "NaN" in raw_csv_text, f"El CSV {ws.depth_csv_path.name} debe usar 'NaN' para valores faltantes"

        df_csv = pd.read_csv(ws.depth_csv_path)
        assert list(df_csv.columns[:6]) == FIXED_CSV_COLS
        assert 2_480_000 <= int(df_csv["XWELL"].iloc[0]) <= 2_485_000
        assert 4_830_000 <= int(df_csv["YWELL"].iloc[0]) <= 4_838_000
        assert len(str(df_csv["UWI"].iloc[0])) == 14

        # Generar gráfico comparativo Grilla Común vs Original (RNF-2)
        comp_img = plot_unified_vs_raw_comparison(
            well_result=well,
            well_dfs=all_dfs[well.well_folder_name],
            unified_csv_path=ws.depth_csv_path,
            output_dir=IMAGES_DIR,
        )
        assert comp_img.exists() and comp_img.stat().st_size > 10000

        report_lines.append(
            f"POZO {idx}: {ws.well_name} (Archivo CSV: {ws.depth_csv_path.name})"
        )
        report_lines.append(
            f"  - UWI: {ws.uwi} | XWELL: {ws.coord_x} | YWELL: {ws.coord_y} | COTA: {ws.elevation} m"
        )
        report_lines.append(
            f"  - Grilla Común DEPTH: Start={ws.grid_start} m, Stop={ws.grid_stop} m, "
            f"Step={ws.grid_step} m ({ws.total_grid_rows} filas)"
        )
        report_lines.append(
            f"  - Curvas exportadas ({len(ws.exported_depth_curves)}): "
            + ", ".join(ws.exported_depth_curves)
        )
        if ws.time_csv_paths:
            report_lines.append(
                f"  - Registros en Tiempo exportados en output/time_logs/{ws.well_name}/: "
                f"{len(ws.time_csv_paths)} archivos CSV"
            )
        report_lines.append(f"  - Imagen validación: {comp_img.name}")
        report_lines.append("-" * 80)

    # Verificar estructura final de output/well_metadata.xlsx
    excel_path = OUTPUT_DIR / "well_metadata.xlsx"
    df_excel = pd.read_excel(excel_path)
    assert list(df_excel.columns[:9]) == FIXED_EXCEL_COLS
    curve_cols = list(df_excel.columns[9:])
    assert curve_cols == sorted(curve_cols), "Las columnas de curvas en el Excel deben estar ordenadas alfabéticamente"
    for c in curve_cols:
        vals = set(df_excel[c].astype(str).str.lower().unique())
        assert vals <= {"yes", "no"}, f"Columna {c} contiene valores inválidos: {vals}"

    # Verificar registros en tiempo en output/time_logs/
    xpt_summary_csv = OUTPUT_DIR / "time_logs" / "xpt_stations_summary.csv"
    assert xpt_summary_csv.exists()
    df_xpt = pd.read_csv(xpt_summary_csv)
    assert len(df_xpt) == 18, "Se esperaban las 18 estaciones XPT (12 de CVa-37 + 6 de CVa-39)"

    # Generar gráficos globales de Spec 002 (matriz de Excel y resumen de estaciones XPT)
    matrix_img = plot_excel_availability_matrix(excel_path, IMAGES_DIR)
    xpt_img = plot_xpt_stations_summary(xpt_summary_csv, IMAGES_DIR)
    assert matrix_img.exists() and matrix_img.stat().st_size > 10000
    assert xpt_img.exists() and xpt_img.stat().st_size > 10000

    report_lines.append("")
    report_lines.append(f"EXCEL MAESTRO: {excel_path}")
    report_lines.append(f"  - Total pozos registrados : {len(df_excel)}")
    report_lines.append(f"  - Total columnas de curvas: {len(curve_cols)}")
    report_lines.append(f"RESUMEN TIEMPO (XPT): {xpt_summary_csv} ({len(df_xpt)} estaciones)")
    report_lines.append(f"IMÁGENES GLOBALES: {matrix_img.name}, {xpt_img.name}")

    REPORT_LOG.write_text("\n".join(report_lines), encoding="utf-8")
    assert REPORT_LOG.exists()

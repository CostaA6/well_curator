"""
Suite de pruebas automatizadas y validación visual para Spec 001 (v2 - Rama feat/pure-header-llm-extraction):
- Corte puro del encabezado .LAS en Python con triple barrera de seguridad (sin pasar el header por lasio).
- El agente LLM (Gemma-4-E4B en RTX 4060) lee el header crudo y regulariza Nombre, UWI, X, Y, Cota y Curvas.
- lasio se utiliza exclusivamente para leer la matriz numérica de las curvas (las.df()).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import pytest

from scripts.las_explorer import (
    WellExplorationResult,
    explore_all_wells,
    extract_raw_header_before_ascii,
)
from scripts.llm_extractor import LocalLlamaServer
from scripts.visual_validator import (
    plot_well_curves_preview,
    plot_well_depth_coverage,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUT_DIR = PROJECT_ROOT / "Input"
SPEC_TEST_DIR = Path(__file__).resolve().parent
IMAGES_DIR = SPEC_TEST_DIR / "images"
LOG_FILE = SPEC_TEST_DIR / "exploration_report.log"
SUMMARY_JSON = SPEC_TEST_DIR / "exploration_summary.json"


@pytest.fixture(scope="module")
def exploration_data() -> Tuple[List[WellExplorationResult], Dict[str, Dict[str, pd.DataFrame]]]:
    """Explora los 6 pozos de Input/ una sola vez para toda la suite de tests."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    results, all_dfs = explore_all_wells(INPUT_DIR)
    return results, all_dfs


def test_rf1_rf2_scan_and_encoding(exploration_data) -> None:
    """
    RF-1: Escanea las 6 carpetas de pozo, lista solo .las/.LAS e ignora .DLIS/.LIS/.PDS.
    RF-2: Decodifica archivos en latin-1/cp1252 (ej. CAÑADON VASCO) sin UnicodeDecodeError.
    """
    results, _ = exploration_data
    assert len(results) == 6, "Se esperaban exactamente 6 carpetas de pozo en Input/"

    by_name = {r.well_folder_name: r for r in results}
    assert "YPF.SC.CVa-19" in by_name
    cva19 = by_name["YPF.SC.CVa-19"]

    assert cva19.las_files_count == 3
    assert any(f.endswith(".LIS") for f in cva19.ignored_files)
    assert "CAÑADON VASCO" in cva19.consolidated_header_block


def test_rf3_triple_barrier_pure_header_separation(exploration_data) -> None:
    """
    RF-3: Verifica las 3 barreras de seguridad del cortador puro de encabezados
    y que las líneas originales (ej. FL1 con doble ':') lleguen intactas al bloque del LLM.
    """
    results, _ = exploration_data
    for well in results:
        for s in well.las_summaries:
            assert s.header_stop_reason == "SECTION_ASCII"
            for line in s.raw_header_text.splitlines():
                assert not line.strip().upper().startswith("~A"), (
                    f"Se filtró una línea ~A en el encabezado de {s.file_name}"
                )
            assert len(s.raw_header_text) > 50

    # Verificar que en CVa-24 la línea original de FL1 llegue intacta sin ser cortada por lasio
    by_name = {r.well_folder_name: r for r in results}
    cva24_block = by_name["YPF.SC.CVa-24"].consolidated_header_block
    assert "Y: 2.482.090,63" in cva24_block

    # Probar Barrera 2: archivo sintético mal formado donde olvidaron escribir ~A
    malformed_las = (
        "~Version\nVERS. 2.0 : CWLS\n"
        "~Well\nWELL. POZO-TEST : Well\n"
        "~Curve\nDEPT.M : Depth\nGR.GAPI : Gamma Ray\nDT.US/F : Sonic\n"
        "   100.0000   -999.2500    85.4000\n"
        "   100.1000     72.3000    84.9000\n"
    )
    hdr, reason = extract_raw_header_before_ascii(malformed_las)
    assert reason == "NUMERIC_DATA_ROW"
    assert "100.0000" not in hdr
    assert "GR.GAPI" in hdr

    # Probar Barrera 3: límite máximo de líneas
    long_las = "\n".join([f"# Comentario {i}" for i in range(600)])
    hdr_long, reason_long = extract_raw_header_before_ascii(long_las, max_lines=500)
    assert reason_long == "MAX_LINES_LIMIT"
    assert len(hdr_long.splitlines()) == 500


def test_rf4_depth_vs_time_classification(exploration_data) -> None:
    """
    RF-4: Clasifica correctamente registros en Profundidad (DEPTH) vs Tiempo (TIME - XPT)
    y rescata la profundidad estacionaria de las estaciones XPT usando lasio sobre los datos.
    """
    results, _ = exploration_data
    by_name = {r.well_folder_name: r for r in results}

    cva37 = by_name["YPF.SC.CVa-37"]
    assert cva37.depth_files_count == 2
    assert cva37.time_files_count == 12

    xpt_021 = next(
        s for s in cva37.las_summaries if "XPT_021LTC" in s.file_name
    )
    assert xpt_021.index_type == "TIME"
    assert xpt_021.stationary_depth_m is not None
    assert 738.0 <= xpt_021.stationary_depth_m <= 739.0


def test_rf5_repeated_curves_detection(exploration_data) -> None:
    """
    RF-5: Detecta curvas repetidas dentro de un mismo .LAS (ej. M2R6:2 en CVa-19)
    y entre distintos .LAS de un mismo pozo, etiquetándolas con :2, :3.
    """
    results, _ = exploration_data
    by_name = {r.well_folder_name: r for r in results}

    cva19 = by_name["YPF.SC.CVa-19"]
    assert "M2R6" in cva19.repeated_depth_curves
    assert "M2R6:2" in cva19.all_assigned_depth_curves

    cva39 = by_name["YPF.SC.CVa-39"]
    assert len(cva39.repeated_depth_curves) > 5


def test_rf6_rf7_llm_gpu_extraction_and_visual_validation(exploration_data) -> None:
    """
    RF-6 y RNF-2: Ejecuta gemma-4-E4B-it-Q4_K_M.gguf en la GPU RTX 4060 mediante llama-server.exe
    pasándole directamente los encabezados crudos para regularizar Nombre, UWI, X, Y, Cota y Curvas.
    RF-7: Genera los logs de prueba y los gráficos PNG en tests/001-extraccion-y-exploracion-las/images/.
    """
    results, all_dfs = exploration_data

    log_lines: List[str] = [
        "================================================================================",
        "REPORTE DE PRUEBA Y EXPLORACIÓN .LAS — SPEC 001 (v2: Encabezado Puro + LLM)",
        "Motor GPU: llama.cpp (llama-server.exe Vulkan1 - NVIDIA GeForce RTX 4060)",
        "Modelo LLM: gemma-4-E4B-it-Q4_K_M.gguf",
        "Estrategia: Texto crudo del header (triple barrera) -> LLM | Curvas numéricas -> lasio",
        "================================================================================",
        "",
    ]

    with LocalLlamaServer(gpu_device="Vulkan1") as llm_server:
        for well in results:
            meta = llm_server.extract_well_metadata(well.consolidated_header_block)
            well.llm_metadata = meta

            # Validar campos extraídos por el LLM (RF-6)
            assert meta.get("nombre_pozo"), f"Falta nombre_pozo en {well.well_folder_name}"
            assert meta.get("uwi"), f"Falta uwi en {well.well_folder_name}"
            assert meta.get("uwi") == meta.get("uwi").upper(), "El UWI debe estar regularizado en mayúsculas"
            assert meta.get("coord_x") is not None, f"Falta coord_x en {well.well_folder_name}"
            assert meta.get("coord_y") is not None, f"Falta coord_y en {well.well_folder_name}"
            assert meta.get("cota") is not None, f"Falta cota en {well.well_folder_name}"
            assert isinstance(meta.get("curvas_detectadas"), list) and len(meta["curvas_detectadas"]) > 0, (
                f"El LLM no devolvió curvas_detectadas para {well.well_folder_name}"
            )

            # Generar imágenes de validación visual (RF-7)
            cov_img = plot_well_depth_coverage(well, IMAGES_DIR)
            curv_img = plot_well_curves_preview(well, all_dfs[well.well_folder_name], IMAGES_DIR)
            assert cov_img.exists() and cov_img.stat().st_size > 10000
            assert curv_img.exists() and curv_img.stat().st_size > 10000

            # Documentar en el log de prueba
            log_lines.append(f"POZO: {well.well_folder_name}")
            log_lines.append(
                f"  Archivos .LAS: {well.las_files_count} "
                f"(Profundidad: {well.depth_files_count}, Tiempo/XPT: {well.time_files_count}) | "
                f"Ignorados no-LAS: {len(well.ignored_files)} ({', '.join(well.ignored_files)})"
            )
            log_lines.append("  Metadatos regularizados por Gemma-4-E4B (leyendo el header crudo):")
            log_lines.append(f"    - Nombre Pozo       : {meta.get('nombre_pozo')}")
            log_lines.append(f"    - UWI               : {meta.get('uwi')}")
            log_lines.append(f"    - Coordenada X      : {meta.get('coord_x')}")
            log_lines.append(f"    - Coordenada Y      : {meta.get('coord_y')}")
            log_lines.append(f"    - Cota              : {meta.get('cota')} m ({meta.get('referencia_cota')})")
            log_lines.append(f"    - Fuente en Header  : {meta.get('fuente_coordenadas')}")
            log_lines.append(
                f"    - Curvas leídas por LLM ({len(meta.get('curvas_detectadas', []))}): "
                + ", ".join(meta.get("curvas_detectadas", []))
            )
            log_lines.append(f"    - Tiempo Inferencia : {meta.get('_inference_time_s')} s")
            log_lines.append("  Detalle de Archivos .LAS en Profundidad (datos leídos con lasio):")
            for s in well.las_summaries:
                if s.index_type == "DEPTH":
                    log_lines.append(
                        f"    * {s.file_name} [encoding={s.encoding_used}, stop={s.header_stop_reason}] -> "
                        f"STRT={s.declared_start}m, STOP={s.declared_stop}m, STEP={s.step}m, "
                        f"Curvas={len(s.curves)}"
                    )
            if well.time_files_count > 0:
                xpt_depths = [
                    f"{s.file_name} (@ {s.stationary_depth_m}m, {s.declared_start}-{s.declared_stop}s)"
                    for s in well.las_summaries
                    if s.index_type == "TIME"
                ]
                log_lines.append(f"  Detalle de Archivos .LAS en Tiempo ({well.time_files_count}):")
                for xd in xpt_depths:
                    log_lines.append(f"    * {xd}")

            log_lines.append(
                f"  Curvas únicas en Profundidad (lasio) ({len(well.unique_depth_curves)}): "
                + ", ".join(well.unique_depth_curves)
            )
            log_lines.append(
                f"  Curvas repetidas detectadas ({len(well.repeated_depth_curves)}):"
            )
            for mnem, occs in well.repeated_depth_curves.items():
                occ_desc = "; ".join(
                    f"{o['assigned_name']} en {o['source_file']} [{o['valid_min_depth']}-{o['valid_max_depth']}m, step={o['step']}m]"
                    for o in occs
                )
                log_lines.append(f"    - {mnem}: {occ_desc}")
            log_lines.append("  Imágenes generadas:")
            log_lines.append(f"    - {cov_img.name}")
            log_lines.append(f"    - {curv_img.name}")
            log_lines.append("-" * 80)
            log_lines.append("")

    LOG_FILE.write_text("\n".join(log_lines), encoding="utf-8")

    summary_payload = [w.to_dict() for w in results]
    SUMMARY_JSON.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    assert LOG_FILE.exists()
    assert SUMMARY_JSON.exists()

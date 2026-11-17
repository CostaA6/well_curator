"""
Suite de pruebas automatizadas y validación visual para Spec 003:
- Agnóstico al modelo LLM: proveedor OpenAI-compatible mock con fallback `json_schema -> json_object` y reintento.
- Multi-campo & Geodesia (`pyproj`): prioridad a Gauss-Krüger (Fajas 1-7) y proyección automática Lat/Lon -> GK / UTM.
- Conversión automática `FT -> M` en curvas de profundidad.
- Compactación inteligente de encabezados, tolerancia a fallos por pozo, caché de metadatos y modo incremental.
- Prueba en vivo con `Gemma-4-E4B` en RTX 4060 sobre los 6 pozos reales de `Input/`.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from scripts.field_profiles import (
    FieldProfile,
    detect_gk_faja_from_lon,
    load_field_profile,
    normalize_well_metadata,
)
from scripts.las_explorer import (
    FEET_TO_METERS,
    _compact_raw_header_for_llm,
    explore_well_directory,
)
from scripts.llm_extractor import LLMConfig, ManagedLLMSession
from scripts.run_pipeline import run_full_pipeline
from scripts.visual_validator import plot_ft_to_m_alignment, plot_geodesic_wells_map
from scripts.well_exporter import export_well_to_output

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUT_DIR = PROJECT_ROOT / "Input"
OUTPUT_DIR = PROJECT_ROOT / "output"
SPEC003_TEST_DIR = Path(__file__).resolve().parent
IMAGES_DIR = SPEC003_TEST_DIR / "images"
REPORT_LOG = SPEC003_TEST_DIR / "spec_003_report.log"


def test_rf1_rf2_agnostic_llm_client_and_schema_fallback(monkeypatch) -> None:
    """
    RF-1 / RF-2: Levanta un servidor HTTP OpenAI-compatible simulado que:
    1) Valida Authorization: Bearer <API_KEY>.
    2) Rechaza `json_schema` (HTTP 400) obligando al fallback `json_object`.
    3) Devuelve un JSON inválido la primera vez y válido en el reintento.
    """
    call_log: List[str] = []

    class _MockHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            auth = self.headers.get("Authorization")
            if auth != "Bearer test-secret-token":
                self.send_response(401)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", "0"))
            req_body = json.loads(self.rfile.read(length).decode("utf-8"))
            resp_fmt = req_body.get("response_format", {})
            mode = resp_fmt.get("type", "plain")
            call_log.append(mode)

            if mode == "json_schema":
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "json_schema strict not supported"}')
                return

            # Primer intento en json_object devuelve JSON truncado para probar reintento
            if len([m for m in call_log if m == "json_object"]) == 1:
                content = '{"nombre_pozo": ""}'
            else:
                content = json.dumps({
                    "nombre_pozo": "NEUQUEN-TEST-1",
                    "uwi": "nqn-001",
                    "coord_x": None,
                    "coord_y": None,
                    "latitud": -38.5123,
                    "longitud": -68.9102,
                    "sistema_coordenadas_origen": "LAT_LON",
                    "cota": 612.4,
                    "referencia_cota": "KB",
                    "fuente_coordenadas": "LATI/LONG del header",
                    "curvas_detectadas": ["GR", "DT"],
                })

            payload = {
                "choices": [{"message": {"content": content}}],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        monkeypatch.setenv("CUSTOM_LLM_KEY", "test-secret-token")
        cfg = LLMConfig(
            provider="cloud_api",
            model_name="custom-gpt-or-gemini",
            base_url=f"http://127.0.0.1:{port}",
            api_key_env="CUSTOM_LLM_KEY",
        )
        generic_profile = load_field_profile("generic")
        with ManagedLLMSession(cfg) as session:
            meta = session.extract_well_metadata(
                "~Well\nWELL. NEUQUEN-TEST-1 : Well\n",
                field_profile=generic_profile,
            )

        assert call_log == ["json_schema", "json_object", "json_object"]
        assert meta["nombre_pozo"] == "NEUQUEN-TEST-1"
        assert meta["uwi"] == "NQN-001"  # En generic no agrega '00' argentino
        assert meta["_provider"] == "cloud_api"
        assert meta["_response_mode"] == "json_object"
        assert meta["crs_used"] == "EPSG:5344"  # -68.91° -> Faja 2 POSGAR 2007 automática
        assert 2_400_000 <= int(meta["coord_x"]) <= 2_600_000
        assert 5_700_000 <= int(meta["coord_y"]) <= 5_800_000
    finally:
        server.shutdown()
        server.server_close()


def test_rf4_geodesic_priority_and_projection() -> None:
    """
    RF-4: Verifica prioridad a Gauss-Krüger, corrección automática X/Y en múltiples fajas,
    proyección Lat/Lon -> POSGAR 2007 (Fajas 1..7) o UTM internacional, y faja fija en borde.
    """
    generic = load_field_profile("generic")

    # 1. Prioridad a Gauss-Krüger Faja 3 invertida (Chubut costa) incluso si también viene Lat/Lon
    raw_gk_f3 = {
        "nombre_pozo": "CHUBUT-COSTA-1",
        "uwi": "ch01001",
        "coord_x": 4915200.0,  # Norte puesto en X por error
        "coord_y": 3465120.0,  # Este Faja 3 puesto en Y
        "latitud": -45.90,
        "longitud": -66.45,
        "cota": 125.0,
    }
    norm_gk3 = normalize_well_metadata(raw_gk_f3, profile=generic)
    assert norm_gk3["coord_x"] == 3465120
    assert norm_gk3["coord_y"] == 4915200
    assert norm_gk3["crs_used"] == "EPSG:5345"  # Faja 3
    assert "GK" in str(norm_gk3["coord_resolution_note"])

    # 2. Solo Lat/Lon en Salta/Noroeste (-22.6°, -63.7°) -> proyecta a Faja 3 o 4 (-63.7° está en Faja 4: -63° CM)
    assert detect_gk_faja_from_lon(-63.7) == 4
    raw_salta = {
        "nombre_pozo": "NOROESTE-1",
        "uwi": "noa-99",
        "coord_x": None,
        "coord_y": None,
        "latitud": -22.6,
        "longitud": -63.7,
        "cota": 480.0,
    }
    norm_salta = normalize_well_metadata(raw_salta, profile=generic)
    assert norm_salta["crs_used"] == "EPSG:5346"  # Faja 4
    assert 4_400_000 <= int(norm_salta["coord_x"]) <= 4_600_000
    assert 7_400_000 <= int(norm_salta["coord_y"]) <= 7_600_000

    # 3. Campo internacional (Permian Basin, Texas: 31.85° N, -102.35° W) -> UTM Zone 13N (EPSG:32613)
    raw_intl = {
        "nombre_pozo": "PERMIAN-A1",
        "uwi": "4232900001",
        "coord_x": None,
        "coord_y": None,
        "latitud": 31.85,
        "longitud": -102.35,
        "cota": 890.2,
    }
    norm_intl = normalize_well_metadata(raw_intl, profile=generic)
    assert norm_intl["crs_used"] == "EPSG:32613"
    assert 700_000 <= int(norm_intl["coord_x"]) <= 800_000
    assert 3_500_000 <= int(norm_intl["coord_y"]) <= 3_600_000

    # 4. Borde de Faja: un pozo en -67.3° normalmente iría a Faja 3, pero con perfil Faja 2 forzada queda en Faja 2
    gsj = load_field_profile("golfo_san_jorge_faja2")
    raw_border = {
        "nombre_pozo": "BORDE-1",
        "uwi": "AR0100009999",
        "coord_x": None,
        "coord_y": None,
        "latitud": -46.2,
        "longitud": -67.3,
        "cota": 180.0,
    }
    norm_border = normalize_well_metadata(raw_border, profile=gsj)
    assert norm_border["crs_used"] == "EPSG:5344"  # Faja 2 forzada por perfil del campo
    assert norm_border["uwi"] == "AR010000999900"


def test_rf5_rf6_ft_to_m_conversion_and_header_compaction(tmp_path: Path) -> None:
    """
    RF-5: Crea un pozo sintético con un .LAS en pies (FT) y otro en metros (M) y valida
    que se conviertan a metros y se alineen sobre la grilla común más fina, con gráfico visual.
    RF-6: Verifica compactación de líneas vacías en ~Parameter conservando las útiles.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Compactación de headers
    sample_hdr = (
        "~Version\nVERS. 2.0 : CWLS\n"
        "~Well\nWELL. TEST-POZO : Well\n"
        "~Curve\nDEPT.M : Depth\nGR.GAPI : Gamma Ray\n"
        "~Parameter\nR14 . :Remark Line 14\n#---------------\nFL1 . Y: 2.482.090,63 : Field Location 1\n"
    )
    compacted = _compact_raw_header_for_llm(sample_hdr)
    assert "R14" not in compacted
    assert "#---------------" not in compacted
    assert "Y: 2.482.090,63" in compacted

    # Pozo sintético con 1 archivo en FT (1000..1020 ft cada 0.5 ft -> 304.8..310.896 m cada 0.1524 m)
    # y 1 archivo en M (306.0..312.0 m cada 0.10 m) midiendo la misma señal sinusoidal GR(depth_m)
    well_dir = tmp_path / "SYNTH_FT_M"
    well_dir.mkdir()
    out_dir = tmp_path / "out"

    depths_ft = np.arange(1000.0, 1020.5, 0.5)
    gr_ft = 60.0 + 25.0 * np.sin((depths_ft * FEET_TO_METERS) * 0.8)

    las_ft_lines = [
        "~Version\nVERS. 2.0 : CWLS LOG ASCII STANDARD\nWRAP. NO : ONE LINE PER DEPTH STEP",
        "~Well\nSTRT.FT 1000.0 : START DEPTH\nSTOP.FT 1020.0 : STOP DEPTH\nSTEP.FT 0.5 : STEP\nNULL. -999.25 : NULL VALUE\nWELL. SYNTH-FT-M : WELL",
        "~Curve\nDEPT.FT : DEPTH IN FEET\nGR.GAPI : GAMMA RAY",
        "~A",
    ]
    for d, g in zip(depths_ft, gr_ft):
        las_ft_lines.append(f"{d:10.4f} {g:10.4f}")
    (well_dir / "run_feet.las").write_text("\n".join(las_ft_lines), encoding="utf-8")

    depths_m = np.arange(306.0, 312.1, 0.1)
    gr_m = 60.0 + 25.0 * np.sin(depths_m * 0.8)
    las_m_lines = [
        "~Version\nVERS. 2.0 : CWLS LOG ASCII STANDARD\nWRAP. NO : ONE LINE PER DEPTH STEP",
        "~Well\nSTRT.M 306.0 : START DEPTH\nSTOP.M 312.0 : STOP DEPTH\nSTEP.M 0.1 : STEP\nNULL. -999.25 : NULL VALUE\nWELL. SYNTH-FT-M : WELL",
        "~Curve\nDEPT.M : DEPTH IN METERS\nGR.GAPI : GAMMA RAY",
        "~A",
    ]
    for d, g in zip(depths_m, gr_m):
        las_m_lines.append(f"{d:10.4f} {g:10.4f}")
    (well_dir / "run_meters.las").write_text("\n".join(las_m_lines), encoding="utf-8")

    well_res, dfs = explore_well_directory(well_dir, target_depth_unit="M")
    s_ft = next(s for s in well_res.las_summaries if s.file_name == "run_feet.las")
    assert s_ft.original_index_unit == "FT"
    assert s_ft.index_unit == "M"
    assert np.isclose(s_ft.step, 0.1524, atol=1e-4)

    well_res.llm_metadata = {
        "nombre_pozo": "SYNTH-FT-M",
        "uwi": "SYNTH-001",
        "coord_x": None,
        "coord_y": None,
        "latitud": -46.63,
        "longitud": -69.23,
        "cota": 300.0,
    }
    generic = load_field_profile("generic")
    ws = export_well_to_output(
        well=well_res,
        well_dfs=dfs,
        output_dir=out_dir,
        repeat_strategy="merge",
        field_profile=generic,
    )
    assert ws.grid_step == 0.1  # El paso más chico entre 0.1524m (0.5 ft) y 0.10m es 0.10m
    assert ws.crs_used == "EPSG:5344"

    df_out = pd.read_csv(ws.depth_csv_path)
    # Verificar que en el tramo solapado (306..310.8m) el valor GR mergeado coincida con la onda analítica real
    overlap = df_out[(df_out["DEPTH"] >= 306.0) & (df_out["DEPTH"] <= 310.8)]
    expected_gr = 60.0 + 25.0 * np.sin(overlap["DEPTH"].to_numpy() * 0.8)
    max_err = float(np.max(np.abs(overlap["GR"].to_numpy() - expected_gr)))
    assert max_err < 0.05

    align_img = plot_ft_to_m_alignment(
        ft_depths_m=depths_ft * FEET_TO_METERS,
        ft_values=gr_ft,
        m_depths=depths_m,
        m_values=gr_m,
        unified_csv_path=ws.depth_csv_path,
        curve_name="GR",
        output_dir=IMAGES_DIR,
    )
    assert align_img.exists() and align_img.stat().st_size > 10000


def test_rf7_live_batch_pipeline_incremental_cache_and_fault_tolerance(tmp_path: Path) -> None:
    """
    RF-7 + RNF-2 + RNF-3:
    1) Ejecuta el pipeline completo en vivo con Gemma-4-E4B (GPU RTX 4060) sobre los 6 pozos reales de Input/.
    2) Verifica reanudación incremental (`incremental=True`) sin re-invocar el LLM.
    3) Verifica re-exportación rápida desde caché (`use_cache=True`, estrategia `merge`) en carpeta temporal.
    4) Verifica aislamiento de fallos creando una carpeta rota junto a una válida y comprobando `batch_errors.log`.
    5) Genera el mapa geodésico `geodesic_wells_map.png` y `spec_003_report.log`.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    gsj_profile = load_field_profile("golfo_san_jorge_faja2")
    cfg = LLMConfig(provider="llama_cpp_local", gpu_device="Vulkan1")

    # 1) Corrida en vivo completa limpia sobre los 6 pozos reales hacia output/
    report = run_full_pipeline(
        input_dir=INPUT_DIR,
        output_dir=OUTPUT_DIR,
        field_profile=gsj_profile,
        llm_config=cfg,
        repeat_strategy="keep_all",
        incremental=False,
        use_cache=False,
        clean_output_first=True,
    )
    assert report.processed_count == 6
    assert report.failed_count == 0
    assert (OUTPUT_DIR / "well_metadata.xlsx").exists()
    assert (OUTPUT_DIR / "batch_run_report.json").exists()
    assert len(list((OUTPUT_DIR / "metadata_cache").glob("*_metadata.json"))) == 6

    # 2) Corrida incremental: debe saltear los 6 pozos sin levantar llama-server.exe
    report_inc = run_full_pipeline(
        input_dir=INPUT_DIR,
        output_dir=OUTPUT_DIR,
        field_profile=gsj_profile,
        llm_config=cfg,
        incremental=True,
    )
    assert report_inc.processed_count == 0
    assert report_inc.skipped_existing_count == 6
    assert report_inc.duration_s < 5.0

    # 3) Prueba de caché (use_cache=True) + tolerancia a fallos sobre un lote sintético
    batch_in = tmp_path / "batch_input"
    batch_out = tmp_path / "batch_output"
    batch_in.mkdir()
    shutil.copytree(INPUT_DIR / "YPF.SC.CVa-25", batch_in / "YPF.SC.CVa-25")
    shutil.copytree(OUTPUT_DIR / "metadata_cache", batch_out / "metadata_cache")

    # Carpeta defectuosa (sin archivos .las válidos)
    broken_dir = batch_in / "POZO_ROTO"
    broken_dir.mkdir()
    (broken_dir / "corrupto.las").write_text("BASURA TOTAL NO ES UN LAS", encoding="utf-8")

    # Agregamos un pozo extra en Lat/Lon en caché para que salga proyectado en el mapa
    report_fault = run_full_pipeline(
        input_dir=batch_in,
        output_dir=batch_out,
        field_profile=gsj_profile,
        llm_config=cfg,
        repeat_strategy="merge",
        use_cache=True,
    )
    assert report_fault.processed_count == 1
    assert report_fault.failed_count == 1
    assert report_fault.cache_hits == 1
    assert (batch_out / "batch_errors.log").exists()
    assert "POZO_ROTO" in (batch_out / "batch_errors.log").read_text(encoding="utf-8")

    # Generar gráfico geodésico combinando los 6 pozos reales + 1 pozo proyectado desde Lat/Lon
    projected_sample = normalize_well_metadata(
        {
            "nombre_pozo": "CVa-LATLON-TEST",
            "uwi": "AR0100009999",
            "coord_x": None,
            "coord_y": None,
            "latitud": -46.632,
            "longitud": -69.235,
            "cota": 295.0,
        },
        profile=load_field_profile("generic"),
    )
    map_records = list(report.processed_wells) + [
        {
            "well_name": projected_sample["nombre_pozo"],
            "coord_x": projected_sample["coord_x"],
            "coord_y": projected_sample["coord_y"],
            "crs_used": projected_sample["crs_used"],
            "coord_note": projected_sample["coord_resolution_note"],
        }
    ]
    map_img = plot_geodesic_wells_map(map_records, IMAGES_DIR)
    assert map_img.exists() and map_img.stat().st_size > 10000

    # Escribir log de auditoría Spec 003
    log_lines = [
        "================================================================================",
        "REPORTE SPEC 003 — MULTI-CAMPO, AGNÓSTICO A LLM Y BATCH ESCALABLE",
        "================================================================================",
        f"Corrida en vivo (6 pozos Input/): {report.duration_s} s | Perfil: {report.field_profile} | LLM: {report.llm_model}",
        f"Corrida incremental (skip_existing): {report_inc.duration_s} s | Salteados: {report_inc.skipped_existing_count}",
        f"Prueba tolerancia a fallos + caché: Procesados={report_fault.processed_count}, Fallidos={report_fault.failed_count}",
        "",
        "Detalle Geodésico de Pozos:",
    ]
    for pw in report.processed_wells:
        log_lines.append(
            f"  - {pw['well_name']} (UWI={pw['uwi']}): X={pw['coord_x']}, Y={pw['coord_y']} "
            f"[{pw['crs_used']}] | {pw['coord_note']}"
        )
    REPORT_LOG.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

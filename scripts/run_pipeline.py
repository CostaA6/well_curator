"""
Script integrador de punta a punta (End-to-End Pipeline):
Ejecuta el flujo completo en vivo sin cachés intermedias:
  1. Escanea `Input/` y corta los encabezados puros (triple barrera) con Python + lee curvas con `lasio`.
  2. Levanta `llama-server.exe` en la GPU RTX 4060 (`Vulkan1`) con `gemma-4-E4B-it-Q4_K_M.gguf`.
  3. Envía los encabezados crudos de cada pozo al LLM y recibe el JSON estructurado.
  4. Normaliza metadatos, arma la grilla común al paso más chico y exporta incrementalmente:
     - `output/well_metadata.xlsx`
     - `output/wells/<Well>_selected.csv`
     - `output/time_logs/xpt_stations_summary.csv` + CSVs por estación en tiempo.
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from pathlib import Path
from typing import List, Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.las_explorer import explore_all_wells
from scripts.llm_extractor import LocalLlamaServer
from scripts.well_exporter import WellExportSummary, export_well_to_output

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_pipeline")


def run_full_pipeline(
    input_dir: Path = PROJECT_ROOT / "Input",
    output_dir: Path = PROJECT_ROOT / "output",
    repeat_strategy: Literal["keep_all", "merge"] = "keep_all",
    clean_output_first: bool = True,
    gpu_device: str = "Vulkan1",
) -> List[WellExportSummary]:
    """Ejecuta el flujo completo de curación desde `Input/` hasta `output/` con el LLM en vivo."""
    t_start = time.time()
    logger.info("1/3 Escaneando carpetas de pozos en %s ...", input_dir)
    well_results, all_dfs = explore_all_wells(input_dir)
    logger.info("Se encontraron %d pozos para procesar.", len(well_results))

    if clean_output_first and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries: List[WellExportSummary] = []

    logger.info("2/3 Iniciando modelo local Gemma-4-E4B en GPU (%s)...", gpu_device)
    with LocalLlamaServer(gpu_device=gpu_device) as llm_server:
        for idx, well in enumerate(well_results, start=1):
            logger.info(
                "[%d/%d] Procesando pozo %s (%d archivos .LAS) con Gemma-4-E4B...",
                idx,
                len(well_results),
                well.well_folder_name,
                well.las_files_count,
            )
            meta = llm_server.extract_well_metadata(well.consolidated_header_block)
            well.llm_metadata = meta
            logger.info(
                "  -> LLM extrajo: Well=%s | UWI=%s | X=%s | Y=%s | Cota=%s m (en %.1fs)",
                meta.get("nombre_pozo"),
                meta.get("uwi"),
                meta.get("coord_x"),
                meta.get("coord_y"),
                meta.get("cota"),
                meta.get("_inference_time_s", 0.0),
            )

            ws = export_well_to_output(
                well=well,
                well_dfs=all_dfs[well.well_folder_name],
                output_dir=output_dir,
                repeat_strategy=repeat_strategy,
                update_excel=True,
            )
            summaries.append(ws)
            logger.info(
                "  -> Exportado %s (%d filas, step=%sm, %d curvas) y actualizado well_metadata.xlsx",
                ws.depth_csv_path.name,
                ws.total_grid_rows,
                ws.grid_step,
                len(ws.exported_depth_curves),
            )

    total_s = round(time.time() - t_start, 2)
    logger.info("3/3 Flujo completo finalizado con éxito en %.1f s. Salidas en %s", total_s, output_dir)
    return summaries


if __name__ == "__main__":
    run_full_pipeline()

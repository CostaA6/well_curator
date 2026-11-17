"""
Script integrador de punta a punta (Spec 003 — Multi-Campo, Agnóstico al Modelo y Batch Escalable):
1. Carga el `FieldProfile` (`generic`, `golfo_san_jorge_faja2`, etc.) y la `LLMConfig` (`llama_cpp_local`,
   `openai_compatible_local` o `cloud_api`).
2. Procesa los pozos de `input_dir` uno a la vez (memoria constante), con soporte para:
   - `--incremental`: saltea pozos ya exportados en `output/well_metadata.xlsx` y `output/wells/`.
   - `--use-cache`: reutiliza `output/metadata_cache/<carpeta>_metadata.json` para re-exportar sin re-ejecutar LLM.
   - Aislamiento de errores por pozo: registra fallos en `output/batch_errors.log` sin abortar el lote.
3. Emite `output/batch_run_report.json` con trazabilidad completa (CRS usado, tiempos, estado por pozo).
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.field_profiles import FieldProfile, load_field_profile
from scripts.las_explorer import explore_well_directory
from scripts.llm_extractor import DEFAULT_MODEL_PATH, LLMConfig, ManagedLLMSession
from scripts.well_exporter import WellExportSummary, export_well_to_output

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_pipeline")


@dataclass
class BatchRunReport:
    """Resumen estructurado de una corrida batch (Spec 003 - RF-7)."""
    started_at: str
    finished_at: str
    duration_s: float
    field_profile: str
    llm_provider: str
    llm_model: str
    total_well_folders: int
    processed_count: int
    skipped_existing_count: int
    cache_hits: int
    failed_count: int
    processed_wells: List[Dict[str, object]]
    failed_wells: List[Dict[str, str]]


def _load_existing_processed_wells(output_dir: Path) -> Set[str]:
    """Detecta qué pozos ya existen completos en `output/well_metadata.xlsx` y `output/wells/`."""
    excel_path = output_dir / "well_metadata.xlsx"
    wells_dir = output_dir / "wells"
    existing: Set[str] = set()

    if excel_path.exists():
        try:
            df = pd.read_excel(excel_path)
            for _, row in df.iterrows():
                wname = str(row.get("Well") or "").strip()
                fstem = str(row.get("File") or f"{wname}_selected").strip()
                if wname and (wells_dir / f"{fstem}.csv").exists():
                    existing.add(wname)
        except Exception as e:
            logger.warning("No se pudo leer %s para modo incremental: %s", excel_path, e)

    # También mapear por metadata_cache para asociar well_folder_name -> well_name
    cache_dir = output_dir / "metadata_cache"
    if cache_dir.exists():
        for cf in cache_dir.glob("*_metadata.json"):
            try:
                data = json.loads(cf.read_text(encoding="utf-8"))
                wname = str(data.get("well_name") or "")
                folder_name = str(data.get("well_folder_name") or "")
                if wname in existing and folder_name:
                    existing.add(folder_name)
            except Exception:
                continue

    return existing


def run_full_pipeline(
    input_dir: Path = PROJECT_ROOT / "Input",
    output_dir: Path = PROJECT_ROOT / "output",
    field_profile: str | FieldProfile = "generic",
    llm_config: Optional[LLMConfig] = None,
    repeat_strategy: Literal["keep_all", "merge"] = "keep_all",
    incremental: bool = False,
    use_cache: bool = False,
    clean_output_first: bool = False,
) -> BatchRunReport:
    """Ejecuta el flujo curador de punta a punta con tolerancia a fallos y soporte multi-campo/multi-LLM."""
    started_at = datetime.now(timezone.utc).isoformat()
    t_start = time.time()

    profile = (
        field_profile
        if isinstance(field_profile, FieldProfile)
        else load_field_profile(field_profile)
    )
    cfg = llm_config or LLMConfig()

    if clean_output_first and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    well_dirs = sorted(p for p in input_dir.iterdir() if p.is_dir())
    existing_set = _load_existing_processed_wells(output_dir) if incremental else set()

    logger.info(
        "Iniciando pipeline | Pozos detectados: %d | Perfil: %s | LLM: %s (%s) | Incremental: %s | Caché: %s",
        len(well_dirs),
        profile.profile_name,
        cfg.provider,
        cfg.model_name,
        incremental,
        use_cache,
    )

    processed_wells: List[Dict[str, object]] = []
    failed_wells: List[Dict[str, str]] = []
    error_lines: List[str] = []
    skipped_count = 0
    cache_hits = 0

    # Filtrar pozos a procesar antes de decidir si hace falta levantar el servidor LLM
    pending_dirs: List[Path] = []
    cached_meta_by_folder: Dict[str, Dict[str, object]] = {}

    for wd in well_dirs:
        if incremental and wd.name in existing_set:
            logger.info("[SKIP] Pozo %s ya curado en output/", wd.name)
            skipped_count += 1
            continue

        if use_cache:
            cache_file = output_dir / "metadata_cache" / f"{wd.name}_metadata.json"
            if cache_file.exists():
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                    if isinstance(cached.get("metadata"), dict):
                        cached_meta_by_folder[wd.name] = cached["metadata"]
                        cache_hits += 1
                except Exception as ce:
                    logger.warning("Caché inválida para %s (%s), se usará LLM.", wd.name, ce)

        pending_dirs.append(wd)

    need_llm = any(wd.name not in cached_meta_by_folder for wd in pending_dirs)

    def _process_one_well(wd: Path, session: Optional[ManagedLLMSession]) -> None:
        well_res, dfs = explore_well_directory(wd, target_depth_unit=profile.target_depth_unit)
        if well_res.las_files_count == 0:
            raise ValueError(f"La carpeta {wd.name} no contiene archivos .las/.LAS.")

        if wd.name in cached_meta_by_folder:
            well_res.llm_metadata = cached_meta_by_folder[wd.name]
            source_tag = "cache"
        else:
            assert session is not None
            well_res.llm_metadata = session.extract_well_metadata(
                consolidated_header_block=well_res.consolidated_header_block,
                field_profile=profile,
                fallback_well_name=wd.name,
            )
            source_tag = cfg.provider

        ws: WellExportSummary = export_well_to_output(
            well=well_res,
            well_dfs=dfs,
            output_dir=output_dir,
            repeat_strategy=repeat_strategy,
            update_excel=True,
            field_profile=profile,
            save_metadata_cache=True,
        )

        processed_wells.append({
            "folder": wd.name,
            "well_name": ws.well_name,
            "uwi": ws.uwi,
            "coord_x": ws.coord_x,
            "coord_y": ws.coord_y,
            "crs_used": ws.crs_used,
            "coord_note": ws.coord_resolution_note,
            "grid_step": ws.grid_step,
            "rows": ws.total_grid_rows,
            "curves_count": len(ws.exported_depth_curves),
            "time_files": len(ws.time_csv_paths),
            "metadata_source": source_tag,
        })
        logger.info(
            "  [OK] %s -> X=%s Y=%s [%s] | step=%sm (%d filas, %d curvas)",
            ws.well_name,
            ws.coord_x,
            ws.coord_y,
            ws.crs_used,
            ws.grid_step,
            ws.total_grid_rows,
            len(ws.exported_depth_curves),
        )

    if pending_dirs:
        if need_llm:
            with ManagedLLMSession(cfg) as session:
                for idx, wd in enumerate(pending_dirs, start=1):
                    logger.info("[%d/%d] Procesando %s...", idx, len(pending_dirs), wd.name)
                    try:
                        _process_one_well(wd, session)
                    except Exception as exc:
                        err_msg = f"[{wd.name}] Error: {type(exc).__name__}: {exc}"
                        logger.error(err_msg)
                        failed_wells.append({"folder": wd.name, "error": str(exc)})
                        error_lines.append(err_msg)
        else:
            for idx, wd in enumerate(pending_dirs, start=1):
                logger.info("[%d/%d] Re-exportando desde caché %s...", idx, len(pending_dirs), wd.name)
                try:
                    _process_one_well(wd, None)
                except Exception as exc:
                    err_msg = f"[{wd.name}] Error: {type(exc).__name__}: {exc}"
                    logger.error(err_msg)
                    failed_wells.append({"folder": wd.name, "error": str(exc)})
                    error_lines.append(err_msg)

    if error_lines:
        errors_log = output_dir / "batch_errors.log"
        errors_log.write_text("\n".join(error_lines) + "\n", encoding="utf-8")

    finished_at = datetime.now(timezone.utc).isoformat()
    report = BatchRunReport(
        started_at=started_at,
        finished_at=finished_at,
        duration_s=round(time.time() - t_start, 2),
        field_profile=profile.profile_name,
        llm_provider=cfg.provider,
        llm_model=cfg.model_name,
        total_well_folders=len(well_dirs),
        processed_count=len(processed_wells),
        skipped_existing_count=skipped_count,
        cache_hits=cache_hits,
        failed_count=len(failed_wells),
        processed_wells=processed_wells,
        failed_wells=failed_wells,
    )

    report_path = output_dir / "batch_run_report.json"
    report_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Pipeline finalizado en %.1f s | Procesados=%d | Salteados=%d | Errores=%d",
        report.duration_s,
        report.processed_count,
        report.skipped_existing_count,
        report.failed_count,
    )
    return report


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Well Curator — Pipeline batch multi-campo y agnóstico a LLM")
    p.add_argument("--input", type=Path, default=PROJECT_ROOT / "Input")
    p.add_argument("--output", type=Path, default=PROJECT_ROOT / "output")
    p.add_argument("--field-profile", default="generic", help="Nombre de perfil en config/fields/ o ruta JSON")
    p.add_argument(
        "--provider",
        choices=["llama_cpp_local", "openai_compatible_local", "cloud_api"],
        default="llama_cpp_local",
    )
    p.add_argument("--model", default="gemma-4-E4B-it-Q4_K_M.gguf", help="Nombre de modelo o archivo GGUF")
    p.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    p.add_argument("--base-url", default="http://127.0.0.1:8089")
    p.add_argument("--api-key-env", default="LLM_API_KEY")
    p.add_argument("--gpu-device", default="Vulkan1")
    p.add_argument("--repeat-strategy", choices=["keep_all", "merge"], default="keep_all")
    p.add_argument("--incremental", action="store_true", help="Saltea pozos ya presentes en output/")
    p.add_argument("--use-cache", action="store_true", help="Reutiliza JSON en output/metadata_cache/")
    p.add_argument("--clean", action="store_true", help="Limpia output/ antes de iniciar")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    resolved_model_path = args.model_path
    if args.provider == "llama_cpp_local" and args.model != DEFAULT_MODEL_PATH.name:
        cand = PROJECT_ROOT / "model" / args.model
        if cand.exists():
            resolved_model_path = cand

    cfg = LLMConfig(
        provider=args.provider,
        model_name=args.model,
        model_path=resolved_model_path,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        gpu_device=args.gpu_device,
    )
    run_full_pipeline(
        input_dir=args.input,
        output_dir=args.output,
        field_profile=args.field_profile,
        llm_config=cfg,
        repeat_strategy=args.repeat_strategy,
        incremental=args.incremental,
        use_cache=args.use_cache,
        clean_output_first=args.clean,
    )

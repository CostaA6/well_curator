"""
Módulo de homogeneización en grilla común de profundidad y exportación a `output/` (Spec 002):
- Normaliza Nombre, UWI (14 caracteres), Coordenada X (~2.481.xxx Este), Coordenada Y (~4.834.xxx Norte) y Cota.
- Genera un archivo CSV por pozo en `output/wells/<Well>_selected.csv` sobre la grilla común con el paso más chico (`min_step`), usando `NaN` para valores nulos.
- Actualiza incrementalmente el Excel maestro `output/well_metadata.xlsx` con la matriz de curvas (`yes` / `no`).
- Exporta los registros indexados en tiempo (`TIME.S` / XPT) en `output/time_logs/`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
import pandas as pd

from scripts.field_profiles import FieldProfile, load_field_profile, normalize_well_metadata
from scripts.las_explorer import LasFileSummary, WellExplorationResult

logger = logging.getLogger(__name__)

FIXED_EXCEL_COLS = ["File", "Well", "UWI", "Start", "Stop", "Step", "X", "Y", "Elevation"]
FIXED_CSV_COLS = ["DEPTH", "nombre_pozo", "UWI", "XWELL", "YWELL", "COTA"]


@dataclass
class WellExportSummary:
    """Resumen del proceso de exportación de un pozo hacia `output/`."""
    well_name: str
    uwi: str
    coord_x: float | int
    coord_y: float | int
    elevation: float
    grid_start: float
    grid_stop: float
    grid_step: float
    total_grid_rows: int
    exported_depth_curves: List[str]
    depth_csv_path: Path
    time_csv_paths: List[Path] = field(default_factory=list)
    crs_used: str = ""
    coord_resolution_note: str = ""


def compute_effective_step(summary: LasFileSummary, df: pd.DataFrame) -> float:
    """
    Obtiene el paso efectivo de muestreo de un archivo .LAS:
    - Si `STEP > 0` en el encabezado, utiliza ese valor.
    - Si `STEP == 0` (ej. archivos Baker Atlas de CVa-19 y CVa-24 muestreados a 1/4 pie = 0.0762m
      con profundidades truncadas a 3 decimales), calcula el paso medio robusto y lo ajusta al
      paso estándar de perfilaje más cercano si difiere en menos de 0.5 mm.
    """
    if summary.step and summary.step > 0:
        return round(float(summary.step), 4)

    index_col = df.columns[0]
    valid_idx = df[index_col].dropna().sort_values().unique()
    if len(valid_idx) >= 2:
        diffs = np.diff(valid_idx)
        pos_diffs = diffs[diffs > 1e-5]
        if len(pos_diffs) > 0:
            med = float(np.median(pos_diffs))
            inliers = pos_diffs[(pos_diffs >= med * 0.8) & (pos_diffs <= med * 1.2)]
            mean_step = float(np.mean(inliers)) if len(inliers) > 0 else med
            standard_steps = (0.0254, 0.0508, 0.0762, 0.1, 0.1524, 0.2, 0.25, 0.3048, 0.5, 1.0)
            for std_s in standard_steps:
                if abs(mean_step - std_s) < 5e-4:
                    return std_s
            return round(mean_step, 4)

    return 0.1524


def compute_batch_depth_grid_bounds(
    wells_items: Sequence[Tuple[WellExplorationResult, Dict[str, pd.DataFrame]]],
    target_step: Optional[float] = None,
) -> Dict[str, float]:
    """
    Calcula los límites globales de profundidad (`global_start`, `global_stop`, `min_step`,
    `max_step`, `chosen_step`, `rows`) a partir de un lote de pozos cargados en memoria,
    para poder regularizar todos los pozos sobre una grilla común sin extrapolar.
    """
    min_depths: List[float] = []
    max_depths: List[float] = []
    eff_steps: List[float] = []
    wells_with_depth = 0

    for well, well_dfs in wells_items:
        depth_summaries = [s for s in well.las_summaries if s.index_type == "DEPTH" and s.file_name in well_dfs]
        if not depth_summaries:
            continue
        well_has_data = False
        for s in depth_summaries:
            raw_df = well_dfs[s.file_name]
            if raw_df is None or raw_df.empty:
                continue
            idx_col = raw_df.columns[0]
            valid_idx = raw_df[idx_col].dropna().sort_values()
            if valid_idx.empty:
                continue
            well_has_data = True
            min_depths.append(float(valid_idx.iloc[0]))
            max_depths.append(float(valid_idx.iloc[-1]))
            eff_steps.append(compute_effective_step(s, raw_df))
        if well_has_data:
            wells_with_depth += 1

    if not min_depths or not eff_steps:
        raise ValueError("Ninguno de los pozos del lote contiene datos válidos indexados en profundidad.")

    global_start = round(min(min_depths), 4)
    raw_global_stop = round(max(max_depths), 4)
    min_step = round(min(eff_steps), 4)
    max_step = round(max(eff_steps), 4)
    chosen_step = round(float(target_step), 4) if (target_step is not None and target_step > 0) else min_step

    num_steps = max(0, int(round((raw_global_stop - global_start) / chosen_step)))
    aligned_stop = round(global_start + num_steps * chosen_step, 4)
    return {
        "global_start": global_start,
        "global_stop": aligned_stop,
        "raw_global_stop": raw_global_stop,
        "min_step": min_step,
        "max_step": max_step,
        "chosen_step": chosen_step,
        "rows": num_steps + 1,
        "wells_with_depth": wells_with_depth,
    }


def build_unified_well_depth_df(
    well: WellExplorationResult,
    well_dfs: Dict[str, pd.DataFrame],
    normalized_meta: Dict[str, object],
    repeat_strategy: Literal["keep_all", "merge"] = "keep_all",
    per_curve_repeat_strategy: Optional[Dict[str, Literal["keep_all", "merge"]]] = None,
    target_grid_step: Optional[float] = None,
    target_grid_start: Optional[float] = None,
    target_grid_stop: Optional[float] = None,
) -> Tuple[pd.DataFrame, float, float, float, List[str]]:
    """
    Construye el DataFrame unificado en profundidad para un pozo.
    - Por defecto (`target_grid_* is None`) usa el paso más chico propio del pozo (`Spec 002`).
    - Si se provee `target_grid_step` (y opcionalmente `target_grid_start` / `target_grid_stop`),
      interpola linealmente las curvas sobre esa grilla común sin extrapolar fuera del
      rango medido de cada curva (Spec 004 - RF-9).

    Retorna:
      (unified_df, grid_start, grid_stop, grid_step, exported_curve_names)
    """
    depth_summaries = [s for s in well.las_summaries if s.index_type == "DEPTH"]
    if not depth_summaries:
        raise ValueError(f"El pozo {well.well_folder_name} no posee archivos .LAS en profundidad.")

    effective_steps: List[float] = []
    min_depths: List[float] = []
    max_depths: List[float] = []
    cleaned_dfs: Dict[str, Tuple[np.ndarray, pd.DataFrame, float]] = {}

    for s in depth_summaries:
        raw_df = well_dfs[s.file_name].copy()
        idx_col = raw_df.columns[0]
        raw_df = raw_df[raw_df[idx_col].notna()].sort_values(by=idx_col, ascending=True)
        raw_df = raw_df.drop_duplicates(subset=[idx_col], keep="first").reset_index(drop=True)

        eff_step = compute_effective_step(s, raw_df)
        d_vals = raw_df[idx_col].to_numpy(dtype=float)
        if len(d_vals) == 0:
            continue

        effective_steps.append(eff_step)
        min_depths.append(float(d_vals[0]))
        max_depths.append(float(d_vals[-1]))
        cleaned_dfs[s.file_name] = (d_vals, raw_df, eff_step)

    if not min_depths or not effective_steps:
        raise ValueError(f"El pozo {well.well_folder_name} no posee filas válidas en profundidad.")

    grid_start = (
        round(float(target_grid_start), 4)
        if target_grid_start is not None
        else round(min(min_depths), 4)
    )
    raw_stop = (
        round(float(target_grid_stop), 4)
        if target_grid_stop is not None
        else round(max(max_depths), 4)
    )
    if raw_stop < grid_start:
        raw_stop = grid_start

    grid_step = (
        round(float(target_grid_step), 4)
        if (target_grid_step is not None and target_grid_step > 0)
        else round(min(effective_steps), 4)
    )

    num_steps = max(0, int(round((raw_stop - grid_start) / grid_step)))
    grid_depths = np.round(grid_start + np.arange(num_steps + 1) * grid_step, 4)
    grid_stop = round(float(grid_depths[-1]), 4) if len(grid_depths) > 0 else raw_stop

    well_name = str(normalized_meta["nombre_pozo"])
    uwi = str(normalized_meta.get("uwi") or "")
    xwell = normalized_meta.get("coord_x") if normalized_meta.get("coord_x") is not None else 0
    ywell = normalized_meta.get("coord_y") if normalized_meta.get("coord_y") is not None else 0
    cota = round(float(normalized_meta["cota"]), 2) if normalized_meta.get("cota") is not None else np.nan

    base_data: Dict[str, object] = {
        "DEPTH": grid_depths,
        "nombre_pozo": well_name,
        "UWI": uwi,
        "XWELL": xwell,
        "YWELL": ywell,
        "COTA": cota,
    }

    curve_columns: Dict[str, np.ndarray] = {}

    for s in depth_summaries:
        if s.file_name not in cleaned_dfs:
            continue
        d_vals, raw_df, eff_step = cleaned_dfs[s.file_name]

        for col_pos, curve_occ in enumerate(s.curves, start=1):
            if col_pos >= raw_df.shape[1]:
                continue

            y_vals = raw_df.iloc[:, col_pos].to_numpy(dtype=float)
            valid_mask = ~np.isnan(y_vals) & ~np.isnan(d_vals)

            projected = np.full(len(grid_depths), np.nan, dtype=float)
            if np.any(valid_mask):
                dv = d_vals[valid_mask]
                yv = y_vals[valid_mask]

                if len(dv) == 1:
                    nearest_idx = int(np.argmin(np.abs(grid_depths - dv[0])))
                    if abs(grid_depths[nearest_idx] - dv[0]) <= max(grid_step, eff_step) * 0.6:
                        projected[nearest_idx] = round(float(yv[0]), 6)
                else:
                    in_range = (grid_depths >= dv[0] - 1e-4) & (grid_depths <= dv[-1] + 1e-4)
                    projected[in_range] = np.round(np.interp(grid_depths[in_range], dv, yv), 6)

                    # Preservar brechas internas donde faltan datos (> 2.5 * eff_step) como NaN
                    gaps = np.diff(dv)
                    gap_indices = np.where(gaps > 2.5 * eff_step)[0]
                    for gi in gap_indices:
                        gap_start = dv[gi]
                        gap_end = dv[gi + 1]
                        in_gap = (grid_depths > gap_start + 1e-4) & (grid_depths < gap_end - 1e-4)
                        projected[in_gap] = np.nan

            curve_strategy = (
                per_curve_repeat_strategy.get(curve_occ.original_mnemonic, repeat_strategy)
                if per_curve_repeat_strategy
                else repeat_strategy
            )

            if curve_strategy == "keep_all":
                col_name = curve_occ.assigned_name
                curve_columns[col_name] = projected
            else:
                col_name = curve_occ.original_mnemonic
                if col_name not in curve_columns:
                    curve_columns[col_name] = projected
                else:
                    # En estrategia 'merge', completar NaNs con la nueva corrida sin pisar datos válidos previos
                    existing = curve_columns[col_name]
                    fill_mask = np.isnan(existing) & ~np.isnan(projected)
                    existing[fill_mask] = projected[fill_mask]
                    curve_columns[col_name] = existing

    # Mantener orden alfabético de curvas en el CSV tras las 6 columnas fijas
    sorted_curve_names = sorted(curve_columns.keys())
    for cname in sorted_curve_names:
        base_data[cname] = curve_columns[cname]

    unified_df = pd.DataFrame(base_data)
    return unified_df, grid_start, grid_stop, grid_step, sorted_curve_names


def export_well_time_logs(
    well: WellExplorationResult,
    well_dfs: Dict[str, pd.DataFrame],
    normalized_meta: Dict[str, object],
    output_dir: Path,
    field_profile: Optional[FieldProfile] = None,
) -> Tuple[List[Path], List[Dict[str, object]]]:
    """
    Exporta los archivos .LAS indexados en tiempo (ej. estaciones de presión XPT/MDT/RFT)
    a `output/time_logs/<Well>/` y retorna las filas para `xpt_stations_summary.csv` (Spec 002 - RF-6).
    """
    time_summaries = [s for s in well.las_summaries if s.index_type == "TIME"]
    if not time_summaries:
        return [], []

    well_name = str(normalized_meta["nombre_pozo"])
    uwi = str(normalized_meta.get("uwi") or "")
    xwell = normalized_meta.get("coord_x") if normalized_meta.get("coord_x") is not None else 0
    ywell = normalized_meta.get("coord_y") if normalized_meta.get("coord_y") is not None else 0
    cota = round(float(normalized_meta["cota"]), 2) if normalized_meta.get("cota") is not None else np.nan

    well_time_dir = output_dir / "time_logs" / well_name
    well_time_dir.mkdir(parents=True, exist_ok=True)

    exported_paths: List[Path] = []
    station_rows: List[Dict[str, object]] = []

    pressure_candidates = tuple(
        field_profile.pressure_summary_mnemonics
        if field_profile and field_profile.pressure_summary_mnemonics
        else ("MTEP_SAP", "MTEP_HYD", "CP_SAP", "CP_HYD", "PCO_HYD", "PTV_XPT")
    )

    for s in time_summaries:
        raw_df = well_dfs[s.file_name].copy()
        idx_col = raw_df.columns[0]
        raw_df = raw_df[raw_df[idx_col].notna()].sort_values(by=idx_col, ascending=True).reset_index(drop=True)

        t_vals = raw_df[idx_col].to_numpy(dtype=float)
        start_t = round(float(t_vals[0]), 2) if len(t_vals) > 0 else s.declared_start
        stop_t = round(float(t_vals[-1]), 2) if len(t_vals) > 0 else s.declared_stop
        duration_s = round(stop_t - start_t, 2) if (not np.isnan(start_t) and not np.isnan(stop_t)) else np.nan

        out_df = pd.DataFrame({
            "TIME": t_vals,
            "nombre_pozo": well_name,
            "UWI": uwi,
            "XWELL": xwell,
            "YWELL": ywell,
            "COTA": cota,
            "STATION_DEPTH_M": s.stationary_depth_m if s.stationary_depth_m is not None else np.nan,
        })

        for col in raw_df.columns[1:]:
            clean_col = col.split(":")[0].strip().rstrip(".").upper()
            if clean_col in out_df.columns:
                clean_col = col.strip().upper()
            out_df[clean_col] = raw_df[col].to_numpy(dtype=float)

        csv_path = well_time_dir / f"{Path(s.file_name).stem}.csv"
        out_df.to_csv(csv_path, index=False, na_rep="NaN")
        exported_paths.append(csv_path)

        row_info: Dict[str, object] = {
            "Well": well_name,
            "UWI": uwi,
            "XWELL": xwell,
            "YWELL": ywell,
            "COTA": cota,
            "File": s.file_name,
            "Station_Depth_m": s.stationary_depth_m if s.stationary_depth_m is not None else np.nan,
            "Start_Time_s": start_t,
            "Stop_Time_s": stop_t,
            "Duration_s": duration_s,
            "Samples": len(out_df),
        }
        for pcol in pressure_candidates:
            if pcol in out_df.columns and out_df[pcol].notna().any():
                # Último valor estabilizado de la estación
                row_info[f"{pcol}_final"] = round(float(out_df[pcol].dropna().iloc[-1]), 3)
            else:
                row_info[f"{pcol}_final"] = np.nan

        station_rows.append(row_info)

    return exported_paths, station_rows


def update_time_stations_summary(station_rows: List[Dict[str, object]], output_dir: Path) -> Optional[Path]:
    """Actualiza incrementalmente `output/time_logs/xpt_stations_summary.csv`."""
    if not station_rows:
        return None

    time_dir = output_dir / "time_logs"
    time_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = time_dir / "xpt_stations_summary.csv"

    new_df = pd.DataFrame(station_rows)
    if summary_csv.exists():
        existing_df = pd.read_csv(summary_csv)
        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["Well", "File"], keep="last")
    else:
        combined = new_df

    combined = combined.sort_values(by=["Well", "Station_Depth_m", "Start_Time_s"], ascending=[True, True, True])
    combined.to_csv(summary_csv, index=False, na_rep="NaN")
    return summary_csv


def update_master_excel(
    well_Summaries: List[WellExportSummary],
    excel_path: Path,
) -> Path:
    """
    Crea o actualiza incrementalmente `output/well_metadata.xlsx` respetando el formato exacto:
    `File | Well | UWI | Start | Stop | Step | X | Y | Elevation | <CURVA_1> | <CURVA_2> | ...`
    con valores 'yes' / 'no' (Spec 002 - RF-5).
    """
    excel_path.parent.mkdir(parents=True, exist_ok=True)

    existing_rows: Dict[str, Dict[str, object]] = {}
    all_curves_set = set()

    if excel_path.exists():
        old_df = pd.read_excel(excel_path)
        old_curve_cols = [c for c in old_df.columns if c not in FIXED_EXCEL_COLS]
        all_curves_set.update(old_curve_cols)
        for _, row in old_df.iterrows():
            wname = str(row["Well"])
            existing_rows[wname] = row.to_dict()

    for ws in well_Summaries:
        all_curves_set.update(ws.exported_depth_curves)

    sorted_curves = sorted(all_curves_set)

    for ws in well_Summaries:
        present_set = set(ws.exported_depth_curves)
        row_dict: Dict[str, object] = {
            "File": ws.depth_csv_path.stem,
            "Well": ws.well_name,
            "UWI": ws.uwi,
            "Start": ws.grid_start,
            "Stop": ws.grid_stop,
            "Step": ws.grid_step,
            "X": ws.coord_x,
            "Y": ws.coord_y,
            "Elevation": ws.elevation,
        }
        for c in sorted_curves:
            row_dict[c] = "yes" if c in present_set else "no"
        existing_rows[ws.well_name] = row_dict

    # Asegurar que todas las filas previas tengan todas las columnas nuevas con 'no'
    final_rows: List[Dict[str, object]] = []
    for wname in sorted(existing_rows.keys()):
        r = existing_rows[wname]
        ordered_row: Dict[str, object] = {}
        for fc in FIXED_EXCEL_COLS:
            ordered_row[fc] = r.get(fc, "")
        for c in sorted_curves:
            val = str(r.get(c, "no")).strip().lower()
            ordered_row[c] = "yes" if val == "yes" else "no"
        final_rows.append(ordered_row)

    master_df = pd.DataFrame(final_rows, columns=FIXED_EXCEL_COLS + sorted_curves)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        master_df.to_excel(writer, index=False, sheet_name="Well_Metadata")
        ws_sheet = writer.sheets["Well_Metadata"]

        header_font = Font(bold=True)
        center_align = Alignment(horizontal="center", vertical="center")
        yes_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

        for col_idx, col_name in enumerate(master_df.columns, start=1):
            cell = ws_sheet.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.alignment = center_align

            if col_name in FIXED_EXCEL_COLS:
                ws_sheet.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 16
            else:
                ws_sheet.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = max(len(col_name) + 3, 8)
                for row_idx in range(2, len(master_df) + 2):
                    val_cell = ws_sheet.cell(row=row_idx, column=col_idx)
                    if str(val_cell.value).lower() == "yes":
                        val_cell.fill = yes_fill

        ws_sheet.freeze_panes = "J2"

    return excel_path


def export_well_to_output(
    well: WellExplorationResult,
    well_dfs: Dict[str, pd.DataFrame],
    output_dir: Path,
    repeat_strategy: Literal["keep_all", "merge"] = "keep_all",
    per_curve_repeat_strategy: Optional[Dict[str, Literal["keep_all", "merge"]]] = None,
    update_excel: bool = True,
    field_profile: Optional[FieldProfile] = None,
    save_metadata_cache: bool = True,
    manual_override: bool = False,
    target_grid_step: Optional[float] = None,
    target_grid_start: Optional[float] = None,
    target_grid_stop: Optional[float] = None,
) -> WellExportSummary:
    """
    Ejecuta el flujo completo de exportación para un pozo hacia `output/`:
    1. Normaliza metadatos según `FieldProfile` (o respeta edición manual si `manual_override=True`).
    2. Construye el CSV de curvas en profundidad en `output/wells/<Well>_selected.csv` con `NaN`.
    3. Exporta los archivos en tiempo (`TIME.S` / XPT / MDT) en `output/time_logs/`.
    4. Guarda caché de metadatos JSON en `output/metadata_cache/` (Spec 003 - RF-7).
    5. Actualiza `output/well_metadata.xlsx`.
    """
    raw_meta = dict(well.llm_metadata or {})
    if manual_override:
        norm_meta = raw_meta
        if not norm_meta.get("nombre_pozo"):
            norm_meta["nombre_pozo"] = well.well_folder_name
    else:
        norm_meta = normalize_well_metadata(
            raw_meta,
            profile=field_profile,
            fallback_well_name=well.well_folder_name,
        )
    well.llm_metadata = norm_meta

    well_name = str(norm_meta["nombre_pozo"])
    uwi = str(norm_meta.get("uwi") or "")
    coord_x = norm_meta.get("coord_x") if norm_meta.get("coord_x") is not None else 0
    coord_y = norm_meta.get("coord_y") if norm_meta.get("coord_y") is not None else 0
    elevation = round(float(norm_meta["cota"]), 2) if norm_meta.get("cota") is not None else np.nan
    crs_used = str(norm_meta.get("crs_used") or "")
    coord_note = str(norm_meta.get("coord_resolution_note") or "")

    unified_df, grid_start, grid_stop, grid_step, exported_curves = build_unified_well_depth_df(
        well=well,
        well_dfs=well_dfs,
        normalized_meta=norm_meta,
        repeat_strategy=repeat_strategy,
        per_curve_repeat_strategy=per_curve_repeat_strategy,
        target_grid_step=target_grid_step,
        target_grid_start=target_grid_start,
        target_grid_stop=target_grid_stop,
    )

    wells_dir = output_dir / "wells"
    wells_dir.mkdir(parents=True, exist_ok=True)
    depth_csv_path = wells_dir / f"{well_name}_selected.csv"
    unified_df.to_csv(depth_csv_path, index=False, na_rep="NaN")

    # Identificar qué curvas tienen efectivamente al menos 1 muestra válida en la tabla exportada
    active_curves = [
        c for c in exported_curves if unified_df[c].notna().any()
    ]

    time_csv_paths, station_rows = export_well_time_logs(
        well=well,
        well_dfs=well_dfs,
        normalized_meta=norm_meta,
        output_dir=output_dir,
        field_profile=field_profile,
    )
    if station_rows:
        update_time_stations_summary(station_rows, output_dir)

    if save_metadata_cache:
        cache_dir = output_dir / "metadata_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{well.well_folder_name}_metadata.json"
        cache_payload = {
            "well_folder_name": well.well_folder_name,
            "well_name": well_name,
            "metadata": norm_meta,
            "grid_start": grid_start,
            "grid_stop": grid_stop,
            "grid_step": grid_step,
            "depth_csv": str(depth_csv_path.name),
        }
        cache_file.write_text(json.dumps(cache_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = WellExportSummary(
        well_name=well_name,
        uwi=uwi,
        coord_x=coord_x,
        coord_y=coord_y,
        elevation=elevation,
        grid_start=grid_start,
        grid_stop=grid_stop,
        grid_step=grid_step,
        total_grid_rows=len(unified_df),
        exported_depth_curves=active_curves,
        depth_csv_path=depth_csv_path,
        time_csv_paths=time_csv_paths,
        crs_used=crs_used,
        coord_resolution_note=coord_note,
    )

    if update_excel:
        update_master_excel([summary], output_dir / "well_metadata.xlsx")

    return summary

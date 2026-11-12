"""
Módulo de exploración de archivos .LAS y extracción de encabezados por pozo (Spec 001).
Cumple RF-1, RF-2, RF-3, RF-4 y RF-5.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lasio
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CurveOccurrence:
    """Representa una curva individual dentro de un archivo .LAS."""
    original_mnemonic: str
    assigned_name: str  # Ej. "GR", "GR:2", "M2R6:2"
    unit: str
    description: str
    source_file: str
    index_type: str  # "DEPTH" o "TIME"
    step: float
    declared_start: float
    declared_stop: float
    valid_min_index: Optional[float]
    valid_max_index: Optional[float]
    valid_samples: int
    total_samples: int
    is_repeat_occurrence: bool


@dataclass
class LasFileSummary:
    """Resumen estructurado de un archivo .LAS individual."""
    file_name: str
    file_path: str
    encoding_used: str
    index_type: str  # "DEPTH" o "TIME"
    index_mnemonic: str
    index_unit: str
    declared_start: float
    declared_stop: float
    step: float
    null_value: float
    well_header_dict: Dict[str, str]
    param_header_dict: Dict[str, str]
    raw_header_text: str
    curves: List[CurveOccurrence] = field(default_factory=list)
    stationary_depth_m: Optional[float] = None  # Para registros en TIEMPO (ej. XPT)


@dataclass
class WellExplorationResult:
    """Resultado consolidado de la exploración de una carpeta de pozo."""
    well_folder_name: str
    well_folder_path: str
    las_files_count: int
    ignored_files: List[str]
    depth_files_count: int
    time_files_count: int
    las_summaries: List[LasFileSummary]
    consolidated_header_block: str
    unique_depth_curves: List[str]
    repeated_depth_curves: Dict[str, List[Dict[str, object]]]
    all_assigned_depth_curves: List[str]
    llm_metadata: Optional[Dict[str, object]] = None

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        for s in data["las_summaries"]:
            # Evitar duplicar textos gigantes en el JSON si ya están en consolidated_header_block
            s["raw_header_lines"] = len(s["raw_header_text"].splitlines())
        return data


def read_text_with_fallback(file_path: Path) -> Tuple[str, str]:
    """Lee un archivo .LAS probando utf-8, cp1252 y latin-1 (RF-2)."""
    raw_bytes = file_path.read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("latin-1", errors="replace"), "latin-1-replace"


def extract_raw_header_before_ascii(full_text: str) -> str:
    """
    Extrae únicamente el encabezado del archivo .LAS deteniéndose antes de ~A / ~ASCII (RF-3).
    """
    header_lines: List[str] = []
    for line in full_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("~A"):
            break
        header_lines.append(line)
    return "\n".join(header_lines)


def _safe_float(val: object, default: float = np.nan) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        s = str(val).strip().replace(",", ".")
        try:
            return float(s)
        except (ValueError, TypeError):
            return default


def parse_single_las(
    file_path: Path,
    depth_curve_counts: Dict[str, int],
    time_curve_counts: Dict[str, int],
) -> Tuple[LasFileSummary, pd.DataFrame]:
    """
    Analiza un archivo .LAS individual, separa su encabezado, clasifica DEPTH vs TIME (RF-4)
    y registra sus curvas etiquetando repetidas con :2, :3 (RF-5).
    """
    full_text, encoding_used = read_text_with_fallback(file_path)
    raw_header_text = extract_raw_header_before_ascii(full_text)

    # Cargar con lasio desde el string ya decodificado
    las = lasio.read(full_text)

    # Extraer diccionarios de ~Well y ~Parameter corrigiendo el bug de doble ':' en lasio
    well_dict: Dict[str, str] = {}
    for item in las.well:
        val_str = str(item.value).strip() if item.value is not None else ""
        descr_str = str(item.descr).strip() if item.descr is not None else ""
        if ":" in descr_str:
            extra_val, real_descr = descr_str.rsplit(":", 1)
            val_str = f"{val_str}: {extra_val.strip()}".strip(": ")
            descr_str = real_descr.strip()
        if val_str:
            well_dict[f"{item.mnemonic}.{item.unit}".strip(".")] = f"{val_str} ({descr_str})"

    param_dict: Dict[str, str] = {}
    for item in las.params:
        val_str = str(item.value).strip() if item.value is not None else ""
        descr_str = str(item.descr).strip() if item.descr is not None else ""
        if ":" in descr_str:
            extra_val, real_descr = descr_str.rsplit(":", 1)
            val_str = f"{val_str}: {extra_val.strip()}".strip(": ")
            descr_str = real_descr.strip()
        if val_str:
            param_dict[f"{item.mnemonic}.{item.unit}".strip(".")] = f"{val_str} ({descr_str})"

    declared_start = _safe_float(las.well.STRT.value if "STRT" in las.well else np.nan)
    declared_stop = _safe_float(las.well.STOP.value if "STOP" in las.well else np.nan)
    step = _safe_float(las.well.STEP.value if "STEP" in las.well else 0.0, default=0.0)
    null_val = _safe_float(las.well.NULL.value if "NULL" in las.well else -999.25, default=-999.25)
    strt_unit = (las.well.STRT.unit if "STRT" in las.well else "").strip().upper()

    first_curve = las.curves[0] if len(las.curves) > 0 else None
    index_mnemonic = first_curve.mnemonic.strip().upper() if first_curve else "UNKNOWN"
    index_unit = first_curve.unit.strip().upper() if first_curve else strt_unit

    # Clasificación RF-4: TIME vs DEPTH
    is_time_log = (
        index_mnemonic in ("TIME", "ETIM", "ETIM_XPT")
        or index_unit in ("S", "SEC", "MS", "MSEC", "MIN")
        or strt_unit in ("S", "SEC", "MS", "MSEC")
    )
    index_type = "TIME" if is_time_log else "DEPTH"

    # Construir DataFrame de datos enmascarando el valor NULL
    df = las.df().reset_index()
    if not np.isnan(null_val):
        df = df.replace(null_val, np.nan)
        # También enmascarar variantes de redondeo como -999.25
        for col in df.select_dtypes(include=[np.number]).columns:
            df.loc[np.isclose(df[col], null_val, atol=1e-2, equal_nan=False), col] = np.nan

    index_col = df.columns[0]
    index_series = df[index_col]

    # Si es un registro en TIEMPO (ej. XPT), rescatar la profundidad estacionaria de sonda en metros
    stationary_depth_m: Optional[float] = None
    if index_type == "TIME":
        if "PDEP_XPT" in df.columns and df["PDEP_XPT"].notna().any():
            stationary_depth_m = round(float(df["PDEP_XPT"].median()), 2)
        elif "DEPT_SL" in df.columns and df["DEPT_SL"].notna().any():
            # DEPT_SL suele venir en .1IN (décimas de pulgada)
            stationary_depth_m = round(float(df["DEPT_SL"].median()) * 0.1 * 0.0254, 2)
        elif "TDEP" in df.columns and df["TDEP"].notna().any():
            stationary_depth_m = round(float(df["TDEP"].median()) * 0.1 * 0.0254, 2)

    curve_occurrences: List[CurveOccurrence] = []
    # lasio puede renombrar duplicados internos como M2R6:1, M2R6:2; usamos original_mnemonic
    for idx, c in enumerate(las.curves):
        if idx == 0:
            # Saltamos la curva índice (DEPT o TIME:1)
            continue

        orig_mnem = c.original_mnemonic.strip().upper() if hasattr(c, "original_mnemonic") and c.original_mnemonic else c.mnemonic.strip().upper()
        # Limpiar sufijos automáticos de lasio si existieran
        if ":" in orig_mnem:
            orig_mnem = orig_mnem.split(":")[0].strip()

        counter_dict = time_curve_counts if index_type == "TIME" else depth_curve_counts
        count = counter_dict.get(orig_mnem, 0) + 1
        counter_dict[orig_mnem] = count

        assigned_name = orig_mnem if count == 1 else f"{orig_mnem}:{count}"
        is_repeat = count > 1

        # Serie de datos de esta columna (por posición idx en df)
        col_series = df.iloc[:, idx] if idx < df.shape[1] else pd.Series(dtype=float)
        valid_mask = col_series.notna() & index_series.notna()
        valid_samples = int(valid_mask.sum())
        total_samples = int(len(col_series))

        if valid_samples > 0:
            valid_min = round(float(index_series[valid_mask].min()), 4)
            valid_max = round(float(index_series[valid_mask].max()), 4)
        else:
            valid_min = None
            valid_max = None

        curve_occurrences.append(
            CurveOccurrence(
                original_mnemonic=orig_mnem,
                assigned_name=assigned_name,
                unit=c.unit.strip(),
                description=c.descr.strip(),
                source_file=file_path.name,
                index_type=index_type,
                step=step,
                declared_start=declared_start,
                declared_stop=declared_stop,
                valid_min_index=valid_min,
                valid_max_index=valid_max,
                valid_samples=valid_samples,
                total_samples=total_samples,
                is_repeat_occurrence=is_repeat,
            )
        )

    summary = LasFileSummary(
        file_name=file_path.name,
        file_path=str(file_path),
        encoding_used=encoding_used,
        index_type=index_type,
        index_mnemonic=index_mnemonic,
        index_unit=index_unit,
        declared_start=declared_start,
        declared_stop=declared_stop,
        step=step,
        null_value=null_val,
        well_header_dict=well_dict,
        param_header_dict=param_dict,
        raw_header_text=raw_header_text,
        curves=curve_occurrences,
        stationary_depth_m=stationary_depth_m,
    )
    return summary, df


def build_consolidated_header_block(
    well_folder_name: str,
    summaries: List[LasFileSummary],
) -> str:
    """
    Construye un bloque de texto estructurado y compacto con los encabezados de un pozo
    para enviar al LLM (RF-3), evitando repetir 15 encabezados idénticos de estaciones XPT.
    """
    lines: List[str] = [
        f"=== POZO (CARPETA): {well_folder_name} ===",
        f"Total archivos .LAS: {len(summaries)}",
        "",
    ]

    seen_time_header = False
    xpt_station_depths: List[str] = []

    for s in summaries:
        if s.index_type == "TIME":
            if seen_time_header:
                if s.stationary_depth_m is not None:
                    xpt_station_depths.append(f"{s.file_name} (@ {s.stationary_depth_m} m)")
                else:
                    xpt_station_depths.append(s.file_name)
                continue
            seen_time_header = True

        lines.append(f"--- ARCHIVO: {s.file_name} [Índice: {s.index_type} ({s.index_mnemonic}.{s.index_unit})] ---")
        lines.append("[~WELL INFORMATION]")
        for k, v in s.well_header_dict.items():
            lines.append(f"  {k}: {v}")
        lines.append("[~PARAMETER INFORMATION (Seleccionados)]")
        # Incluir todos los parámetros relevantes (coordenadas, cotas, elevaciones, identificadores)
        for k, v in s.param_header_dict.items():
            k_up = k.upper()
            if any(
                tag in k_up
                for tag in ("WN", "WELL", "UWI", "API", "LAT", "LON", "LOC", "FL1", "FL2", "EPD", "EGL", "EDF", "APD", "PDAT", "LMF", "DMF", "TD", "FLD", "FN", "CN")
            ):
                lines.append(f"  {k}: {v}")
        curve_names = ", ".join(c.assigned_name for c in s.curves)
        lines.append(f"[~CURVES ({len(s.curves)})]: {curve_names}")
        lines.append("")

    if xpt_station_depths:
        lines.append(
            f"--- ESTACIONES ADICIONALES EN TIEMPO (XPT) CON MISMO ENCABEZADO ({len(xpt_station_depths)}) ---"
        )
        lines.append("  " + ", ".join(xpt_station_depths))
        lines.append("")

    return "\n".join(lines)


def explore_well_directory(well_dir: Path) -> Tuple[WellExplorationResult, Dict[str, pd.DataFrame]]:
    """
    Escanea una carpeta de pozo, procesa todos sus .las/.LAS (RF-1) y devuelve el
    resultado de exploración junto con los DataFrames en memoria para validación visual.
    """
    las_files: List[Path] = []
    ignored_files: List[str] = []

    for item in sorted(well_dir.iterdir()):
        if item.is_file():
            if item.suffix.lower() == ".las":
                las_files.append(item)
            else:
                ignored_files.append(item.name)

    depth_curve_counts: Dict[str, int] = {}
    time_curve_counts: Dict[str, int] = {}
    summaries: List[LasFileSummary] = []
    dataframes: Dict[str, pd.DataFrame] = {}

    for lf in las_files:
        summary, df = parse_single_las(lf, depth_curve_counts, time_curve_counts)
        summaries.append(summary)
        dataframes[lf.name] = df

    depth_files_count = sum(1 for s in summaries if s.index_type == "DEPTH")
    time_files_count = sum(1 for s in summaries if s.index_type == "TIME")

    # Recopilar curvas en profundidad únicas, todas las asignadas (con :2, :3) y las repetidas
    unique_depth_curves = sorted(depth_curve_counts.keys())
    all_assigned_depth_curves: List[str] = []
    by_base_mnemonic: Dict[str, List[Dict[str, object]]] = {}

    for s in summaries:
        if s.index_type != "DEPTH":
            continue
        for c in s.curves:
            all_assigned_depth_curves.append(c.assigned_name)
            by_base_mnemonic.setdefault(c.original_mnemonic, []).append(
                {
                    "assigned_name": c.assigned_name,
                    "source_file": c.source_file,
                    "unit": c.unit,
                    "step": c.step,
                    "valid_min_depth": c.valid_min_index,
                    "valid_max_depth": c.valid_max_index,
                    "valid_samples": c.valid_samples,
                }
            )

    repeated_depth_curves = {
        mnem: occs for mnem, occs in sorted(by_base_mnemonic.items()) if len(occs) > 1
    }

    consolidated_block = build_consolidated_header_block(well_dir.name, summaries)

    result = WellExplorationResult(
        well_folder_name=well_dir.name,
        well_folder_path=str(well_dir),
        las_files_count=len(las_files),
        ignored_files=ignored_files,
        depth_files_count=depth_files_count,
        time_files_count=time_files_count,
        las_summaries=summaries,
        consolidated_header_block=consolidated_block,
        unique_depth_curves=unique_depth_curves,
        repeated_depth_curves=repeated_depth_curves,
        all_assigned_depth_curves=all_assigned_depth_curves,
    )
    return result, dataframes


def explore_all_wells(input_root: Path) -> Tuple[List[WellExplorationResult], Dict[str, Dict[str, pd.DataFrame]]]:
    """Escanea todas las carpetas de pozos dentro de Input/."""
    results: List[WellExplorationResult] = []
    all_dfs: Dict[str, Dict[str, pd.DataFrame]] = {}

    for sub in sorted(input_root.iterdir()):
        if sub.is_dir():
            res, dfs = explore_well_directory(sub)
            results.append(res)
            all_dfs[sub.name] = dfs

    return results, all_dfs

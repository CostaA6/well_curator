"""
Módulo de exploración de archivos .LAS y extracción pura de encabezados por pozo (Spec 001).
- Python puro corta el texto del encabezado antes de ~A con triple barrera de seguridad (RF-3).
- lasio se utiliza exclusivamente para leer la matriz numérica de curvas (las.df() y las.curves) (RF-4, RF-5).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lasio
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CurveOccurrence:
    """Representa una curva individual medida dentro de la matriz de datos de un archivo .LAS."""
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
    """Resumen de un archivo .LAS: encabezado crudo extraído por Python + curvas leídas con lasio."""
    file_name: str
    file_path: str
    encoding_used: str
    header_stop_reason: str  # "SECTION_ASCII", "NUMERIC_DATA_ROW", "MAX_LINES_LIMIT" o "EOF"
    index_type: str  # "DEPTH" o "TIME"
    index_mnemonic: str
    index_unit: str
    declared_start: float
    declared_stop: float
    step: float
    null_value: float
    raw_header_text: str
    curves: List[CurveOccurrence] = field(default_factory=list)
    stationary_depth_m: Optional[float] = None  # Para registros en TIEMPO (ej. XPT)
    original_index_unit: str = ""
    depth_unit_scale: float = 1.0


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


def _is_pure_numeric_data_row(stripped_line: str, min_numbers: int = 3) -> bool:
    """
    Detecta si una línea sin ':' ni '~' ni '#' es una fila de mediciones numéricas
    (ej. '228.2000  -999.2500  -999.2500'), como segunda barrera por si falta '~A'.
    """
    if not stripped_line or ":" in stripped_line or stripped_line.startswith(("#", "~")):
        return False
    tokens = stripped_line.split()
    if len(tokens) < min_numbers:
        return False
    for tok in tokens:
        try:
            float(tok)
        except ValueError:
            return False
    return True


def extract_raw_header_before_ascii(
    full_text: str,
    max_lines: int = 500,
) -> Tuple[str, str]:
    """
    Extrae únicamente el texto crudo del encabezado del archivo .LAS usando Python puro
    con triple barrera de seguridad (RF-3), sin tocarlo con lasio:
      1. Barrera 1 (Estándar LAS 1.2/2.0/3.0): línea que comienza con ~A, ~ A, ~ASCII o ~LOG_DATA.
      2. Barrera 2 (Detector de fila numérica): tras pasar ~Curve, si aparece una fila de >=3 números
         flotantes sin ':', corta antes de esa fila.
      3. Barrera 3 (Tope de líneas): máximo `max_lines` líneas por encabezado.
    Retorna (raw_header_text, stop_reason).
    """
    header_lines: List[str] = []
    seen_curve_section = False
    stop_reason = "EOF"

    for line in full_text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()

        # Barrera 1: Sección ~A / ~ASCII / ~LOG_DATA
        if upper.startswith("~"):
            after_tilde = upper[1:].lstrip()
            if after_tilde.startswith(("A", "LOG_DATA")):
                stop_reason = "SECTION_ASCII"
                break
            if after_tilde.startswith("C"):
                seen_curve_section = True

        # Barrera 2: Fila de datos numéricos sin ~A luego de ~Curve
        if seen_curve_section and _is_pure_numeric_data_row(stripped):
            stop_reason = "NUMERIC_DATA_ROW"
            break

        # Barrera 3: Límite máximo de líneas de seguridad
        if len(header_lines) >= max_lines:
            stop_reason = "MAX_LINES_LIMIT"
            break

        header_lines.append(line)

    return "\n".join(header_lines), stop_reason


def _compact_raw_header_for_llm(raw_header_text: str) -> str:
    """
    Mantiene el texto original del encabezado tal cual fue escrito en el archivo .LAS
    (sin usar lasio), omitiendo únicamente líneas decorativas de guiones (#-----)
    y líneas de ~Parameter cuyo campo de valor está completamente vacío (ej. R14 . :Remark Line 14),
    para ahorrar tokens en pozos con muchos .las sin perder información real.
    """
    cleaned: List[str] = []
    current_section = ""
    empty_param_re = re.compile(r"^[A-Za-z0-9_]+\s*\.[^\s:]*\s*:")

    for line in raw_header_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("~"):
            current_section = s[1:].lstrip().upper()[:1]
            cleaned.append(line.rstrip())
            continue
        # Ignorar separadores decorativos #----
        if s.startswith("#") and set(s[1:].strip()) <= {"-", "=", " ", "*"}:
            continue
        # En ~Parameter / ~Other, omitir líneas sin valor antes de ':' (ej. "R14 . :Remark Line 14")
        if current_section in ("P", "O") and not s.startswith("#") and ":" in s and "." in s:
            if empty_param_re.match(s):
                continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned)


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


FEET_TO_METERS = 0.3048
FEET_UNITS = {"F", "FT", "FEET", "FOOT"}


def parse_single_las(
    file_path: Path,
    depth_curve_counts: Dict[str, int],
    time_curve_counts: Dict[str, int],
    target_depth_unit: str = "M",
) -> Tuple[LasFileSummary, pd.DataFrame]:
    """
    1. Corta el encabezado crudo en Python puro con triple barrera (RF-3).
    2. Usa `lasio` exclusivamente para leer las curvas y la tabla numérica ~A (RF-4, RF-5).
    3. Si un archivo DEPTH viene indexado en pies (FT/FEET/F) y target_depth_unit=='M',
       convierte índice y rangos a metros (* 0.3048) (Spec 003 - RF-5).
    """
    full_text, encoding_used = read_text_with_fallback(file_path)
    raw_header_text, stop_reason = extract_raw_header_before_ascii(full_text)

    # Cargar con lasio para extraer exclusivamente las curvas y el DataFrame numérico
    las = lasio.read(full_text)

    declared_start = _safe_float(las.well.STRT.value if "STRT" in las.well else np.nan)
    declared_stop = _safe_float(las.well.STOP.value if "STOP" in las.well else np.nan)
    step = _safe_float(las.well.STEP.value if "STEP" in las.well else 0.0, default=0.0)
    null_val = _safe_float(las.well.NULL.value if "NULL" in las.well else -999.25, default=-999.25)
    strt_unit = (las.well.STRT.unit if "STRT" in las.well else "").strip().upper()

    first_curve = las.curves[0] if len(las.curves) > 0 else None
    index_mnemonic = (
        first_curve.original_mnemonic.strip().upper()
        if first_curve and getattr(first_curve, "original_mnemonic", None)
        else (first_curve.mnemonic.strip().upper() if first_curve else "UNKNOWN")
    )
    original_index_unit = (first_curve.unit.strip().upper() if first_curve else strt_unit) or strt_unit
    index_unit = original_index_unit

    # Clasificación RF-4: TIME vs DEPTH a partir de la curva índice
    is_time_log = (
        index_mnemonic in ("TIME", "ETIM", "ETIM_XPT")
        or index_unit in ("S", "SEC", "MS", "MSEC", "MIN")
        or strt_unit in ("S", "SEC", "MS", "MSEC")
    )
    index_type = "TIME" if is_time_log else "DEPTH"

    # Construir DataFrame de datos numéricos enmascarando el valor NULL
    df = las.df().reset_index()
    if not np.isnan(null_val):
        df = df.replace(null_val, np.nan)
        for col in df.select_dtypes(include=[np.number]).columns:
            df.loc[np.isclose(df[col], null_val, atol=1e-2, equal_nan=False), col] = np.nan

    index_col = df.columns[0]

    # Conversión automática FT -> M si el archivo de profundidad viene en pies (Spec 003 - RF-5)
    depth_unit_scale = 1.0
    if index_type == "DEPTH" and target_depth_unit.upper() == "M" and original_index_unit in FEET_UNITS:
        depth_unit_scale = FEET_TO_METERS
        df[index_col] = pd.to_numeric(df[index_col], errors="coerce") * depth_unit_scale
        if not np.isnan(declared_start):
            declared_start = round(declared_start * depth_unit_scale, 4)
        if not np.isnan(declared_stop):
            declared_stop = round(declared_stop * depth_unit_scale, 4)
        if step > 0:
            step = round(step * depth_unit_scale, 4)
        index_unit = "M"

    index_series = df[index_col]

    # Si es un registro en TIEMPO (ej. XPT), rescatar la profundidad estacionaria de sonda en metros
    stationary_depth_m: Optional[float] = None
    if index_type == "TIME":
        if "PDEP_XPT" in df.columns and df["PDEP_XPT"].notna().any():
            stationary_depth_m = round(float(df["PDEP_XPT"].median()), 2)
        elif "DEPT_SL" in df.columns and df["DEPT_SL"].notna().any():
            stationary_depth_m = round(float(df["DEPT_SL"].median()) * 0.1 * 0.0254, 2)
        elif "TDEP" in df.columns and df["TDEP"].notna().any():
            stationary_depth_m = round(float(df["TDEP"].median()) * 0.1 * 0.0254, 2)

    curve_occurrences: List[CurveOccurrence] = []
    for idx, c in enumerate(las.curves):
        if idx == 0:
            continue

        orig_mnem = (
            c.original_mnemonic.strip().upper()
            if hasattr(c, "original_mnemonic") and c.original_mnemonic
            else c.mnemonic.strip().upper()
        )
        if ":" in orig_mnem:
            orig_mnem = orig_mnem.split(":")[0].strip()

        counter_dict = time_curve_counts if index_type == "TIME" else depth_curve_counts
        count = counter_dict.get(orig_mnem, 0) + 1
        counter_dict[orig_mnem] = count

        assigned_name = orig_mnem if count == 1 else f"{orig_mnem}:{count}"
        is_repeat = count > 1

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
        header_stop_reason=stop_reason,
        index_type=index_type,
        index_mnemonic=index_mnemonic,
        index_unit=index_unit,
        declared_start=declared_start,
        declared_stop=declared_stop,
        step=step,
        null_value=null_val,
        raw_header_text=raw_header_text,
        curves=curve_occurrences,
        stationary_depth_m=stationary_depth_m,
        original_index_unit=original_index_unit,
        depth_unit_scale=depth_unit_scale,
    )
    return summary, df


def build_consolidated_header_block(
    well_folder_name: str,
    summaries: List[LasFileSummary],
) -> str:
    """
    Construye el bloque de encabezados del pozo concatenando DIRECTAMENTE el texto crudo
    extraído por Python antes de ~A (sin pasar por lasio), para que el LLM lea tanto los
    metadatos (~Well, ~Parameter) como las curvas (~Curve) tal como vienen en el archivo.
    """
    lines: List[str] = [
        f"=== POZO (CARPETA): {well_folder_name} ===",
        f"Total archivos .LAS en carpeta: {len(summaries)}",
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

        lines.append(
            f"================ ARCHIVO: {s.file_name} (Tipo de registro: {s.index_type}) ================"
        )
        lines.append(_compact_raw_header_for_llm(s.raw_header_text))
        lines.append("")

    if xpt_station_depths:
        lines.append(
            f"=== ARCHIVOS ADICIONALES EN TIEMPO (XPT) CON EL MISMO ENCABEZADO ({len(xpt_station_depths)}) ==="
        )
        lines.append("  " + ", ".join(xpt_station_depths))
        lines.append("")

    return "\n".join(lines)


def explore_well_directory(
    well_dir: Path,
    target_depth_unit: str = "M",
) -> Tuple[WellExplorationResult, Dict[str, pd.DataFrame]]:
    """
    Escanea una carpeta de pozo, extrae los encabezados puros y lee las curvas numéricas con lasio.
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
        summary, df = parse_single_las(
            lf,
            depth_curve_counts,
            time_curve_counts,
            target_depth_unit=target_depth_unit,
        )
        summaries.append(summary)
        dataframes[lf.name] = df

    depth_files_count = sum(1 for s in summaries if s.index_type == "DEPTH")
    time_files_count = sum(1 for s in summaries if s.index_type == "TIME")

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


def explore_all_wells(
    input_root: Path,
    target_depth_unit: str = "M",
) -> Tuple[List[WellExplorationResult], Dict[str, Dict[str, pd.DataFrame]]]:
    """Escanea todas las carpetas de pozos dentro de Input/."""
    results: List[WellExplorationResult] = []
    all_dfs: Dict[str, Dict[str, pd.DataFrame]] = {}

    for sub in sorted(input_root.iterdir()):
        if sub.is_dir():
            res, dfs = explore_well_directory(sub, target_depth_unit=target_depth_unit)
            results.append(res)
            all_dfs[sub.name] = dfs

    return results, all_dfs

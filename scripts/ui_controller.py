"""
Controlador lógico desacoplado de la interfaz gráfica PySide6 (Spec 004):
- Staging seguro de Drag & Drop (carpetas, archivos .las/.LAS sueltos, .zip) hacia `workspace_imports/<Pozo>/`
  sin modificar jamás `Input/`.
- Descubrimiento y pre-escaneo rápido de pozos sin LLM.
- Reproyección inversa `EPSG -> WGS84` y generación del mapa geográfico interactivo con Folium.
- Preparación de series Crudo .LAS vs CSV Grilla Común para el visor multi-track (hasta 6 tracks).
- Lectura de inventarios y resultados en `output/`.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import folium
import pandas as pd
import pyproj

from scripts.las_explorer import WellExplorationResult

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_IMPORTS_DIR = PROJECT_ROOT / "workspace_imports"


def sanitize_folder_name(name: str) -> str:
    """Limpia un nombre de pozo para usarlo de forma segura como nombre de carpeta."""
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", name.strip())
    return cleaned.strip(". ") or "POZO_NUEVO"


def suggest_well_name_from_paths(paths: Sequence[Path]) -> str:
    """Sugiere un nombre de pozo legible a partir de las rutas soltadas (carpeta, .zip o .las)."""
    if not paths:
        return "POZO_NUEVO"
    first = Path(paths[0])
    if first.is_dir():
        return sanitize_folder_name(first.name)
    if first.suffix.lower() == ".zip":
        return sanitize_folder_name(first.stem)
    # Si son archivos .las sueltos, intentar usar el nombre de la carpeta padre si no es genérica
    parent_name = first.parent.name
    if parent_name and parent_name.lower() not in ("desktop", "downloads", "descargas", "tmp", "temp", "input"):
        return sanitize_folder_name(parent_name)
    return sanitize_folder_name(first.stem)


def stage_well_drop_items(
    paths: Sequence[Path],
    target_well_name: str = "",
    workspace_root: Path = WORKSPACE_IMPORTS_DIR,
) -> Tuple[Path, List[str]]:
    """
    Copia archivos `.las`/`.LAS` desde carpetas, archivos sueltos o `.zip` hacia
    `workspace_imports/<Pozo>/` sin modificar ni eliminar los archivos originales (Spec 004 - RF-2).

    Retorna `(carpeta_destino, lista_archivos_las_importados)`.
    """
    if not paths:
        raise ValueError("No se recibieron rutas de archivos o carpetas para importar.")

    resolved_name = sanitize_folder_name(target_well_name) if target_well_name.strip() else suggest_well_name_from_paths(paths)
    dest_dir = workspace_root / resolved_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    imported_files: List[str] = []

    for item in paths:
        src = Path(item)
        if not src.exists():
            continue

        if src.is_dir():
            for child in sorted(src.rglob("*")):
                if child.is_file() and child.suffix.lower() == ".las":
                    target_file = dest_dir / child.name
                    shutil.copy2(child, target_file)
                    imported_files.append(child.name)

        elif src.is_file() and src.suffix.lower() == ".las":
            target_file = dest_dir / src.name
            shutil.copy2(src, target_file)
            imported_files.append(src.name)

        elif src.is_file() and src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src, "r") as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    member_path = Path(member.filename)
                    # Protección contra ZipSlip y filtrado estricto a .las/.LAS
                    if member_path.is_absolute() or ".." in member_path.parts:
                        logger.warning("Entrada insegura ignorada en ZIP: %s", member.filename)
                        continue
                    if member_path.suffix.lower() != ".las":
                        continue
                    safe_name = member_path.name
                    target_file = dest_dir / safe_name
                    with zf.open(member, "r") as z_in, open(target_file, "wb") as f_out:
                        shutil.copyfileobj(z_in, f_out)
                    imported_files.append(safe_name)

    if not imported_files:
        raise ValueError("No se encontraron archivos .las/.LAS válidos en los elementos seleccionados.")

    return dest_dir, sorted(set(imported_files))


def discover_well_sources(roots: Sequence[Tuple[str, Path]]) -> List[Dict[str, object]]:
    """
    Lista las carpetas de pozos presentes en los directorios configurados
    (ej. `[('Input', Path('Input')), ('Importados UI', Path('workspace_imports'))]`).
    """
    discovered: List[Dict[str, object]] = []
    seen_paths = set()

    for source_label, root_dir in roots:
        if not root_dir.exists() or not root_dir.is_dir():
            continue
        # Si la carpeta elegida contiene directamente .las en su raíz, tratarla como pozo único
        direct_las = [f for f in root_dir.iterdir() if f.is_file() and f.suffix.lower() == ".las"]
        if direct_las:
            rp = root_dir.resolve()
            if rp not in seen_paths:
                seen_paths.add(rp)
                discovered.append({
                    "well_folder_name": root_dir.name,
                    "folder_path": root_dir,
                    "source": source_label,
                    "las_count": len(direct_las),
                })

        for sub in sorted(root_dir.iterdir()):
            if not sub.is_dir():
                continue
            las_in_sub = [f for f in sub.iterdir() if f.is_file() and f.suffix.lower() == ".las"]
            if not las_in_sub:
                continue
            rp = sub.resolve()
            if rp not in seen_paths:
                seen_paths.add(rp)
                discovered.append({
                    "well_folder_name": sub.name,
                    "folder_path": sub,
                    "source": source_label,
                    "las_count": len(las_in_sub),
                })

    return discovered


def inverse_project_to_wgs84(
    coord_x: object,
    coord_y: object,
    crs_used: str,
) -> Optional[Tuple[float, float]]:
    """
    Reproyecta `(X, Y)` desde el CRS curado (`EPSG:5343..5349`, `EPSG:326xx/327xx`, etc.)
    hacia coordenadas geográficas `(lat, lon)` WGS84 (`EPSG:4326`) para el mapa Folium (Spec 004 - RF-5).
    """
    try:
        x_val = float(coord_x)
        y_val = float(coord_y)
    except (TypeError, ValueError):
        return None

    if -180.0 <= x_val <= 180.0 and -90.0 <= y_val <= 90.0:
        return y_val, x_val

    epsg_code = str(crs_used or "").strip().upper()
    if not epsg_code.startswith("EPSG:"):
        # Inferencia de seguridad si se trata de Gauss-Krüger Argentina Faja 1..7
        if 1_150_000.0 <= x_val <= 7_850_000.0 and 3_700_000.0 <= y_val <= 7_750_000.0:
            faja = int(x_val // 1_000_000)
            if 1 <= faja <= 7:
                epsg_code = f"EPSG:{5342 + faja}"
        if not epsg_code.startswith("EPSG:"):
            return None

    try:
        transformer = pyproj.Transformer.from_crs(epsg_code, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(x_val, y_val)
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return round(float(lat), 6), round(float(lon), 6)
    except Exception as exc:
        logger.warning("No se pudo reproyectar (%s, %s) desde %s a WGS84: %s", x_val, y_val, epsg_code, exc)
    return None


def build_folium_well_map_html(
    wells_records: Sequence[Dict[str, object]],
    output_html_path: Optional[Path] = None,
) -> str:
    """
    Genera un mapa geográfico interactivo HTML (OpenStreetMap + Satélite ESRI) usando Folium,
    mostrando la ubicación exacta de los pozos reproyectados a WGS84 y sus metadatos curados.
    """
    plotted_points: List[Tuple[float, float, Dict[str, object]]] = []
    for rec in wells_records:
        coords = inverse_project_to_wgs84(
            rec.get("coord_x"),
            rec.get("coord_y"),
            str(rec.get("crs_used") or ""),
        )
        if coords is not None:
            plotted_points.append((coords[0], coords[1], rec))

    if plotted_points:
        center_lat = sum(p[0] for p in plotted_points) / len(plotted_points)
        center_lon = sum(p[1] for p in plotted_points) / len(plotted_points)
        zoom = 13
    else:
        center_lat, center_lon, zoom = -45.8, -67.5, 5

    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, tiles="OpenStreetMap")
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satélite (Esri)",
        overlay=False,
        control=True,
    ).add_to(fmap)

    for lat, lon, rec in plotted_points:
        well_name = str(rec.get("well_name") or rec.get("nombre_pozo") or "")
        orig_folder = str(rec.get("folder") or rec.get("well_folder_name") or well_name)
        uwi = str(rec.get("uwi") or "S/D")
        crs_used = str(rec.get("crs_used") or "S/D")
        note = str(rec.get("coord_note") or rec.get("coord_resolution_note") or "")
        is_projected = "Proyectado" in note
        color = "#dc2626" if is_projected else "#2563eb"

        popup_html = (
            f"<div style='font-family:Segoe UI,sans-serif;font-size:12px;min-width:210px'>"
            f"<b>Pozo:</b> {well_name}<br>"
            f"<b>Origen:</b> {orig_folder}<br>"
            f"<b>UWI:</b> {uwi}<br>"
            f"<b>X / Y:</b> {rec.get('coord_x')} / {rec.get('coord_y')}<br>"
            f"<b>CRS:</b> {crs_used}<br>"
            f"<b>Lat / Lon:</b> {lat:.5f}, {lon:.5f}<br>"
            f"<b>Step:</b> {rec.get('grid_step', 'S/D')} m | <b>Curvas:</b> {rec.get('curves_count', 'S/D')}"
            f"</div>"
        )
        folium.CircleMarker(
            location=[lat, lon],
            radius=8,
            color="#111827",
            weight=1.5,
            fill=True,
            fill_color=color,
            fill_opacity=0.88,
            tooltip=f"{well_name} ({uwi}) [{crs_used}]",
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(fmap)

    if len(plotted_points) >= 2:
        lats = [p[0] for p in plotted_points]
        lons = [p[1] for p in plotted_points]
        fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(30, 30))

    folium.LayerControl().add_to(fmap)
    html_str = fmap.get_root().render()
    if output_html_path is not None:
        output_html_path.parent.mkdir(parents=True, exist_ok=True)
        output_html_path.write_text(html_str, encoding="utf-8")
    return html_str


def prepare_well_log_tracks(
    well_result: WellExplorationResult,
    well_dfs: Dict[str, pd.DataFrame],
    unified_csv_path: Optional[Path],
    selected_curves: Sequence[str],
    max_tracks: int = 6,
) -> List[Dict[str, object]]:
    """
    Prepara hasta `max_tracks` tracks de curvas para graficar en la UI, comparando
    las ocurrencias originales en `.LAS` (incluyendo `:2`, `:3`) frente a la grilla común CSV.
    """
    raw_by_assigned: Dict[str, Tuple[pd.Series, pd.Series, str, str]] = {}
    for s in well_result.las_summaries:
        if s.index_type != "DEPTH":
            continue
        df = well_dfs[s.file_name]
        d_s = df.iloc[:, 0]
        for idx, c in enumerate(s.curves, start=1):
            if idx < df.shape[1]:
                v_s = df.iloc[:, idx]
                if v_s.notna().any():
                    raw_by_assigned[c.assigned_name] = (d_s, v_s, s.file_name, c.unit)

    uni_df = pd.read_csv(unified_csv_path) if (unified_csv_path and unified_csv_path.exists()) else None
    tracks: List[Dict[str, object]] = []

    for cname in list(selected_curves)[:max_tracks]:
        raw_series_list: List[Dict[str, object]] = []
        if cname in raw_by_assigned:
            d_s, v_s, src, unit = raw_by_assigned[cname]
            valid = d_s.notna() & v_s.notna()
            raw_series_list.append({
                "label": f"{cname} ({src})",
                "depth": d_s[valid].to_numpy(dtype=float),
                "values": v_s[valid].to_numpy(dtype=float),
                "unit": unit,
            })
        else:
            # Si viene fusionada (ej. M2R6 en estrategia 'merge'), juntar todas sus ocurrencias base
            for assigned, (d_s, v_s, src, unit) in raw_by_assigned.items():
                if assigned == cname or assigned.startswith(f"{cname}:"):
                    valid = d_s.notna() & v_s.notna()
                    raw_series_list.append({
                        "label": f"{assigned} ({src})",
                        "depth": d_s[valid].to_numpy(dtype=float),
                        "values": v_s[valid].to_numpy(dtype=float),
                        "unit": unit,
                    })

        csv_depth = None
        csv_vals = None
        if uni_df is not None and cname in uni_df.columns:
            valid_u = uni_df[cname].notna() & uni_df["DEPTH"].notna()
            csv_depth = uni_df.loc[valid_u, "DEPTH"].to_numpy(dtype=float)
            csv_vals = uni_df.loc[valid_u, cname].to_numpy(dtype=float)

        unit_label = raw_series_list[0]["unit"] if raw_series_list else ""
        tracks.append({
            "curve_name": cname,
            "unit": unit_label,
            "raw_series": raw_series_list,
            "csv_depth": csv_depth,
            "csv_values": csv_vals,
        })

    return tracks


def load_output_inventory(output_dir: Path) -> Dict[str, object]:
    """Lee un inventario actualizado de todos los artefactos presentes en `output/`."""
    excel_path = output_dir / "well_metadata.xlsx"
    wells_dir = output_dir / "wells"
    time_summary_path = output_dir / "time_logs" / "xpt_stations_summary.csv"
    errors_log_path = output_dir / "batch_errors.log"
    cache_dir = output_dir / "metadata_cache"

    excel_df = pd.read_excel(excel_path) if excel_path.exists() else pd.DataFrame()
    well_csvs = sorted(wells_dir.glob("*.csv")) if wells_dir.exists() else []
    time_summary_df = pd.read_csv(time_summary_path) if time_summary_path.exists() else pd.DataFrame()
    errors_text = errors_log_path.read_text(encoding="utf-8", errors="replace") if errors_log_path.exists() else ""

    cache_records: List[Dict[str, object]] = []
    if cache_dir.exists():
        for cf in sorted(cache_dir.glob("*_metadata.json")):
            try:
                cache_records.append(json.loads(cf.read_text(encoding="utf-8")))
            except Exception:
                continue

    return {
        "excel_path": excel_path,
        "excel_df": excel_df,
        "well_csvs": well_csvs,
        "time_summary_path": time_summary_path,
        "time_summary_df": time_summary_df,
        "errors_text": errors_text,
        "cache_records": cache_records,
    }

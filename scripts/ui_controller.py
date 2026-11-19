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
import tempfile
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import folium
import pandas as pd
import pyproj
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_crs_info
from pyproj.enums import PJType

from scripts.las_explorer import WellExplorationResult, group_las_files_by_well

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
            direct_las = [c for c in sorted(src.iterdir()) if c.is_file() and c.suffix.lower() == ".las"]
            if direct_las:
                for child in direct_las:
                    shutil.copy2(child, dest_dir / child.name)
                    imported_files.append(child.name)
            else:
                # Si la carpeta soltada contiene subcarpetas de pozos (un lote entero), advertir para no mezclarlos
                sub_las_dirs = [
                    d for d in sorted(src.iterdir())
                    if d.is_dir() and any(f.is_file() and f.suffix.lower() == ".las" for f in d.iterdir())
                ]
                if len(sub_las_dirs) > 1:
                    raise ValueError(
                        f"La carpeta '{src.name}' contiene {len(sub_las_dirs)} subcarpetas de pozos distintos. "
                        "Usá 'Añadir Carpeta Batch...' en el panel derecho para no mezclarlos en un solo pozo."
                    )
                for child in sorted(src.rglob("*")):
                    if child.is_file() and child.suffix.lower() == ".las":
                        shutil.copy2(child, dest_dir / child.name)
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


def stage_grouped_well_drop_items(
    paths: Sequence[Path],
    target_well_name: str = "",
    workspace_root: Path = WORKSPACE_IMPORTS_DIR,
) -> List[Tuple[Path, List[str]]]:
    """
    Importación inteligente con aislamiento por pozo:
    - Recolecta archivos `.las`/`.LAS` desde archivos sueltos, carpeta de pozo o `.zip` (con protección ZipSlip).
    - Inspecciona la identidad `WELL` de cada encabezado antes de guardarlos en `workspace_imports/`:
      * Si todos pertenecen al mismo pozo, los agrupa en una sola carpeta (`target_well_name` o detectado).
      * Si se mezclaron archivos de múltiples pozos distintos (ej. CVa-25 y CVa-38 juntos), los separa
        automáticamente en subcarpetas independientes por pozo para no mezclar contextos del LLM.
    """
    if not paths:
        raise ValueError("No se recibieron rutas de archivos o carpetas para importar.")

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        collected_las: List[Path] = []

        for item in paths:
            src = Path(item)
            if not src.exists():
                continue
            if src.is_dir():
                direct_las = [c for c in sorted(src.iterdir()) if c.is_file() and c.suffix.lower() == ".las"]
                if direct_las:
                    collected_las.extend(direct_las)
                else:
                    sub_las_dirs = [
                        d for d in sorted(src.iterdir())
                        if d.is_dir() and any(f.is_file() and f.suffix.lower() == ".las" for f in d.iterdir())
                    ]
                    if len(sub_las_dirs) > 1:
                        raise ValueError(
                            f"La carpeta '{src.name}' contiene {len(sub_las_dirs)} subcarpetas de pozos distintos. "
                            "Usá 'Añadir Carpeta Batch...' en el panel derecho para importarlos como lote."
                        )
                    for child in sorted(src.rglob("*")):
                        if child.is_file() and child.suffix.lower() == ".las":
                            collected_las.append(child)
            elif src.is_file() and src.suffix.lower() == ".las":
                collected_las.append(src)
            elif src.is_file() and src.suffix.lower() == ".zip":
                with zipfile.ZipFile(src, "r") as zf:
                    for member in zf.infolist():
                        if member.is_dir():
                            continue
                        member_path = Path(member.filename)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            continue
                        if member_path.suffix.lower() != ".las":
                            continue
                        extracted = tmp_dir / member_path.name
                        with zf.open(member, "r") as z_in, open(extracted, "wb") as f_out:
                            shutil.copyfileobj(z_in, f_out)
                        collected_las.append(extracted)

        if not collected_las:
            raise ValueError("No se encontraron archivos .las/.LAS válidos en los elementos seleccionados.")

        groups = group_las_files_by_well(collected_las)
        staged: List[Tuple[Path, List[str]]] = []

        if len(groups) == 1:
            only_group = next(iter(groups.values()))
            folder_label = (
                sanitize_folder_name(target_well_name)
                if target_well_name.strip()
                else sanitize_folder_name(str(only_group["well_name"]) or suggest_well_name_from_paths(paths))
            )
            dest_dir = workspace_root / folder_label
            dest_dir.mkdir(parents=True, exist_ok=True)
            copied: List[str] = []
            for fp in only_group["files"]:
                shutil.copy2(fp, dest_dir / fp.name)
                copied.append(fp.name)
            staged.append((dest_dir, sorted(set(copied))))
        else:
            for grp in groups.values():
                folder_label = sanitize_folder_name(str(grp["well_name"]))
                dest_dir = workspace_root / folder_label
                dest_dir.mkdir(parents=True, exist_ok=True)
                copied = []
                for fp in grp["files"]:
                    shutil.copy2(fp, dest_dir / fp.name)
                    copied.append(fp.name)
                staged.append((dest_dir, sorted(set(copied))))

        return staged


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


# ----------------------------------------------------------------------
# CATÁLOGO CRS COMPLETO (PYPROJ DATABASE) Y MAPA MUNDI (Spec 004 - RF-8)
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_all_projected_epsg_raw() -> Tuple[Dict[str, object], ...]:
    """Carga una sola vez los CRS proyectados EPSG desde la base geodésica de pyproj."""
    infos = query_crs_info(auth_name="EPSG", pj_types=[PJType.PROJECTED_CRS])
    items: List[Dict[str, object]] = []
    for info in infos:
        bounds = tuple(info.area_of_use.bounds) if info.area_of_use else None
        area_name = str(info.area_of_use.name) if info.area_of_use else ""
        items.append({
            "epsg": f"EPSG:{info.code}",
            "code": str(info.code),
            "name": str(info.name),
            "area_name": area_name,
            "bounds": bounds,  # (west, south, east, north)
        })
    return tuple(items)


def _crs_priority_score(item: Dict[str, object]) -> Tuple[int, int]:
    """Prioriza sistemas modernos y de uso estándar en industria petrolera."""
    name = str(item["name"])
    if "POSGAR 2007" in name:
        return (0, int(item["code"]))
    if "WGS 84 / UTM zone" in name:
        return (1, int(item["code"]))
    if "POSGAR 94" in name or "POSGAR 98" in name:
        return (2, int(item["code"]))
    if "Campo Inchauspe" in name:
        return (3, int(item["code"]))
    if any(k in name for k in ("SIRGAS", "NAD83", "ETRS89", "ED50")):
        return (4, int(item["code"]))
    return (5, int(item["code"]))


def search_projected_crs(
    query: str = "",
    click_lon: Optional[float] = None,
    click_lat: Optional[float] = None,
    category: str = "all",
    limit: int = 250,
) -> List[Dict[str, object]]:
    """
    Busca sistemas de coordenadas proyectados EPSG en `pyproj.database`:
    - Si `click_lon` y `click_lat` están definidos, consulta únicamente CRS válidos en ese punto.
    - Soporta filtro de texto libre (`EPSG`, `POSGAR`, `UTM`, país) y categoría (`all`, `argentina`, `utm`).
    """
    if click_lon is not None and click_lat is not None:
        aoi = AreaOfInterest(click_lon, click_lat, click_lon, click_lat)
        infos = query_crs_info(
            auth_name="EPSG",
            pj_types=[PJType.PROJECTED_CRS],
            area_of_interest=aoi,
        )
        source_items = [
            {
                "epsg": f"EPSG:{i.code}",
                "code": str(i.code),
                "name": str(i.name),
                "area_name": str(i.area_of_use.name) if i.area_of_use else "",
                "bounds": tuple(i.area_of_use.bounds) if i.area_of_use else None,
            }
            for i in infos
        ]
    else:
        source_items = list(_load_all_projected_epsg_raw())

    q = query.strip().lower()
    cat = category.lower()
    filtered: List[Dict[str, object]] = []

    for item in source_items:
        name_l = str(item["name"]).lower()
        area_l = str(item["area_name"]).lower()
        epsg_l = str(item["epsg"]).lower()

        if cat == "argentina" and not any(
            k in name_l or k in area_l for k in ("posgar", "argentina", "inchauspe", "chubut", "neuquen")
        ):
            continue
        if cat == "utm" and "utm zone" not in name_l:
            continue

        if q and not (q in epsg_l or q in name_l or q in area_l):
            continue
        filtered.append(item)

    filtered.sort(key=_crs_priority_score)
    return filtered[:limit]


def get_crs_details(epsg_code: str) -> Optional[Dict[str, object]]:
    """Retorna metadatos y `area_of_use.bounds` de un EPSG específico."""
    norm = epsg_code.strip().upper()
    if not norm.startswith("EPSG:") and norm.isdigit():
        norm = f"EPSG:{norm}"
    for item in _load_all_projected_epsg_raw():
        if item["epsg"] == norm:
            return item
    return None


def list_popular_oilfield_crs() -> List[Dict[str, object]]:
    """Lista curada de CRS frecuentes para poblar el desplegable rápido de Pestaña 2."""
    preferred_codes = [
        "5343", "5344", "5345", "5346", "5347", "5348", "5349",  # POSGAR 2007 Fajas 1-7
        "22181", "22182", "22183", "22184", "22185", "22186", "22187",  # POSGAR 94
        "22191", "22192", "22193", "22194", "22195", "22196", "22197",  # Campo Inchauspe
        "32718", "32719", "32720", "32721",  # UTM Sur Sudamérica
        "32613", "32614", "32615", "32618", "32619",  # UTM Norte Américas
        "3116", "31983", "31984",  # Colombia MAGNA-SIRGAS / Brasil SIRGAS 2000
    ]
    by_code = {str(it["code"]): it for it in _load_all_projected_epsg_raw()}
    return [by_code[c] for c in preferred_codes if c in by_code]


def load_world_continents_geometry() -> Dict[str, object]:
    """Carga la geometría offline de continentes y Argentina para el Mapa Mundi QGIS."""
    geom_path = PROJECT_ROOT / "config" / "world_continents.json"
    if not geom_path.exists():
        return {"continents": [], "argentina_outline": []}
    return json.loads(geom_path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# PERSISTENCIA DE PROYECTOS (Spec 004 - RF-7)
# ----------------------------------------------------------------------
def save_project_file(project_path: Path, project_state: Dict[str, object]) -> Path:
    """Guarda el proyecto completo (`*.wellcurator.json`) sin almacenar secretos de API keys."""
    payload = dict(project_state)
    payload["version"] = "1.0"
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()
    llm_cfg = dict(payload.get("llm_config") or {})
    llm_cfg.pop("api_key", None)  # Nunca persistir secretos en disco
    payload["llm_config"] = llm_cfg

    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return project_path


def load_project_file(project_path: Path) -> Dict[str, object]:
    """Carga un proyecto guardado (`*.wellcurator.json`)."""
    data = json.loads(project_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "wells" not in data:
        raise ValueError(f"El archivo {project_path.name} no es un proyecto válido de Well Curator.")
    return data


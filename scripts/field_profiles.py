"""
Perfiles de campo petrolero y resolución/proyección geodésica automática con `pyproj` (Spec 003 - RF-4).
- Prioriza coordenadas planas en Gauss-Krüger cuando están presentes y válidas.
- Proyecta automáticamente coordenadas geográficas (Lat/Lon WGS84/POSGAR) hacia la Faja
  Gauss-Krüger correspondiente (POSGAR 2007 Fajas 1-7 en Argentina) o UTM WGS84 fuera de Argentina.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pyproj

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIELDS_CONFIG_DIR = PROJECT_ROOT / "config" / "fields"

# POSGAR 2007 / Argentina Faja 1..7 (EPSG:5343..5349)
POSGAR2007_FAJA_EPSG: Dict[int, str] = {
    1: "EPSG:5343",
    2: "EPSG:5344",
    3: "EPSG:5345",
    4: "EPSG:5346",
    5: "EPSG:5347",
    6: "EPSG:5348",
    7: "EPSG:5349",
}


@dataclass
class FieldProfile:
    """Configuración de dominio y geodésica parametrizable por yacimiento/cliente."""
    profile_name: str = "generic"
    description: str = ""
    target_depth_unit: str = "M"
    prioritize_gauss_kruger: bool = True
    forced_epsg: Optional[str] = None
    forced_gk_faja: Optional[int] = None
    coord_x_range: Optional[Tuple[float, float]] = None
    coord_y_range: Optional[Tuple[float, float]] = None
    coord_decimals: Optional[int] = 0
    uwi_pad_rules: List[Dict[str, str]] = field(default_factory=list)
    well_name_regex_replacements: List[Tuple[str, str]] = field(default_factory=list)
    prompt_domain_hints: str = ""
    pressure_summary_mnemonics: List[str] = field(
        default_factory=lambda: [
            "MTEP_SAP", "MTEP_HYD", "CP_SAP", "CP_HYD", "PCO_HYD", "PTV_XPT",
            "PRES", "BHP", "PS", "PQZ",
        ]
    )


def load_field_profile(profile_or_path: str | Path = "generic") -> FieldProfile:
    """
    Carga un `FieldProfile` desde `config/fields/<nombre>.json` o desde una ruta explícita.
    """
    cand = Path(profile_or_path)
    if not cand.exists():
        cand = FIELDS_CONFIG_DIR / f"{profile_or_path}.json"
    if not cand.exists():
        raise FileNotFoundError(f"No se encontró el perfil de campo '{profile_or_path}' en {FIELDS_CONFIG_DIR}")

    raw = json.loads(cand.read_text(encoding="utf-8"))
    x_range = tuple(raw["coord_x_range"]) if raw.get("coord_x_range") else None
    y_range = tuple(raw["coord_y_range"]) if raw.get("coord_y_range") else None
    name_reps = [(pair[0], pair[1]) for pair in raw.get("well_name_regex_replacements", [])]

    return FieldProfile(
        profile_name=raw.get("profile_name", cand.stem),
        description=raw.get("description", ""),
        target_depth_unit=str(raw.get("target_depth_unit", "M")).upper(),
        prioritize_gauss_kruger=bool(raw.get("prioritize_gauss_kruger", True)),
        forced_epsg=raw.get("forced_epsg"),
        forced_gk_faja=raw.get("forced_gk_faja"),
        coord_x_range=x_range,
        coord_y_range=y_range,
        coord_decimals=raw.get("coord_decimals", 0),
        uwi_pad_rules=list(raw.get("uwi_pad_rules", [])),
        well_name_regex_replacements=name_reps,
        prompt_domain_hints=raw.get("prompt_domain_hints", ""),
        pressure_summary_mnemonics=list(
            raw.get("pressure_summary_mnemonics", FieldProfile().pressure_summary_mnemonics)
        ),
    )


def detect_gk_faja_from_lon(lon_deg: float) -> Optional[int]:
    """
    Determina la Faja Gauss-Krüger argentina (1 a 7) a partir de la longitud geográfica.
    Meridianos centrales: -72° (F1), -69° (F2), -66° (F3), -63° (F4), -60° (F5), -57° (F6), -54° (F7).
    """
    if not (-73.5 <= lon_deg <= -52.5):
        return None
    faja = int(round((75.0 + lon_deg) / 3.0))
    return max(1, min(7, faja))


def detect_utm_epsg(lat_deg: float, lon_deg: float) -> str:
    """Determina el código EPSG UTM WGS84 para coordenadas fuera de Gauss-Krüger Argentina."""
    zone = int((lon_deg + 180.0) // 6.0) + 1
    zone = max(1, min(60, zone))
    base = 32600 if lat_deg >= 0 else 32700
    return f"EPSG:{base + zone}"


def _looks_like_latlon(val1: float, val2: float) -> bool:
    return (-90.0 <= val1 <= 90.0 and -180.0 <= val2 <= 180.0) and (
        abs(val1) <= 180.0 and abs(val2) <= 180.0
    )


def _is_valid_argentina_gk_pair(x_east: float, y_north: float) -> bool:
    """
    Verifica si (x_east, y_north) corresponde a un par válido Gauss-Krüger Argentina:
    - Este entre 1.150.000 y 7.850.000 (Fajas 1 a 7, falso este = Faja*1e6 + 500.000 ± 350 km).
    - Norte entre 3.700.000 y 7.750.000 (latitudes ~ -56° a -21°).
    """
    if not (1_150_000.0 <= x_east <= 7_850_000.0):
        return False
    if not (3_700_000.0 <= y_north <= 7_750_000.0):
        return False
    faja = int(x_east // 1_000_000)
    if not (1 <= faja <= 7):
        return False
    offset_from_cm = abs(x_east - (faja * 1_000_000.0 + 500_000.0))
    return offset_from_cm <= 350_000.0


def project_latlon_to_planar(
    lat_deg: float,
    lon_deg: float,
    profile: FieldProfile,
) -> Tuple[float, float, str]:
    """
    Proyecta (lat_deg, lon_deg) en WGS84 (EPSG:4326) al sistema plano apropiado usando `pyproj`:
    1. Si `profile.forced_epsg` está definido, usa ese EPSG.
    2. Si `profile.forced_gk_faja` (1..7) está definido, usa POSGAR 2007 Faja N.
    3. Si la longitud cae en Argentina (-73.5° a -52.5°), proyecta automáticamente a la Faja GK (1..7).
    4. Caso contrario (campos internacionales), proyecta automáticamente a UTM WGS84.
    """
    target_epsg: Optional[str] = profile.forced_epsg
    if not target_epsg and profile.forced_gk_faja in POSGAR2007_FAJA_EPSG:
        target_epsg = POSGAR2007_FAJA_EPSG[int(profile.forced_gk_faja)]

    if not target_epsg:
        faja = detect_gk_faja_from_lon(lon_deg)
        if faja is not None:
            target_epsg = POSGAR2007_FAJA_EPSG[faja]
        else:
            target_epsg = detect_utm_epsg(lat_deg, lon_deg)

    transformer = pyproj.Transformer.from_crs("EPSG:4326", target_epsg, always_xy=True)
    x_proj, y_proj = transformer.transform(lon_deg, lat_deg)
    return float(x_proj), float(y_proj), target_epsg


def _round_coord(val: float, decimals: Optional[int]) -> float | int:
    if decimals is None:
        return round(float(val), 6)
    if decimals == 0:
        return int(round(float(val)))
    return round(float(val), decimals)


def resolve_well_coordinates(
    meta: Dict[str, object],
    profile: FieldProfile,
) -> Tuple[Optional[float | int], Optional[float | int], str, str]:
    """
    Resuelve las coordenadas finales `(coord_x, coord_y, crs_used, resolution_note)`:
    - Prioriza coordenadas planas Gauss-Krüger cuando están presentes y válidas.
    - Corrige automáticamente el intercambio X/Y si vinieron invertidas respecto al rango del perfil
      o respecto a las fajas Gauss-Krüger argentinas.
    - Si no hay coordenadas planas válidas pero hay Lat/Lon (o si X/Y vinieron en grados),
      proyecta automáticamente con `pyproj` a Gauss-Krüger o UTM según ubicación.
    """
    cx = meta.get("coord_x")
    cy = meta.get("coord_y")
    lat = meta.get("latitud")
    lon = meta.get("longitud")

    cx_f = float(cx) if isinstance(cx, (int, float)) else None
    cy_f = float(cy) if isinstance(cy, (int, float)) else None
    lat_f = float(lat) if isinstance(lat, (int, float)) else None
    lon_f = float(lon) if isinstance(lon, (int, float)) else None

    # Si coord_x / coord_y vinieron en grados decimales (-180..180), moverlos a lat/lon si faltaban
    if cx_f is not None and cy_f is not None and _looks_like_latlon(cx_f, cy_f):
        if lat_f is None and lon_f is None:
            # En Argentina lat ~ -55..-21, lon ~ -73..-53; en general si |cx_f| <= 90 y cy_f != cx_f
            if -90.0 <= cy_f <= 90.0 and -180.0 <= cx_f <= 180.0 and abs(cx_f) > abs(cy_f) and cx_f < 0 and cy_f < 0:
                # cx parece longitud (ej. -69.2) y cy parece latitud (ej. -46.6)
                lon_f, lat_f = cx_f, cy_f
            else:
                lat_f, lon_f = cx_f, cy_f
        cx_f = None
        cy_f = None

    # Asegurar que latitud esté en [-90, 90] y longitud en [-180, 180] si vinieron invertidas
    if lat_f is not None and lon_f is not None:
        if abs(lat_f) > 90.0 and abs(lon_f) <= 90.0:
            lat_f, lon_f = lon_f, lat_f

    # Caso 1: Priorizar coordenadas planas Gauss-Krüger si existen
    if cx_f is not None and cy_f is not None and profile.prioritize_gauss_kruger:
        # 1A: Si el perfil define rangos explícitos X / Y
        if profile.coord_x_range and profile.coord_y_range:
            xmin, xmax = profile.coord_x_range
            ymin, ymax = profile.coord_y_range
            if (xmin <= cy_f <= xmax) and (ymin <= cx_f <= ymax):
                cx_f, cy_f = cy_f, cx_f
                note = f"GK invertidas X/Y corregidas por rango del perfil ({profile.profile_name})"
            else:
                note = f"GK directas según perfil ({profile.profile_name})"
            crs_used = profile.forced_epsg or (
                POSGAR2007_FAJA_EPSG.get(int(cx_f // 1_000_000), "GAUSS_KRUGER")
            )
            return _round_coord(cx_f, profile.coord_decimals), _round_coord(cy_f, profile.coord_decimals), crs_used, note

        # 1B: Detección genérica de Gauss-Krüger Argentina (Fajas 1 a 7)
        direct_ok = _is_valid_argentina_gk_pair(cx_f, cy_f)
        swapped_ok = _is_valid_argentina_gk_pair(cy_f, cx_f)

        # Si ambos órdenes lucen posibles (ej. Faja 4 donde Easting ~4.5M y Northing ~4.5M),
        # usar longitud/latitud si está disponible para desempatar.
        if direct_ok and swapped_ok and lon_f is not None:
            expected_faja = detect_gk_faja_from_lon(lon_f)
            if expected_faja and int(cy_f // 1_000_000) == expected_faja and int(cx_f // 1_000_000) != expected_faja:
                cx_f, cy_f = cy_f, cx_f
                crs_used = POSGAR2007_FAJA_EPSG.get(expected_faja, "GAUSS_KRUGER")
                return _round_coord(cx_f, profile.coord_decimals), _round_coord(cy_f, profile.coord_decimals), crs_used, "GK X/Y desempatadas con Longitud"

        if not direct_ok and swapped_ok:
            cx_f, cy_f = cy_f, cx_f
            faja = int(cx_f // 1_000_000)
            crs_used = POSGAR2007_FAJA_EPSG.get(faja, "GAUSS_KRUGER")
            return _round_coord(cx_f, profile.coord_decimals), _round_coord(cy_f, profile.coord_decimals), crs_used, f"GK X/Y corregidas automáticamente (Faja {faja})"

        if direct_ok:
            faja = int(cx_f // 1_000_000)
            crs_used = POSGAR2007_FAJA_EPSG.get(faja, "GAUSS_KRUGER")
            return _round_coord(cx_f, profile.coord_decimals), _round_coord(cy_f, profile.coord_decimals), crs_used, f"GK directas detectadas (Faja {faja})"

    # Caso 2: Si no hubo GK válidas pero tenemos Lat/Lon -> proyectar con pyproj
    if lat_f is not None and lon_f is not None and (-90.0 <= lat_f <= 90.0) and (-180.0 <= lon_f <= 180.0):
        px, py, target_epsg = project_latlon_to_planar(lat_f, lon_f, profile)
        return (
            _round_coord(px, profile.coord_decimals),
            _round_coord(py, profile.coord_decimals),
            target_epsg,
            f"Proyectado desde Lat/Lon ({lat_f:.5f}, {lon_f:.5f}) hacia {target_epsg}",
        )

    # Caso 3: Coordenadas planas no GK (ej. UTM o locales en metros ya provistas)
    if cx_f is not None and cy_f is not None:
        return (
            _round_coord(cx_f, profile.coord_decimals),
            _round_coord(cy_f, profile.coord_decimals),
            profile.forced_epsg or "PLANAR_UNSPECIFIED",
            "Coordenadas planas conservadas sin reproyección",
        )

    return None, None, "MISSING", "Sin coordenadas válidas"


def normalize_well_metadata(
    meta: Dict[str, object],
    profile: Optional[FieldProfile] = None,
    fallback_well_name: Optional[str] = None,
) -> Dict[str, object]:
    """
    Aplica normalización parametrizada según `FieldProfile` (Spec 003 - RF-4):
    - Limpia `nombre_pozo` con reglas del perfil.
    - Estandariza `uwi` en mayúsculas y aplica `uwi_pad_rules` solo si el perfil las declara.
    - Prioriza Gauss-Krüger y proyecta automáticamente Lat/Lon con `pyproj`.
    (Si `profile is None`, usa el perfil por defecto 'golfo_san_jorge_faja2' si existe para mantener
    compatibilidad retroactiva con Spec 002; pasar `load_field_profile('generic')` para modo universal).
    """
    if profile is not None:
        active_profile = profile
    else:
        default_gsj = FIELDS_CONFIG_DIR / "golfo_san_jorge_faja2.json"
        active_profile = load_field_profile(default_gsj) if default_gsj.exists() else FieldProfile(profile_name="generic")
    out = dict(meta)

    raw_name = str(out.get("nombre_pozo") or fallback_well_name or "").strip()
    for pat, rep in active_profile.well_name_regex_replacements:
        raw_name = re.sub(pat, rep, raw_name).strip()
    if fallback_well_name and not raw_name:
        raw_name = fallback_well_name
    out["nombre_pozo"] = raw_name

    uwi_val = out.get("uwi")
    if uwi_val:
        uwi_str = re.sub(r"\s+", "", str(uwi_val).upper())
        for rule in active_profile.uwi_pad_rules:
            pat = rule.get("pattern", "")
            app = rule.get("append", "")
            if pat and app and re.match(pat, uwi_str):
                uwi_str = uwi_str + app
        out["uwi"] = uwi_str

    cx_res, cy_res, crs_used, coord_note = resolve_well_coordinates(out, active_profile)
    out["coord_x"] = cx_res
    out["coord_y"] = cy_res
    out["crs_used"] = crs_used
    out["coord_resolution_note"] = coord_note
    out["field_profile"] = active_profile.profile_name

    if out.get("cota") is not None:
        out["cota"] = round(float(out["cota"]), 2)

    return out

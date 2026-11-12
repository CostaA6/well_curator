"""
Módulo de validación visual para Spec 001 (RF-7).
Genera gráficos en tests/001-extraccion-y-exploracion-las/images/ para inspeccionar:
1. Rangos de profundidad (STRT - STOP), pasos (STEP) y estaciones en tiempo (XPT) por pozo.
2. Visualización de curvas de perfilaje vs. profundidad (destacando curvas repetidas :2, :3).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.las_explorer import WellExplorationResult, LasFileSummary


def plot_well_depth_coverage(
    well_result: WellExplorationResult,
    output_dir: Path,
) -> Path:
    """
    Genera un gráfico de barras verticales (Gantt de profundidad) que muestra el rango
    [STRT, STOP] y el STEP de cada archivo .LAS en profundidad, además de las profundidades
    puntuales de las estaciones en tiempo (XPT).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=130)

    depth_summaries = [s for s in well_result.las_summaries if s.index_type == "DEPTH"]
    time_summaries = [s for s in well_result.las_summaries if s.index_type == "TIME"]

    x_labels: List[str] = []
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf", "#d62728"]

    for i, s in enumerate(depth_summaries):
        # Calcular el rango efectivo con datos válidos entre todas sus curvas
        valid_mins = [c.valid_min_index for c in s.curves if c.valid_min_index is not None]
        valid_maxs = [c.valid_max_index for c in s.curves if c.valid_max_index is not None]
        eff_min = min(valid_mins) if valid_mins else s.declared_start
        eff_max = max(valid_maxs) if valid_maxs else s.declared_stop

        decl_top = min(s.declared_start, s.declared_stop)
        decl_bot = max(s.declared_start, s.declared_stop)
        eff_top = min(eff_min, eff_max)
        eff_bot = max(eff_min, eff_max)

        color = colors[i % len(colors)]
        # Barra declarada en gris claro de fondo
        ax.bar(
            i,
            decl_bot - decl_top,
            bottom=decl_top,
            width=0.48,
            color="#d9d9d9",
            edgecolor="#888888",
            linestyle="--",
            label="Rango declarado (STRT-STOP)" if i == 0 else None,
        )
        # Barra efectiva con datos válidos
        ax.bar(
            i,
            eff_bot - eff_top,
            bottom=eff_top,
            width=0.36,
            color=color,
            alpha=0.88,
            edgecolor="black",
            label="Rango con datos válidos" if i == 0 else None,
        )

        # Texto con tope, base, step y cantidad de curvas
        mid_depth = (eff_top + eff_bot) / 2.0
        step_txt = f"STEP: {s.step:.4g}m" if s.step > 0 else "STEP: var"
        ax.text(
            i,
            mid_depth,
            f"{eff_top:.1f} - {eff_bot:.1f} m\n({step_txt} | {len(s.curves)} curvas)",
            ha="center",
            va="center",
            fontsize=8,
            fontweight="bold",
            color="white",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#222222", alpha=0.72),
        )

        short_name = s.file_name
        if len(short_name) > 26:
            short_name = short_name[:12] + "..." + short_name[-11:]
        x_labels.append(f"{short_name}\n[DEPTH]")

    # Si hay estaciones en TIEMPO (ej. XPT), graficarlas en una columna dedicada a la derecha
    if time_summaries:
        x_pos = len(depth_summaries)
        station_depths = [
            s.stationary_depth_m for s in time_summaries if s.stationary_depth_m is not None
        ]
        if station_depths:
            ax.scatter(
                [x_pos] * len(station_depths),
                station_depths,
                color="#e41a1c",
                marker="D",
                s=55,
                zorder=5,
                label=f"Estaciones en Tiempo XPT ({len(station_depths)} pts)",
            )
            for d in sorted(set(station_depths)):
                ax.text(
                    x_pos + 0.12,
                    d,
                    f"{d:.1f} m",
                    va="center",
                    fontsize=7.5,
                    color="#b30000",
                )
        x_labels.append(f"{len(time_summaries)} archivos XPT\n[TIME.S -> Estaciones]")

    ax.set_xticks(range(len(x_labels)))
    ax.set_xticklabels(x_labels, rotation=15, ha="right", fontsize=8.5)
    ax.invert_yaxis()  # En perfilaje de pozos la profundidad crece hacia abajo
    ax.set_ylabel("Profundidad [m]", fontsize=10, fontweight="bold")
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)

    meta = well_result.llm_metadata or {}
    uwi_str = meta.get("uwi") or "S/D"
    x_str = meta.get("coord_x") or "S/D"
    y_str = meta.get("coord_y") or "S/D"
    cota_str = meta.get("cota") or "S/D"

    ax.set_title(
        f"Cobertura de Profundidades y Archivos .LAS — Pozo {well_result.well_folder_name}\n"
        f"UWI: {uwi_str} | X: {x_str} | Y: {y_str} | Cota: {cota_str} m | "
        f"Curvas únicas: {len(well_result.unique_depth_curves)} (Repetidas: {len(well_result.repeated_depth_curves)})",
        fontsize=10.5,
        fontweight="bold",
    )
    ax.legend(loc="lower right", fontsize=8.5)
    fig.tight_layout()

    out_path = output_dir / f"{well_result.well_folder_name}_coverage.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_well_curves_preview(
    well_result: WellExplorationResult,
    well_dfs: Dict[str, pd.DataFrame],
    output_dir: Path,
    max_tracks: int = 4,
) -> Path:
    """
    Genera un gráfico multi-track de curvas vs. profundidad para el pozo.
    Prioriza graficar curvas repetidas (superponiendo la curva original y su :2 / :3
    para inspección visual) y curvas petrofísicas clave (GR, CAL/HCAL, DT, NEU/TNPH, RHOZ/ZDEN, etc.).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Mapear cada mnemónico base a todas sus ocurrencias en archivos DEPTH
    mnemonic_map: Dict[str, List[Tuple[str, str, str, pd.Series, pd.Series]]] = {}
    for s in well_result.las_summaries:
        if s.index_type != "DEPTH":
            continue
        df = well_dfs[s.file_name]
        depth_series = df.iloc[:, 0]
        for idx, c in enumerate(s.curves, start=1):
            if idx >= df.shape[1]:
                continue
            val_series = df.iloc[:, idx]
            if val_series.notna().sum() == 0:
                continue
            mnemonic_map.setdefault(c.original_mnemonic, []).append(
                (c.assigned_name, s.file_name, c.unit, depth_series, val_series)
            )

    # Seleccionar hasta max_tracks curvas interesantes: primero repetidas, luego principales conocidas
    repeated_candidates = [
        m for m, occs in mnemonic_map.items() if len(occs) > 1 and m not in ("BIT", "BS")
    ]
    preferred_Order = ["GR", "CAL", "HCAL", "DT", "NEU", "TNPH", "RHOZ", "DPHZ", "M2R6", "AHT90", "CCL", "TENS"]
    selected_mnemonics: List[str] = []

    # 1. Añadir hasta 2 curvas repetidas para compararlas visualmente
    for m in preferred_Order + sorted(repeated_candidates):
        if m in repeated_candidates and m not in selected_mnemonics:
            selected_mnemonics.append(m)
            if len(selected_mnemonics) >= 2:
                break

    # 2. Completar con curvas petrofísicas estándar
    for m in preferred_Order + sorted(mnemonic_map.keys()):
        if m in mnemonic_map and m not in selected_mnemonics:
            selected_mnemonics.append(m)
        if len(selected_mnemonics) >= max_tracks:
            break

    n_tracks = max(1, len(selected_mnemonics))
    fig, axes = plt.subplots(1, n_tracks, figsize=(3.2 * n_tracks, 8.5), dpi=130, sharey=True)
    if n_tracks == 1:
        axes = [axes]

    track_colors = ["#1f77b4", "#e41a1c", "#2ca02c", "#9467bd"]

    for ax, mnem in zip(axes, selected_mnemonics):
        occs = mnemonic_map.get(mnem, [])
        for occ_idx, (assigned_name, src_file, unit, depth_s, val_s) in enumerate(occs):
            valid = depth_s.notna() & val_s.notna()
            if not valid.any():
                continue
            d_clean = depth_s[valid]
            v_clean = val_s[valid]
            short_src = src_file[:16] + "..." if len(src_file) > 19 else src_file
            c_color = track_colors[occ_idx % len(track_colors)]
            linestyle = "-" if occ_idx == 0 else "--"
            alpha = 0.85 if occ_idx == 0 else 0.75

            ax.plot(
                v_clean,
                d_clean,
                label=f"{assigned_name} ({short_src})",
                color=c_color,
                linewidth=1.0 if occ_idx == 0 else 1.2,
                linestyle=linestyle,
                alpha=alpha,
            )

        unit_label = occs[0][2] if occs else ""
        is_rep_tag = " [REPETIDA]" if len(occs) > 1 else ""
        ax.set_title(f"{mnem}{is_rep_tag}\n[{unit_label}]", fontsize=9.5, fontweight="bold")
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.legend(loc="upper right", fontsize=7)

        # Escala horizontal robusta (percentil 1% - 99%) para evitar que un pico de zapato aplaste la curva
        all_vals = pd.concat([o[4].dropna() for o in occs], ignore_index=True) if occs else pd.Series(dtype=float)
        if len(all_vals) > 10:
            p1, p99 = np.percentile(all_vals, [1, 99])
            if p99 > p1:
                margin = (p99 - p1) * 0.12
                ax.set_xlim(p1 - margin, p99 + margin)

    axes[0].invert_yaxis()
    axes[0].set_ylabel("Profundidad [m]", fontsize=10, fontweight="bold")

    fig.suptitle(
        f"Inspección Visual de Curvas y Tramos Repetidos — {well_result.well_folder_name}",
        fontsize=11,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95])

    out_path = output_dir / f"{well_result.well_folder_name}_curves.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_unified_vs_raw_comparison(
    well_result: WellExplorationResult,
    well_dfs: Dict[str, pd.DataFrame],
    unified_csv_path: Path,
    output_dir: Path,
    max_tracks: int = 4,
) -> Path:
    """
    Spec 002 (RNF-2): Superpone las curvas originales de los .LAS con las curvas del CSV
    unificado en la grilla común (al paso más chico) para verificar visualmente que no haya
    extrapolaciones fuera de rango ni distorsiones en la interpolación.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    unified_df = pd.read_csv(unified_csv_path)

    # Mapear cada assigned_name a su serie original (depth, values, source_file)
    raw_series_map: Dict[str, Tuple[pd.Series, pd.Series, str, str]] = {}
    for s in well_result.las_summaries:
        if s.index_type != "DEPTH":
            continue
        df = well_dfs[s.file_name]
        d_s = df.iloc[:, 0]
        for idx, c in enumerate(s.curves, start=1):
            if idx < df.shape[1]:
                v_s = df.iloc[:, idx]
                if v_s.notna().any():
                    raw_series_map[c.assigned_name] = (d_s, v_s, s.file_name, c.unit)

    preferred = [
        "NEU", "DT", "CAL", "HCAL", "ZDEN", "RHOZ", "M2R6", "M2R6:2",
        "SP", "TNPH", "CBL", "AF10", "AF10:2", "BS", "BS:2",
    ]
    selected_cols: List[str] = []
    for cand in preferred + sorted(raw_series_map.keys()):
        if cand in unified_df.columns and cand in raw_series_map and cand not in selected_cols:
            selected_cols.append(cand)
        if len(selected_cols) >= max_tracks:
            break

    n_tracks = max(1, len(selected_cols))
    fig, axes = plt.subplots(1, n_tracks, figsize=(3.4 * n_tracks, 8.5), dpi=130, sharey=True)
    if n_tracks == 1:
        axes = [axes]

    u_depth = unified_df["DEPTH"]

    for ax, col_name in zip(axes, selected_cols):
        d_raw, v_raw, src_file, unit = raw_series_map[col_name]
        valid_raw = d_raw.notna() & v_raw.notna()

        valid_uni = unified_df[col_name].notna()

        ax.plot(
            v_raw[valid_raw],
            d_raw[valid_raw],
            color="#bbbbbb",
            linewidth=2.8,
            alpha=0.85,
            label=f"Original ({src_file[:12]}..)",
        )
        ax.plot(
            unified_df.loc[valid_uni, col_name],
            u_depth[valid_uni],
            color="#d62728",
            linewidth=1.0,
            linestyle="--",
            label="CSV Grilla Común",
        )

        ax.set_title(f"{col_name} [{unit}]\n(n={int(valid_uni.sum())} pts)", fontsize=9.5, fontweight="bold")
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.legend(loc="upper right", fontsize=7.5)

        vals_clean = unified_df.loc[valid_uni, col_name]
        if len(vals_clean) > 10:
            p1, p99 = np.percentile(vals_clean, [1, 99])
            if p99 > p1:
                margin = (p99 - p1) * 0.12
                ax.set_xlim(p1 - margin, p99 + margin)

    axes[0].invert_yaxis()
    axes[0].set_ylabel("Profundidad Grilla Común [m]", fontsize=10, fontweight="bold")

    step_val = round(float(u_depth.iloc[1] - u_depth.iloc[0]), 4) if len(u_depth) > 1 else 0.0
    uwi_val = unified_df["UWI"].iloc[0] if len(unified_df) > 0 else ""
    x_val = unified_df["XWELL"].iloc[0] if len(unified_df) > 0 else ""
    y_val = unified_df["YWELL"].iloc[0] if len(unified_df) > 0 else ""
    cota_val = unified_df["COTA"].iloc[0] if len(unified_df) > 0 else ""

    fig.suptitle(
        f"Validación Grilla Común vs. Original — { unified_df['nombre_pozo'].iloc[0] }\n"
        f"UWI: {uwi_val} | X: {x_val} | Y: {y_val} | Cota: {cota_val}m | "
        f"Rango: {u_depth.min():.2f}-{u_depth.max():.2f}m (Step={step_val}m)",
        fontsize=10.5,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])

    out_path = output_dir / f"{well_result.well_folder_name}_unified_vs_raw.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_excel_availability_matrix(excel_path: Path, output_dir: Path) -> Path:
    """
    Spec 002 (RNF-2): Genera un mapa de calor de la matriz de disponibilidad de curvas
    ('yes' / 'no') contenida en `output/well_metadata.xlsx`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_excel(excel_path)
    fixed_cols = ["File", "Well", "UWI", "Start", "Stop", "Step", "X", "Y", "Elevation"]
    curve_cols = [c for c in df.columns if c not in fixed_cols]

    binary_matrix = np.zeros((len(df), len(curve_cols)), dtype=int)
    for r_idx in range(len(df)):
        for c_idx, c_name in enumerate(curve_cols):
            if str(df.loc[r_idx, c_name]).strip().lower() == "yes":
                binary_matrix[r_idx, c_idx] = 1

    fig_w = max(14.0, len(curve_cols) * 0.18)
    fig, ax = plt.subplots(figsize=(fig_w, 5.2), dpi=130)

    cmap = matplotlib.colors.ListedColormap(["#f5f5f5", "#2ca02c"])
    ax.imshow(binary_matrix, aspect="auto", cmap=cmap, interpolation="nearest")

    ax.set_yticks(range(len(df)))
    well_labels = [
        f"{row['Well']} ({row['UWI']}) [{row['Start']:.1f}-{row['Stop']:.1f}m, step={row['Step']}m]"
        for _, row in df.iterrows()
    ]
    ax.set_yticklabels(well_labels, fontsize=8.5, fontweight="bold")

    ax.set_xticks(range(len(curve_cols)))
    ax.set_xticklabels(curve_cols, rotation=90, fontsize=6.8)

    ax.set_xticks(np.arange(-0.5, len(curve_cols), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(df), 1), minor=True)
    ax.grid(which="minor", color="#cccccc", linestyle="-", linewidth=0.5)

    ax.set_title(
        f"Matriz Maestra de Disponibilidad de Curvas en Excel ({len(df)} Pozos x {len(curve_cols)} Curvas — Verde='yes', Gris='no')",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()

    out_path = output_dir / "excel_curve_availability_matrix.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_xpt_stations_summary(summary_csv_path: Path, output_dir: Path) -> Path:
    """
    Spec 002 (RNF-2): Grafica las estaciones de presión en tiempo (XPT) exportadas en
    `output/time_logs/xpt_stations_summary.csv` (Profundidad Estacionaria vs. Presión Final).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(summary_csv_path)

    fig, axes = plt.subplots(1, 2, figsize=(11, 6), dpi=130, sharey=True)
    colors = {"YPF.SC.CVa-37": "#1f77b4", "YPF.SC.CVa-39": "#e41a1c"}

    for well_name, group in df.groupby("Well"):
        c = colors.get(str(well_name), "#2ca02c")
        # Panel 1: Presión de Formación / Sonda vs Profundidad
        p_col = "CP_SAP_final" if "CP_SAP_final" in group.columns and group["CP_SAP_final"].notna().any() else "CP_HYD_final"
        if p_col in group.columns:
            axes[0].scatter(
                group[p_col],
                group["Station_Depth_m"],
                label=f"{well_name} ({len(group)} estaciones)",
                color=c,
                s=55,
                edgecolor="black",
            )
        # Panel 2: Duración del ensayo (segundos) vs Profundidad
        axes[1].barh(
            group["Station_Depth_m"],
            group["Duration_s"],
            height=12.0,
            color=c,
            alpha=0.75,
            edgecolor="black",
            label=str(well_name),
        )

    axes[0].invert_yaxis()
    axes[0].set_ylabel("Profundidad Estacionaria (PDEP_XPT) [m]", fontsize=10, fontweight="bold")
    axes[0].set_xlabel("Presión Final Estabilizada [psia]", fontsize=9.5, fontweight="bold")
    axes[0].set_title("Estaciones XPT: Presión vs. Profundidad", fontsize=10, fontweight="bold")
    axes[0].grid(True, linestyle=":", alpha=0.6)
    axes[0].legend(loc="lower left", fontsize=8.5)

    axes[1].set_xlabel("Duración del Registro en Tiempo [s]", fontsize=9.5, fontweight="bold")
    axes[1].set_title("Estaciones XPT: Duración por Estación", fontsize=10, fontweight="bold")
    axes[1].grid(True, linestyle=":", alpha=0.6)

    fig.suptitle(
        "Resumen de Registros en Tiempo (XPT) Exportados en output/time_logs/",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()

    out_path = output_dir / "xpt_time_stations_overview.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


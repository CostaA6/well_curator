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

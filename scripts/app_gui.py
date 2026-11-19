"""
Interfaz Gráfica Comercial — Well Curator Studio (Spec 004 — PySide6 / Qt6).

Flujo guiado en 5 pestañas:
  1. Importar Pozos (Drag & Drop de carpetas, .las/.LAS sueltos y .zip -> workspace_imports/ + Lotes Batch)
  2. Configuración (Selector agnóstico de modelo LLM, Perfil de campo/Geodesia pyproj, Curado)
  3. Procesamiento, Revisión Supervisada y Reporte Escrito por el LLM
  4. Validación Visual, Mapa Geográfico Interactivo (Folium) y Visor Multi-Track de Logs (hasta 6 tracks)
  5. Explorador de Salidas (output/well_metadata.xlsx, CSVs por pozo, time_logs/, batch_errors.log)

Ejecución:
  python scripts/app_gui.py
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

# Forzar Matplotlib QtAgg a usar PySide6 (evita conflicto si PyQt6 también está instalado)
os.environ["QT_API"] = "pyside6"

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from PySide6.QtCore import QCoreApplication, Qt, QThread, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.field_profiles import (
    POSGAR2007_FAJA_EPSG,
    FieldProfile,
    list_field_profiles,
    load_field_profile,
    normalize_well_metadata,
)
from scripts.las_explorer import WellExplorationResult, explore_well_directory
from scripts.llm_extractor import (
    DEFAULT_MODEL_PATH,
    LLMConfig,
    ManagedLLMSession,
    list_local_gguf_models,
    test_llm_connection,
)
from scripts.ui_controller import (
    WORKSPACE_IMPORTS_DIR,
    build_folium_well_map_html,
    discover_well_sources,
    get_crs_details,
    inverse_project_to_wgs84,
    list_popular_oilfield_crs,
    load_output_inventory,
    load_project_file,
    load_world_continents_geometry,
    prepare_well_log_tracks,
    save_project_file,
    search_projected_crs,
    stage_grouped_well_drop_items,
    stage_well_drop_items,
    suggest_well_name_from_paths,
)
from scripts.well_exporter import (
    WellExportSummary,
    compute_batch_depth_grid_bounds,
    export_well_to_output,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except Exception:
    HAS_WEBENGINE = False


APP_STYLESHEET = """
QMainWindow {
    background-color: #F3F5F8;
}
QWidget {
    font-family: "Segoe UI", "Calibri", sans-serif;
    font-size: 9.5pt;
    color: #1E293B;
}
QFrame#TopBanner {
    background-color: #1E293B;
    border-bottom: 3px solid #2563EB;
}
QFrame#ProjectBar {
    background-color: #FFFFFF;
    border-bottom: 1px solid #CBD5E1;
}
QLabel#BannerTitle {
    color: #FFFFFF;
    font-size: 13pt;
    font-weight: bold;
}
QLabel#BannerSubtitle {
    color: #CBD5E1;
    font-size: 8.5pt;
}
QLabel#StatusBadge {
    background-color: #334155;
    color: #E2E8F0;
    border: 1px solid #475569;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 8.5pt;
}
QTabWidget::pane {
    border: 1px solid #CBD5E1;
    background: #FFFFFF;
    border-radius: 4px;
}
QTabBar::tab {
    background: #E2E8F0;
    color: #334155;
    padding: 8px 14px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #FFFFFF;
    color: #1D4ED8;
    border-top: 2px solid #2563EB;
}
QGroupBox {
    font-weight: bold;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 10px;
    background-color: #FFFFFF;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #0F172A;
}
QPushButton {
    background-color: #F8FAFC;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 6px 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #EFF6FF;
    border-color: #93C5FD;
}
QPushButton#PrimaryBtn {
    background-color: #2563EB;
    color: #FFFFFF;
    border-color: #1D4ED8;
}
QPushButton#PrimaryBtn:hover {
    background-color: #1D4ED8;
}
QPushButton#PrimaryBtn:disabled {
    background-color: #94A3B8;
    border-color: #94A3B8;
}
QPushButton#SuccessBtn {
    background-color: #16A34A;
    color: #FFFFFF;
    border-color: #15803D;
}
QPushButton#SuccessBtn:hover {
    background-color: #15803D;
}
QFrame#DropZone {
    border: 2px dashed #64748B;
    border-radius: 6px;
    background-color: #F8FAFC;
}
QFrame#DropZone:hover {
    border-color: #2563EB;
    background-color: #EFF6FF;
}
QTableWidget {
    gridline-color: #E2E8F0;
    selection-background-color: #DBEAFE;
    selection-color: #1E293B;
}
QHeaderView::section {
    background-color: #F1F5F9;
    color: #0F172A;
    font-weight: bold;
    padding: 5px;
    border: 1px solid #CBD5E1;
}
"""


class DropZoneWidget(QFrame):
    """Área nativa de Drag & Drop que acepta carpetas de pozos, .las/.LAS sueltos y .zip."""

    paths_dropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(130)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Arrastrá y soltá aquí una CARPETA de pozo, archivos .LAS sueltos o un .ZIP")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 10pt; color: #1E293B;")

        hint = QLabel("Los archivos ingresados se copiarán en workspace_imports/<Pozo>/ sin modificar tus originales.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #64748B; font-size: 8.5pt;")

        layout.addWidget(title)
        layout.addWidget(hint)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("QFrame#DropZone { border: 2px dashed #2563EB; background-color: #DBEAFE; }")

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet("")

    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet("")
        urls = event.mimeData().urls()
        local_paths = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
        if local_paths:
            self.paths_dropped.emit(local_paths)


class MplCanvasWidget(QWidget):
    """Canvas Matplotlib embebido en Qt con barra de herramientas interactiva (zoom/pan/guardar)."""

    def __init__(self, figsize: Tuple[float, float] = (8.0, 5.0), parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=figsize, dpi=110)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)


class CRSSelectorDialog(QDialog):
    """
    Selector visual de Sistema de Referencia de Coordenadas (CRS) estilo QGIS (Spec 004 - RF-8):
    - Consulta la base geodésica completa de pyproj (5.291 EPSG proyectados).
    - Muestra un Mapa Mundi interactivo con grillas UTM (6°) y Fajas Gauss-Krüger Argentina (1-7).
    - Al hacer clic en cualquier punto del mapa, filtra los EPSG válidos en esa ubicación y dibuja
      en rojo el rectángulo `area_of_use.bounds` del CRS seleccionado.
    """

    def __init__(self, initial_epsg: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Selector de Proyección / Faja CRS (Estilo QGIS — Base pyproj EPSG)")
        self.resize(1120, 680)
        self.selected_epsg: str = initial_epsg
        self.click_lon: Optional[float] = None
        self.click_lat: Optional[float] = None
        self.current_results: List[Dict[str, object]] = []
        self._geom = load_world_continents_geometry()

        self._build_dialog_ui()
        self._redraw_map()
        self._run_search()
        if initial_epsg:
            self._select_epsg_in_table(initial_epsg)

    def _build_dialog_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Barra superior de filtros y presets de vista
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Buscar EPSG / Nombre / País:"))
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Ej: POSGAR, 5344, Argentina 2, UTM zone 19S, SIRGAS, Texas...")
        self.txt_search.textChanged.connect(self._run_search)
        top_bar.addWidget(self.txt_search, 2)

        self.cmb_cat = QComboBox()
        self.cmb_cat.addItem("Todos los CRS (5.291)", "all")
        self.cmb_cat.addItem("Argentina (POSGAR / Campo Inchauspe)", "argentina")
        self.cmb_cat.addItem("UTM WGS 84 Global", "utm")
        self.cmb_cat.currentIndexChanged.connect(self._run_search)
        top_bar.addWidget(self.cmb_cat)

        btn_clear_click = QPushButton("Limpiar Clic en Mapa")
        btn_clear_click.clicked.connect(self._clear_map_click)
        top_bar.addWidget(btn_clear_click)
        layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Panel Izquierdo: Mapa Mundi Clickable estilo QGIS
        left_panel = QWidget()
        left_l = QVBoxLayout(left_panel)
        left_l.setContentsMargins(0, 0, 0, 0)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Vista de Mapa:"))
        btn_z_world = QPushButton("Mundo Completo")
        btn_z_world.clicked.connect(lambda: self._set_map_bounds((-180, 180), (-75, 82)))
        btn_z_sa = QPushButton("Sudamérica / Argentina Fajas 1-7")
        btn_z_sa.clicked.connect(lambda: self._set_map_bounds((-82, -45), (-58, -15)))
        btn_z_na = QPushButton("Norteamérica")
        btn_z_na.clicked.connect(lambda: self._set_map_bounds((-140, -55), (15, 65)))
        zoom_row.addWidget(btn_z_world)
        zoom_row.addWidget(btn_z_sa)
        zoom_row.addWidget(btn_z_na)
        zoom_row.addStretch()
        left_l.addLayout(zoom_row)

        self.map_canvas = MplCanvasWidget(figsize=(6.8, 5.2))
        self.map_canvas.canvas.mpl_connect("button_press_event", self._on_map_clicked)
        left_l.addWidget(self.map_canvas, 1)

        self.lbl_click_info = QLabel("Hacé clic en el mapa mundi para detectar automáticamente Fajas GK o Zonas UTM en esa ubicación.")
        self.lbl_click_info.setStyleSheet("color: #1D4ED8; font-weight: 600;")
        left_l.addWidget(self.lbl_click_info)

        # Panel Derecho: Tabla de CRS encontrados + Info Area de Uso
        right_panel = QWidget()
        right_l = QVBoxLayout(right_panel)
        right_l.setContentsMargins(0, 0, 0, 0)

        self.tbl_crs = QTableWidget(0, 3)
        self.tbl_crs.setHorizontalHeaderLabels(["Código EPSG", "Nombre del Sistema Proyectado", "Área de Uso"])
        self.tbl_crs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_crs.horizontalHeader().setStretchLastSection(True)
        self.tbl_crs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl_crs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_crs.itemSelectionChanged.connect(self._on_crs_row_selected)
        self.tbl_crs.itemDoubleClicked.connect(lambda _: self.accept())
        right_l.addWidget(self.tbl_crs, 1)

        self.lbl_crs_detail = QLabel("Seleccioná un CRS de la lista o hacé clic sobre el mapa.")
        self.lbl_crs_detail.setWordWrap(True)
        self.lbl_crs_detail.setStyleSheet("background: #F8FAFC; border: 1px solid #CBD5E1; padding: 8px; border-radius: 4px;")
        right_l.addWidget(self.lbl_crs_detail)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([600, 500])
        layout.addWidget(splitter, 1)

        # Botonera inferior
        btn_box = QHBoxLayout()
        btn_auto = QPushButton("Usar Modo Automático (Priorizar GK / Auto Lat-Lon)")
        btn_auto.clicked.connect(self._accept_automatic)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        self.btn_apply = QPushButton("Aplicar Proyección Seleccionada")
        self.btn_apply.setObjectName("PrimaryBtn")
        self.btn_apply.clicked.connect(self.accept)

        btn_box.addWidget(btn_auto)
        btn_box.addStretch()
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(self.btn_apply)
        layout.addLayout(btn_box)

        self._xlim = (-180.0, 180.0)
        self._ylim = (-75.0, 82.0)

    def _set_map_bounds(self, xlim: Tuple[float, float], ylim: Tuple[float, float]) -> None:
        self._xlim = xlim
        self._ylim = ylim
        self._redraw_map()

    def _clear_map_click(self) -> None:
        self.click_lon = None
        self.click_lat = None
        self.lbl_click_info.setText("Filtro por clic limpiado. Mostrando catálogo EPSG completo.")
        self._run_search()
        self._redraw_map()

    def _on_map_clicked(self, event) -> None:
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            return
        self.click_lon = round(float(event.xdata), 3)
        self.click_lat = round(float(event.ydata), 3)
        self.lbl_click_info.setText(
            f"Punto elegido: Lat {self.click_lat:.3f}°, Lon {self.click_lon:.3f}° — Mostrando CRS EPSG válidos en esta zona."
        )
        self._run_search()
        self._redraw_map()

    def _run_search(self) -> None:
        self.current_results = search_projected_crs(
            query=self.txt_search.text(),
            click_lon=self.click_lon,
            click_lat=self.click_lat,
            category=str(self.cmb_cat.currentData() or "all"),
            limit=250,
        )
        self.tbl_crs.setRowCount(len(self.current_results))
        for r, item in enumerate(self.current_results):
            self.tbl_crs.setItem(r, 0, QTableWidgetItem(str(item["epsg"])))
            self.tbl_crs.setItem(r, 1, QTableWidgetItem(str(item["name"])))
            self.tbl_crs.setItem(r, 2, QTableWidgetItem(str(item["area_name"])))
        if self.current_results and self.click_lon is not None:
            self.tbl_crs.selectRow(0)

    def _select_epsg_in_table(self, epsg_code: str) -> None:
        norm = epsg_code.strip().upper()
        for r, item in enumerate(self.current_results):
            if item["epsg"] == norm:
                self.tbl_crs.selectRow(r)
                return

    def _on_crs_row_selected(self) -> None:
        rows = self.tbl_crs.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self.current_results):
            item = self.current_results[idx]
            self.selected_epsg = str(item["epsg"])
            bounds = item.get("bounds")
            b_str = f"Lon [{bounds[0]:.2f}, {bounds[2]:.2f}] | Lat [{bounds[1]:.2f}, {bounds[3]:.2f}]" if bounds else "S/D"
            self.lbl_crs_detail.setText(
                f"<b>{item['epsg']}</b> — {item['name']}<br>"
                f"<b>Área:</b> {item['area_name']}<br>"
                f"<b>Extensión:</b> {b_str}"
            )
            self._redraw_map()

    def _redraw_map(self) -> None:
        fig = self.map_canvas.figure
        fig.clear()
        ax = fig.add_subplot(111)
        ax.set_facecolor("#EFF6FF")

        # Dibujar grillas UTM cada 6°
        for lon_u in range(-180, 181, 6):
            ax.axvline(lon_u, color="#CBD5E1", linewidth=0.45, zorder=1)
        for lat_u in range(-60, 85, 16):
            ax.axhline(lat_u, color="#CBD5E1", linewidth=0.45, zorder=1)

        # Continentes offline
        for cont in self._geom.get("continents", []):
            pts = np.array(cont["coords"], dtype=float)
            ax.fill(pts[:, 0], pts[:, 1], facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.7, zorder=2)

        # Contorno destacado de Argentina y Fajas GK 1..7 (-73.5 a -52.5 cada 3°)
        arg_pts = self._geom.get("argentina_outline", [])
        if arg_pts:
            ap = np.array(arg_pts, dtype=float)
            ax.fill(ap[:, 0], ap[:, 1], facecolor="#DBEAFE", edgecolor="#1D4ED8", linewidth=1.0, zorder=3)

        faja_colors = ["#BFDBFE", "#93C5FD", "#BFDBFE", "#93C5FD", "#BFDBFE", "#93C5FD", "#BFDBFE"]
        for faja_idx in range(1, 8):
            w_lon = -73.5 + (faja_idx - 1) * 3.0
            rect_f = Rectangle((w_lon, -56.0), 3.0, 35.0, facecolor=faja_colors[faja_idx - 1], alpha=0.22,
                               edgecolor="#2563EB", linestyle="--", linewidth=0.6, zorder=2.5)
            ax.add_patch(rect_f)
            if self._xlim[1] - self._xlim[0] <= 60:
                ax.text(w_lon + 1.5, -54.5, f"F{faja_idx}", ha="center", va="bottom",
                        fontsize=7.5, fontweight="bold", color="#1E40AF", zorder=4)

        # Dibujar Area of Use del CRS seleccionado (estilo QGIS)
        sel_info = get_crs_details(self.selected_epsg) if self.selected_epsg else None
        if sel_info and sel_info.get("bounds"):
            w, s, e, n = sel_info["bounds"]
            box = Rectangle((w, s), max(0.5, e - w), max(0.5, n - s), facecolor="#EF4444", alpha=0.20,
                            edgecolor="#DC2626", linewidth=2.0, zorder=5, label=f"Área {sel_info['epsg']}")
            ax.add_patch(box)

        if self.click_lon is not None and self.click_lat is not None:
            ax.plot(self.click_lon, self.click_lat, marker="+", markersize=14, markeredgewidth=2.2, color="#DC2626", zorder=6)
            ax.plot(self.click_lon, self.click_lat, marker="o", markersize=6, color="#DC2626", zorder=6)

        ax.set_xlim(self._xlim)
        ax.set_ylim(self._ylim)
        ax.set_xlabel("Longitud [°]")
        ax.set_ylabel("Latitud [°]")
        ax.set_title("Clic en el Mapa para Filtrar Faja GK / Zona UTM", fontsize=10, fontweight="bold")
        fig.tight_layout()
        self.map_canvas.canvas.draw()

    def _accept_automatic(self) -> None:
        self.selected_epsg = ""
        self.accept()


class LLMExtractionWorker(QThread):
    """Hilo en segundo plano para ejecutar pre-escaneo e inferencia LLM pozo por pozo."""

    progress_sig = Signal(int, int, str)
    log_sig = Signal(str)
    well_ready_sig = Signal(str, object, object)  # (folder_name, WellExplorationResult, dfs_dict)
    finished_sig = Signal(list)  # failed list
    error_sig = Signal(str)

    def __init__(
        self,
        well_entries: List[Dict[str, object]],
        profile: FieldProfile,
        llm_config: LLMConfig,
        output_dir: Path,
        use_cache: bool,
        incremental: bool,
    ) -> None:
        super().__init__()
        self.well_entries = well_entries
        self.profile = profile
        self.llm_config = llm_config
        self.output_dir = output_dir
        self.use_cache = use_cache
        self.incremental = incremental

    def run(self) -> None:
        failed: List[str] = []
        total = len(self.well_entries)
        try:
            # Verificar cuáles requieren LLM
            explored_list: List[Tuple[str, Path, WellExplorationResult, Dict[str, pd.DataFrame]]] = []
            cached_meta: Dict[str, Dict[str, object]] = {}

            excel_path = self.output_dir / "well_metadata.xlsx"
            existing_wells = set()
            if self.incremental and excel_path.exists():
                try:
                    df_ex = pd.read_excel(excel_path)
                    existing_wells = set(df_ex["Well"].astype(str).tolist())
                except Exception:
                    pass

            for idx, entry in enumerate(self.well_entries, start=1):
                wdir = Path(entry["folder_path"])
                fname = str(entry["well_folder_name"])
                self.progress_sig.emit(idx, total, f"Leyendo {fname}...")
                try:
                    res, dfs = explore_well_directory(wdir, target_depth_unit=self.profile.target_depth_unit)
                    if res.las_files_count == 0:
                        raise ValueError("Sin archivos .las válidos.")

                    cache_file = self.output_dir / "metadata_cache" / f"{fname}_metadata.json"
                    if self.use_cache and cache_file.exists():
                        data = json.loads(cache_file.read_text(encoding="utf-8"))
                        if isinstance(data.get("metadata"), dict):
                            cached_meta[fname] = data["metadata"]
                            self.log_sig.emit(f"[CACHÉ] Metadatos cargados para {fname}.")

                    if self.incremental and (fname in existing_wells or res.well_folder_name in existing_wells):
                        if fname in cached_meta:
                            norm_m = normalize_well_metadata(cached_meta[fname], profile=self.profile, fallback_well_name=fname)
                            norm_m["_extraction_mode"] = "CACHÉ INCREMENTAL (sin invocar LLM)"
                            res.llm_metadata = norm_m
                            self.well_ready_sig.emit(fname, res, dfs)
                            self.log_sig.emit(f"[INCREMENTAL] {fname} ya curado; cargado desde caché.")
                            continue

                    explored_list.append((fname, wdir, res, dfs))
                except Exception as exc:
                    failed.append(f"{fname}: {exc}")
                    self.log_sig.emit(f"[ERROR lectura] {fname}: {exc}")

            need_llm_items = [item for item in explored_list if item[0] not in cached_meta]
            cache_only_items = [item for item in explored_list if item[0] in cached_meta]

            for fname, _, res, dfs in cache_only_items:
                norm_m = normalize_well_metadata(cached_meta[fname], profile=self.profile, fallback_well_name=fname)
                norm_m["_extraction_mode"] = "CACHÉ (sin invocar LLM)"
                res.llm_metadata = norm_m
                self.well_ready_sig.emit(fname, res, dfs)

            if need_llm_items:
                self.log_sig.emit(f"Iniciando servidor / cliente LLM ({self.llm_config.provider}: {self.llm_config.model_name})...")
                with ManagedLLMSession(self.llm_config) as session:
                    for idx, (fname, _, res, dfs) in enumerate(need_llm_items, start=1):
                        self.progress_sig.emit(idx, len(need_llm_items), f"Extrayendo con LLM ({self.llm_config.model_name}): {fname}...")
                        try:
                            meta = session.extract_well_metadata(
                                consolidated_header_block=res.consolidated_header_block,
                                field_profile=self.profile,
                                fallback_well_name=fname,
                            )
                            meta["_extraction_mode"] = (
                                f"LLM EN VIVO ({self.llm_config.provider}: {self.llm_config.model_name}, "
                                f"{meta.get('_inference_time_s', '?')} s)"
                            )
                            res.llm_metadata = meta
                            self.well_ready_sig.emit(fname, res, dfs)
                            self.log_sig.emit(
                                f"[LLM OK] {fname} -> {meta.get('nombre_pozo')} | UWI={meta.get('uwi')} | "
                                f"CRS={meta.get('crs_used')} ({meta.get('_inference_time_s', 0)}s)"
                            )
                        except Exception as exc:
                            failed.append(f"{fname}: {exc}")
                            self.log_sig.emit(f"[ERROR LLM] {fname}: {exc}")

            self.finished_sig.emit(failed)
        except Exception as exc:
            self.error_sig.emit(str(exc))


class ExportWorker(QThread):
    """Hilo en segundo plano para exportar pozos revisados hacia `output/`."""

    progress_sig = Signal(int, int, str)
    log_sig = Signal(str)
    exported_sig = Signal(str, object)
    finished_sig = Signal(list)

    def __init__(
        self,
        items: List[Tuple[str, WellExplorationResult, Dict[str, pd.DataFrame], Dict[str, Literal["keep_all", "merge"]], bool]],
        output_dir: Path,
        global_repeat_strategy: Literal["keep_all", "merge"],
        profile: FieldProfile,
        target_grid_step: Optional[float] = None,
        target_grid_start: Optional[float] = None,
        target_grid_stop: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.items = items
        self.output_dir = output_dir
        self.global_repeat_strategy = global_repeat_strategy
        self.profile = profile
        self.target_grid_step = target_grid_step
        self.target_grid_start = target_grid_start
        self.target_grid_stop = target_grid_stop

    def run(self) -> None:
        summaries: List[WellExportSummary] = []
        total = len(self.items)
        for idx, (fname, res, dfs, per_curve, manual_edit) in enumerate(self.items, start=1):
            self.progress_sig.emit(idx, total, f"Exportando {res.llm_metadata.get('nombre_pozo', fname)}...")
            try:
                ws = export_well_to_output(
                    well=res,
                    well_dfs=dfs,
                    output_dir=self.output_dir,
                    repeat_strategy=self.global_repeat_strategy,
                    per_curve_repeat_strategy=per_curve,
                    update_excel=True,
                    field_profile=self.profile,
                    save_metadata_cache=True,
                    manual_override=manual_edit,
                    target_grid_step=self.target_grid_step,
                    target_grid_start=self.target_grid_start,
                    target_grid_stop=self.target_grid_stop,
                )
                summaries.append(ws)
                self.exported_sig.emit(fname, ws)
                self.log_sig.emit(
                    f"[EXPORTADO] {ws.well_name} -> {ws.depth_csv_path.name} "
                    f"(step={ws.grid_step}m, {ws.total_grid_rows} filas, {len(ws.exported_depth_curves)} curvas)"
                )
            except Exception as exc:
                self.log_sig.emit(f"[ERROR exportación] {fname}: {exc}")
        self.finished_sig.emit(summaries)


class WellCuratorMainWindow(QMainWindow):
    """Ventana principal de Well Curator Studio (Spec 004)."""

    def __init__(self, initial_output_dir: Optional[Path] = None) -> None:
        super().__init__()
        self.setWindowTitle("Well Curator Studio — Curado de Perfiles .LAS")
        self.resize(1380, 860)

        self.output_dir = initial_output_dir or (PROJECT_ROOT / "output")
        self.current_project_path: Optional[Path] = None
        self.project_manual_wells: List[Dict[str, object]] = []
        self.batch_extra_roots: List[Path] = []
        self.discovered_wells: List[Dict[str, object]] = []
        self.prescanned_wells: Dict[str, Tuple[WellExplorationResult, Dict[str, pd.DataFrame]]] = {}
        self.llm_wells: Dict[str, Tuple[WellExplorationResult, Dict[str, pd.DataFrame]]] = {}
        self.manual_edited_wells: set[str] = set()
        self.per_curve_overrides: Dict[str, Dict[str, Literal["keep_all", "merge"]]] = {}
        self.export_summaries: Dict[str, WellExportSummary] = {}

        self._llm_worker: Optional[LLMExtractionWorker] = None
        self._export_worker: Optional[ExportWorker] = None
        self._populating_review_table = False

        self._build_ui()
        self._init_empty_project()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)

        # Banner superior estilo software comercial
        banner = QFrame()
        banner.setObjectName("TopBanner")
        banner.setFixedHeight(58)
        b_layout = QHBoxLayout(banner)
        b_layout.setContentsMargins(16, 6, 16, 6)

        title_col = QVBoxLayout()
        lbl_title = QLabel("WELL CURATOR STUDIO")
        lbl_title.setObjectName("BannerTitle")
        lbl_sub = QLabel("Curado Petrofísico Multi-Campo • Encabezados Puros a LLM + Curvas lasio • Grilla Común & Geodesia pyproj")
        lbl_sub.setObjectName("BannerSubtitle")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)

        self.badge_llm = QLabel("LLM: llama_cpp_local")
        self.badge_llm.setObjectName("StatusBadge")
        self.badge_profile = QLabel("Campo: generic")
        self.badge_profile.setObjectName("StatusBadge")
        self.badge_output = QLabel(f"Salida: {self.output_dir.name}/")
        self.badge_output.setObjectName("StatusBadge")

        b_layout.addLayout(title_col)
        b_layout.addStretch()
        b_layout.addWidget(self.badge_llm)
        b_layout.addWidget(self.badge_profile)
        b_layout.addWidget(self.badge_output)
        root_layout.addWidget(banner)

        # Barra de gestión de proyectos (Nuevo / Abrir / Guardar)
        proj_bar = QFrame()
        proj_bar.setObjectName("ProjectBar")
        p_layout = QHBoxLayout(proj_bar)
        p_layout.setContentsMargins(14, 5, 14, 5)

        self.lbl_project_name = QLabel("Proyecto: Nuevo Proyecto (Vacío)")
        self.lbl_project_name.setStyleSheet("font-weight: bold; color: #0F172A;")
        btn_new_proj = QPushButton("Nuevo Proyecto")
        btn_new_proj.clicked.connect(self._new_project_clicked)
        btn_open_proj = QPushButton("Abrir Proyecto...")
        btn_open_proj.clicked.connect(self._open_project_clicked)
        btn_save_proj = QPushButton("Guardar Proyecto...")
        btn_save_proj.setObjectName("PrimaryBtn")
        btn_save_proj.clicked.connect(self._save_project_clicked)

        p_layout.addWidget(self.lbl_project_name)
        p_layout.addStretch()
        p_layout.addWidget(btn_new_proj)
        p_layout.addWidget(btn_open_proj)
        p_layout.addWidget(btn_save_proj)
        root_layout.addWidget(proj_bar)

        # Contenedor principal de pestañas
        self.tabs = QTabWidget()
        self.tabs.setContentsMargins(8, 4, 8, 8)
        root_layout.addWidget(self.tabs)

        self._build_tab1_import()
        self._build_tab2_config()
        self._build_tab3_process_review()
        self._build_tab4_validation_viz()
        self._build_tab5_outputs()

    # ------------------------------------------------------------------
    # PESTAÑA 1: IMPORTAR POZOS (DRAG & DROP + DIRECTORIO BATCH)
    # ------------------------------------------------------------------
    def _build_tab1_import(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)

        top_split = QHBoxLayout()

        # Izquierda: Añadir Pozo Individual (Drag & Drop)
        grp_drop = QGroupBox("Añadir Pozo (Drag && Drop de Carpeta, Archivos .LAS o .ZIP)")
        drop_layout = QVBoxLayout(grp_drop)

        self.drop_zone = DropZoneWidget()
        self.drop_zone.paths_dropped.connect(self._handle_dropped_paths)
        drop_layout.addWidget(self.drop_zone)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nombre del pozo destino:"))
        self.txt_target_well_name = QLineEdit()
        self.txt_target_well_name.setPlaceholderText("Auto-detectado al soltar o ingresar manualmente (ej. YPF.SC.CVa-99)")
        name_row.addWidget(self.txt_target_well_name)
        drop_layout.addLayout(name_row)

        btn_row = QHBoxLayout()
        btn_add_files = QPushButton("Seleccionar Archivos .LAS...")
        btn_add_files.clicked.connect(self._browse_single_las_files)
        btn_add_zip = QPushButton("Importar .ZIP...")
        btn_add_zip.clicked.connect(self._browse_zip_file)
        btn_add_folder = QPushButton("Importar Carpeta de Pozo...")
        btn_add_folder.clicked.connect(self._browse_single_well_folder)
        btn_row.addWidget(btn_add_files)
        btn_row.addWidget(btn_add_zip)
        btn_row.addWidget(btn_add_folder)
        drop_layout.addLayout(btn_row)

        # Derecha: Fuentes Batch (Carpetas opcionales — desmarcadas por defecto en proyecto nuevo vacío)
        grp_batch = QGroupBox("Directorios de Pozos Activos (Opcional / Batch)")
        batch_layout = QVBoxLayout(grp_batch)

        self.chk_include_input = QCheckBox("Cargar pozos de ejemplo desde Input/ (Solo lectura)")
        self.chk_include_input.setChecked(False)
        self.chk_include_input.stateChanged.connect(self._refresh_discovered_wells)

        self.chk_include_imports = QCheckBox("Cargar historial de workspace_imports/")
        self.chk_include_imports.setChecked(False)
        self.chk_include_imports.stateChanged.connect(self._refresh_discovered_wells)

        imports_row = QHBoxLayout()
        imports_row.addWidget(self.chk_include_imports)
        btn_clear_imports = QPushButton("Vaciar workspace_imports/")
        btn_clear_imports.clicked.connect(self._clear_workspace_imports_clicked)
        imports_row.addWidget(btn_clear_imports)

        batch_layout.addWidget(self.chk_include_input)
        batch_layout.addLayout(imports_row)

        batch_btn_row = QHBoxLayout()
        btn_add_batch_dir = QPushButton("Añadir Carpeta Batch...")
        btn_add_batch_dir.clicked.connect(self._browse_batch_directory)
        btn_prescan = QPushButton("Pre-escanear Pozos Seleccionados (sin LLM)")
        btn_prescan.setObjectName("PrimaryBtn")
        btn_prescan.clicked.connect(self._run_prescan)
        batch_btn_row.addWidget(btn_add_batch_dir)
        batch_btn_row.addWidget(btn_prescan)
        batch_layout.addLayout(batch_btn_row)

        self.lbl_batch_roots = QLabel("Carpetas batch adicionales: ninguna")
        self.lbl_batch_roots.setStyleSheet("color: #64748B; font-size: 8.5pt;")
        batch_layout.addWidget(self.lbl_batch_roots)
        batch_layout.addStretch()

        top_split.addWidget(grp_drop, 3)
        top_split.addWidget(grp_batch, 2)
        layout.addLayout(top_split)

        # Tabla de pre-escaneo
        grp_table = QGroupBox("Pozos Detectados y Pre-escaneo Técnico (.LAS / Unidades / Repetidas)")
        t_layout = QVBoxLayout(grp_table)
        self.tbl_import = QTableWidget(0, 8)
        self.tbl_import.setHorizontalHeaderLabels([
            "Incluir", "Carpeta Pozo", "Origen", "Archivos .LAS",
            "Depth / Time (XPT)", "Unidades", "Curvas Únicas", "Repetidas (:2, :3)"
        ])
        self.tbl_import.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_import.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        t_layout.addWidget(self.tbl_import)
        layout.addWidget(grp_table, 1)

        self.tabs.addTab(tab, "1. Importar Pozos")

    # ------------------------------------------------------------------
    # PESTAÑA 2: CONFIGURACIÓN (LLM, CAMPO Y CURADO)
    # ------------------------------------------------------------------
    def _build_tab2_config(self) -> None:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)

        # Izquierda: Modelo LLM
        grp_llm = QGroupBox("Configuración del Modelo LLM (Agnóstico)")
        form_llm = QFormLayout(grp_llm)

        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(["llama_cpp_local", "openai_compatible_local", "cloud_api"])
        self.cmb_provider.currentTextChanged.connect(self._on_config_changed)

        self.cmb_gguf = QComboBox()
        ggufs = list_local_gguf_models()
        for g in ggufs:
            self.cmb_gguf.addItem(g.name, str(g))
        if not ggufs:
            self.cmb_gguf.addItem(DEFAULT_MODEL_PATH.name, str(DEFAULT_MODEL_PATH))

        self.cmb_gpu = QComboBox()
        self.cmb_gpu.addItems(["Vulkan1", "Vulkan0", "CPU"])

        self.spn_ctx = QSpinBox()
        self.spn_ctx.setRange(2048, 65536)
        self.spn_ctx.setSingleStep(2048)
        self.spn_ctx.setValue(16384)

        self.txt_base_url = QLineEdit("http://127.0.0.1:8089")
        self.txt_model_name = QLineEdit(DEFAULT_MODEL_PATH.name)
        self.txt_api_key = QLineEdit()
        self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_api_key.setPlaceholderText("Opcional (o usar variable LLM_API_KEY)")

        btn_test_conn = QPushButton("Probar Conexión con Modelo / Servidor")
        btn_test_conn.clicked.connect(self._test_llm_conn_clicked)
        self.lbl_conn_status = QLabel("Estado: sin probar")
        self.lbl_conn_status.setWordWrap(True)

        form_llm.addRow("Proveedor:", self.cmb_provider)
        form_llm.addRow("Archivo .GGUF (local):", self.cmb_gguf)
        form_llm.addRow("Dispositivo GPU:", self.cmb_gpu)
        form_llm.addRow("Contexto (tokens):", self.spn_ctx)
        form_llm.addRow("Base URL (Ollama/LM Studio/API):", self.txt_base_url)
        form_llm.addRow("Nombre de Modelo (API):", self.txt_model_name)
        form_llm.addRow("API Key:", self.txt_api_key)
        form_llm.addRow("", btn_test_conn)
        form_llm.addRow("", self.lbl_conn_status)

        # Derecha: Perfil de Campo, Geodesia y Curado
        grp_field = QGroupBox("Perfil de Campo Petrolero, Geodesia y Exportación")
        form_field = QFormLayout(grp_field)

        self.cmb_field_profile = QComboBox()
        profiles = list_field_profiles()
        self.cmb_field_profile.addItems(profiles)
        if "generic" in profiles:
            self.cmb_field_profile.setCurrentText("generic")
        self.cmb_field_profile.currentTextChanged.connect(self._on_config_changed)

        self.lbl_profile_desc = QLabel("")
        self.lbl_profile_desc.setWordWrap(True)
        self.lbl_profile_desc.setStyleSheet("color: #475569; font-size: 8.5pt;")

        crs_row = QHBoxLayout()
        self.cmb_force_epsg = QComboBox()
        self.cmb_force_epsg.setEditable(True)
        self.cmb_force_epsg.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cmb_force_epsg.addItem("Automático del perfil (priorizar GK / proyectar Lat-Lon)", "")
        for crs_item in list_popular_oilfield_crs():
            self.cmb_force_epsg.addItem(f"{crs_item['epsg']} — {crs_item['name']}", crs_item["epsg"])
        completer = QCompleter(self.cmb_force_epsg.model(), self.cmb_force_epsg)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.cmb_force_epsg.setCompleter(completer)
        self.cmb_force_epsg.currentIndexChanged.connect(self._on_config_changed)

        btn_crs_map = QPushButton("Mapa Mundi CRS (QGIS)...")
        btn_crs_map.setObjectName("PrimaryBtn")
        btn_crs_map.clicked.connect(self._open_crs_map_selector)
        crs_row.addWidget(self.cmb_force_epsg, 1)
        crs_row.addWidget(btn_crs_map)

        self.cmb_repeat_strategy = QComboBox()
        self.cmb_repeat_strategy.addItem("Conservar todas con sufijo (:2, :3, :4) [keep_all]", "keep_all")
        self.cmb_repeat_strategy.addItem("Fusionar corridas en una sola curva [merge]", "merge")

        self.chk_incremental = QCheckBox("Modo Incremental (saltear pozos ya presentes en output/)")
        self.chk_use_cache = QCheckBox("Omitir LLM usando Caché si existe (output/metadata_cache/)")
        self.chk_use_cache.setChecked(False)

        out_row = QHBoxLayout()
        self.txt_output_dir = QLineEdit(str(self.output_dir))
        self.txt_output_dir.editingFinished.connect(
            lambda: self.set_output_dir(Path(self.txt_output_dir.text().strip() or str(PROJECT_ROOT / "output")))
        )
        btn_out_dir = QPushButton("...")
        btn_out_dir.setFixedWidth(36)
        btn_out_dir.clicked.connect(self._browse_output_dir)
        out_row.addWidget(self.txt_output_dir)
        out_row.addWidget(btn_out_dir)

        form_field.addRow("Perfil de Campo:", self.cmb_field_profile)
        form_field.addRow("Descripción:", self.lbl_profile_desc)
        form_field.addRow("Proyección / Sistema CRS:", crs_row)
        form_field.addRow("Curvas Repetidas (global):", self.cmb_repeat_strategy)
        form_field.addRow("", self.chk_incremental)
        form_field.addRow("", self.chk_use_cache)
        form_field.addRow("Carpeta Salida (output/):", out_row)

        layout.addWidget(grp_llm, 1)
        layout.addWidget(grp_field, 1)
        self.tabs.addTab(tab, "2. Configuración y LLM")

    # ------------------------------------------------------------------
    # PESTAÑA 3: PROCESAMIENTO, REVISIÓN Y REPORTE ESCRITO DEL LLM
    # ------------------------------------------------------------------
    def _build_tab3_process_review(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)

        # Barra de acciones
        bar = QHBoxLayout()
        self.btn_run_llm = QPushButton("1. Ejecutar Extracción con LLM")
        self.btn_run_llm.setObjectName("PrimaryBtn")
        self.btn_run_llm.clicked.connect(self._start_llm_extraction)

        self.btn_export_output = QPushButton("2. Confirmar Revisión y Exportar a output/")
        self.btn_export_output.setObjectName("SuccessBtn")
        self.btn_export_output.clicked.connect(self._start_export)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.lbl_progress = QLabel("Listo.")

        bar.addWidget(self.btn_run_llm)
        bar.addWidget(self.btn_export_output)
        bar.addWidget(self.progress_bar, 1)
        bar.addWidget(self.lbl_progress)
        layout.addLayout(bar)

        # Panel de regularización de grilla común pre-exportación y carpeta de salida (RF-9)
        grp_pre_export = QGroupBox("Regularización de Grilla Común (Pre-Exportación) y Carpeta de Salida")
        pre_layout = QVBoxLayout(grp_pre_export)
        pre_layout.setContentsMargins(10, 6, 10, 6)
        pre_layout.setSpacing(6)

        grid_row = QHBoxLayout()
        grid_row.addWidget(QLabel("Modo Grilla:"))
        self.cmb_grid_mode = QComboBox()
        self.cmb_grid_mode.addItem("Por pozo (paso más chico individual)", "per_well")
        self.cmb_grid_mode.addItem("Mismo Paso Común (interpola al paso elegido)", "common_step")
        self.cmb_grid_mode.addItem("Grilla Global Idéntica (mismo Start, Stop y Step)", "global_grid")
        self.cmb_grid_mode.currentIndexChanged.connect(self._on_grid_option_changed)
        grid_row.addWidget(self.cmb_grid_mode, 2)

        grid_row.addWidget(QLabel("Paso [m]:"))
        self.cmb_grid_step_preset = QComboBox()
        self.cmb_grid_step_preset.addItem("Auto (menor del lote)", None)
        self.cmb_grid_step_preset.addItem("0.0762 m (1/4 ft)", 0.0762)
        self.cmb_grid_step_preset.addItem("0.1000 m (10 cm)", 0.10)
        self.cmb_grid_step_preset.addItem("0.1524 m (1/2 ft)", 0.1524)
        self.cmb_grid_step_preset.addItem("0.2000 m (20 cm)", 0.20)
        self.cmb_grid_step_preset.addItem("0.3048 m (1 ft)", 0.3048)
        self.cmb_grid_step_preset.addItem("0.5000 m", 0.50)
        self.cmb_grid_step_preset.addItem("1.0000 m", 1.00)
        self.cmb_grid_step_preset.addItem("Personalizado...", "custom")
        self.cmb_grid_step_preset.currentIndexChanged.connect(self._on_grid_option_changed)
        grid_row.addWidget(self.cmb_grid_step_preset, 1)

        self.spn_custom_grid_step = QDoubleSpinBox()
        self.spn_custom_grid_step.setRange(0.0050, 50.0)
        self.spn_custom_grid_step.setDecimals(4)
        self.spn_custom_grid_step.setSingleStep(0.05)
        self.spn_custom_grid_step.setValue(0.1524)
        self.spn_custom_grid_step.setSuffix(" m")
        self.spn_custom_grid_step.setEnabled(False)
        self.spn_custom_grid_step.valueChanged.connect(self._update_depth_grid_preview)
        grid_row.addWidget(self.spn_custom_grid_step)

        self.btn_preview_grid = QPushButton("Calcular Grilla")
        self.btn_preview_grid.clicked.connect(self._update_depth_grid_preview)
        grid_row.addWidget(self.btn_preview_grid)
        pre_layout.addLayout(grid_row)

        out_pre_row = QHBoxLayout()
        self.lbl_grid_preview = QLabel("Grilla: Por pozo (paso nativo más chico de cada pozo, sin extrapolar).")
        self.lbl_grid_preview.setStyleSheet("color: #334155; font-size: 8.5pt;")
        out_pre_row.addWidget(self.lbl_grid_preview, 1)

        out_pre_row.addWidget(QLabel("Exportar en:"))
        self.txt_export_output_dir = QLineEdit(str(self.output_dir))
        self.txt_export_output_dir.setMinimumWidth(260)
        self.txt_export_output_dir.editingFinished.connect(
            lambda: self.set_output_dir(Path(self.txt_export_output_dir.text().strip() or str(PROJECT_ROOT / "output")))
        )
        btn_export_out_browse = QPushButton("Elegir Carpeta...")
        btn_export_out_browse.clicked.connect(self._browse_output_dir)
        out_pre_row.addWidget(self.txt_export_output_dir)
        out_pre_row.addWidget(btn_export_out_browse)
        pre_layout.addLayout(out_pre_row)

        layout.addWidget(grp_pre_export)

        main_split = QSplitter(Qt.Orientation.Vertical)

        # Tabla editable de revisión supervisada + banner de alertas de integridad
        grp_rev = QGroupBox("Revisión Supervisada de Metadatos Normalizados (Doble clic para editar antes de exportar)")
        rev_layout = QVBoxLayout(grp_rev)

        self.lbl_review_warning = QLabel("")
        self.lbl_review_warning.setWordWrap(True)
        self.lbl_review_warning.setStyleSheet(
            "background: #FEF3C7; color: #92400E; border: 1px solid #F59E0B; "
            "border-radius: 4px; padding: 6px; font-weight: bold;"
        )
        self.lbl_review_warning.setVisible(False)
        rev_layout.addWidget(self.lbl_review_warning)

        self.tbl_review = QTableWidget(0, 9)
        self.tbl_review.setHorizontalHeaderLabels([
            "Carpeta", "Pozo (Editable)", "UWI (Editable)",
            "X Este (Editable)", "Y Norte (Editable)", "Cota [m]",
            "CRS", "Step [m]", "Rep."
        ])
        self.tbl_review.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col_fit in (6, 7, 8):
            self.tbl_review.horizontalHeader().setSectionResizeMode(col_fit, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_review.itemSelectionChanged.connect(self._on_review_row_selected)
        self.tbl_review.itemChanged.connect(self._on_review_cell_edited)
        rev_layout.addWidget(self.tbl_review)
        main_split.addWidget(grp_rev)

        # Panel inferior: Reporte escrito LLM + Control por curva repetida + Consola
        bottom_split = QSplitter(Qt.Orientation.Horizontal)

        grp_report = QGroupBox("Reporte Explicativo del LLM sobre el Pozo Seleccionado")
        rep_layout = QVBoxLayout(grp_report)
        self.txt_llm_report = QPlainTextEdit()
        self.txt_llm_report.setReadOnly(True)
        self.txt_llm_report.setPlaceholderText("Seleccioná un pozo procesado para leer la explicación del LLM sobre qué regularizó...")
        rep_layout.addWidget(self.txt_llm_report)

        grp_rep_curves = QGroupBox("Decisión por Curva Repetida")
        rc_layout = QVBoxLayout(grp_rep_curves)
        self.tbl_repeated_curves = QTableWidget(0, 3)
        self.tbl_repeated_curves.setHorizontalHeaderLabels(["Curva", "Ocurrencias (.LAS)", "Tratamiento"])
        self.tbl_repeated_curves.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rc_layout.addWidget(self.tbl_repeated_curves)

        grp_console = QGroupBox("Registro de Ejecución en Vivo")
        c_layout = QVBoxLayout(grp_console)
        self.txt_console = QPlainTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.setFont(QFont("Consolas", 8.5))
        c_layout.addWidget(self.txt_console)

        bottom_split.addWidget(grp_report)
        bottom_split.addWidget(grp_rep_curves)
        bottom_split.addWidget(grp_console)
        main_split.addWidget(bottom_split)

        main_split.setSizes([330, 290])
        layout.addWidget(main_split)

        self.tabs.addTab(tab, "3. Procesamiento y Reporte LLM")

    # ------------------------------------------------------------------
    # PESTAÑA 4: VALIDACIÓN, MAPA GEOGRÁFICO Y VISOR DE LOGS
    # ------------------------------------------------------------------
    def _build_tab4_validation_viz(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        top_bar = QHBoxLayout()
        btn_refresh_viz = QPushButton("Actualizar Mapa y Gráficos desde Datos Actuales / output/")
        btn_refresh_viz.setObjectName("PrimaryBtn")
        btn_refresh_viz.clicked.connect(self._refresh_all_visualizations)
        top_bar.addWidget(btn_refresh_viz)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.subtabs_viz = QTabWidget()
        layout.addWidget(self.subtabs_viz)

        # Subtab 4.1: Mapa Geográfico & Comparación de Nombres
        map_tab = QWidget()
        map_layout = QHBoxLayout(map_tab)

        map_left = QVBoxLayout()
        if HAS_WEBENGINE and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            self.web_map_view = QWebEngineView()
            map_left.addWidget(self.web_map_view, 1)
        else:
            self.web_map_view = None
            self.canvas_map_fallback = MplCanvasWidget(figsize=(7, 5.5))
            map_left.addWidget(self.canvas_map_fallback, 1)

        btn_open_map_browser = QPushButton("Abrir Mapa Interactivo en Navegador Externo")
        btn_open_map_browser.clicked.connect(self._open_map_in_browser)
        map_left.addWidget(btn_open_map_browser)

        grp_names = QGroupBox("Normalización Geodésica y de Nombres")
        names_layout = QVBoxLayout(grp_names)
        self.tbl_map_names = QTableWidget(0, 6)
        self.tbl_map_names.setHorizontalHeaderLabels(["Origen", "Normalizado", "UWI", "CRS", "Lat", "Lon"])
        self.tbl_map_names.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_map_names.horizontalHeader().setStretchLastSection(True)
        names_layout.addWidget(self.tbl_map_names)

        map_layout.addLayout(map_left, 5)
        map_layout.addWidget(grp_names, 4)
        self.subtabs_viz.addTab(map_tab, "Mapa Geográfico y Nombres")

        # Subtab 4.2: Visor Interactivo de Logs (hasta 6 tracks)
        logs_tab = QWidget()
        logs_layout = QHBoxLayout(logs_tab)

        ctrl_panel = QGroupBox("Controles del Visor de Registros (máx. 6)")
        ctrl_panel.setFixedWidth(320)
        cp_layout = QVBoxLayout(ctrl_panel)

        cp_layout.addWidget(QLabel("Pozo:"))
        self.cmb_log_well = QComboBox()
        self.cmb_log_well.currentTextChanged.connect(self._on_log_well_changed)
        cp_layout.addWidget(self.cmb_log_well)

        self.chk_overlay_csv = QCheckBox("Superponer Crudo vs CSV Grilla")
        self.chk_overlay_csv.setChecked(True)
        self.chk_overlay_csv.stateChanged.connect(self._plot_selected_well_logs)
        cp_layout.addWidget(self.chk_overlay_csv)

        cp_layout.addWidget(QLabel("Curvas disponibles (tildar hasta 6):"))
        self.lst_log_curves = QListWidget()
        self.lst_log_curves.itemChanged.connect(self._plot_selected_well_logs)
        cp_layout.addWidget(self.lst_log_curves, 1)

        btn_plot_logs = QPushButton("Graficar Curvas")
        btn_plot_logs.setObjectName("PrimaryBtn")
        btn_plot_logs.clicked.connect(self._plot_selected_well_logs)
        cp_layout.addWidget(btn_plot_logs)

        self.canvas_logs = MplCanvasWidget(figsize=(9.5, 6.5))
        logs_layout.addWidget(ctrl_panel)
        logs_layout.addWidget(self.canvas_logs, 1)
        self.subtabs_viz.addTab(logs_tab, "Visor de Registros (Logs)")

        # Subtab 4.3: Cobertura de Profundidad y Estaciones XPT
        cov_tab = QWidget()
        cov_layout = QHBoxLayout(cov_tab)
        self.canvas_coverage = MplCanvasWidget(figsize=(6.5, 5.5))
        self.canvas_xpt = MplCanvasWidget(figsize=(6.5, 5.5))
        cov_layout.addWidget(self.canvas_coverage)
        cov_layout.addWidget(self.canvas_xpt)
        self.subtabs_viz.addTab(cov_tab, "Cobertura y Estaciones Tiempo (XPT)")

        # Subtab 4.4: Matriz de Disponibilidad Excel
        mat_tab = QWidget()
        mat_layout = QVBoxLayout(mat_tab)
        self.canvas_matrix = MplCanvasWidget(figsize=(11, 5.2))
        mat_layout.addWidget(self.canvas_matrix)
        self.subtabs_viz.addTab(mat_tab, "Matriz Maestra Excel (yes/no)")

        self.tabs.addTab(tab, "4. Validación, Mapa y Logs")

    # ------------------------------------------------------------------
    # PESTAÑA 5: SALIDAS Y EXPLORADOR DE OUTPUT/
    # ------------------------------------------------------------------
    def _build_tab5_outputs(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Carpeta output/ seleccionada:"))
        self.txt_tab5_output_dir = QLineEdit(str(self.output_dir))
        self.txt_tab5_output_dir.editingFinished.connect(
            lambda: self.set_output_dir(Path(self.txt_tab5_output_dir.text().strip() or str(PROJECT_ROOT / "output")))
        )
        btn_pick_tab5_out = QPushButton("Seleccionar Carpeta output/...")
        btn_pick_tab5_out.clicked.connect(self._browse_output_dir)
        dir_row.addWidget(self.txt_tab5_output_dir, 1)
        dir_row.addWidget(btn_pick_tab5_out)
        layout.addLayout(dir_row)

        top_row = QHBoxLayout()
        self.lbl_output_summary = QLabel("Sin inventario cargado.")
        self.lbl_output_summary.setStyleSheet("font-weight: bold;")
        btn_reload_out = QPushButton("Refrescar output/")
        btn_reload_out.clicked.connect(self._refresh_output_tab)
        btn_open_out = QPushButton("Abrir Carpeta output/")
        btn_open_out.setObjectName("PrimaryBtn")
        btn_open_out.clicked.connect(self._open_output_folder)
        top_row.addWidget(self.lbl_output_summary, 1)
        top_row.addWidget(btn_reload_out)
        top_row.addWidget(btn_open_out)
        layout.addLayout(top_row)

        out_tabs = QTabWidget()
        layout.addWidget(out_tabs)

        # Excel tabla maestra
        self.tbl_out_excel = QTableWidget(0, 0)
        out_tabs.addTab(self.tbl_out_excel, "well_metadata.xlsx")

        # CSVs por pozo
        csv_tab = QWidget()
        csv_l = QVBoxLayout(csv_tab)
        csv_sel_row = QHBoxLayout()
        csv_sel_row.addWidget(QLabel("Archivo CSV de pozo:"))
        self.cmb_out_csv = QComboBox()
        self.cmb_out_csv.currentTextChanged.connect(self._load_selected_output_csv)
        csv_sel_row.addWidget(self.cmb_out_csv, 1)
        csv_l.addLayout(csv_sel_row)
        self.tbl_out_csv = QTableWidget(0, 0)
        csv_l.addWidget(self.tbl_out_csv)
        out_tabs.addTab(csv_tab, "CSVs por Pozo (wells/)")

        # Resumen XPT
        self.tbl_out_xpt = QTableWidget(0, 0)
        out_tabs.addTab(self.tbl_out_xpt, "Registros en Tiempo (xpt_stations_summary.csv)")

        # Errores batch
        self.txt_out_errors = QPlainTextEdit()
        self.txt_out_errors.setReadOnly(True)
        out_tabs.addTab(self.txt_out_errors, "batch_errors.log")

        self.tabs.addTab(tab, "5. Salidas (output/)")

    # ------------------------------------------------------------------
    # LÓGICA DE PROYECTOS (VACÍO POR DEFECTO, GUARDAR / ABRIR) Y CRS
    # ------------------------------------------------------------------
    def _init_empty_project(self) -> None:
        """Inicializa la sesión como un proyecto nuevo 100% vacío sin pozos precargados (Spec 004 - RF-7)."""
        self.current_project_path = None
        self.project_manual_wells = []
        self.batch_extra_roots = []
        self.discovered_wells = []
        self.prescanned_wells = {}
        self.llm_wells = {}
        self.manual_edited_wells = set()
        self.per_curve_overrides = {}
        self.export_summaries = {}

        self.chk_include_input.blockSignals(True)
        self.chk_include_imports.blockSignals(True)
        self.chk_include_input.setChecked(False)
        self.chk_include_imports.setChecked(False)
        self.chk_include_input.blockSignals(False)
        self.chk_include_imports.blockSignals(False)

        self.lbl_project_name.setText("Proyecto: Nuevo Proyecto (Vacío)")
        self.lbl_batch_roots.setText("Carpetas batch adicionales: ninguna")
        self.tbl_import.setRowCount(0)
        self.tbl_review.setRowCount(0)
        self.tbl_repeated_curves.setRowCount(0)
        self.tbl_map_names.setRowCount(0)
        self.txt_llm_report.clear()
        self.txt_console.clear()
        self.cmb_log_well.clear()
        self.lst_log_curves.clear()
        self.progress_bar.setValue(0)
        self.lbl_progress.setText("Proyecto nuevo vacío. Arrastrá pozos o abrí un proyecto.")

        self.tbl_out_excel.setRowCount(0)
        self.tbl_out_excel.setColumnCount(0)
        self.tbl_out_csv.setRowCount(0)
        self.tbl_out_csv.setColumnCount(0)
        self.tbl_out_xpt.setRowCount(0)
        self.tbl_out_xpt.setColumnCount(0)
        self.cmb_out_csv.clear()
        self.txt_out_errors.clear()
        self.lbl_output_summary.setText("Proyecto nuevo vacío (sin exportaciones aún en esta sesión).")

        self.cmb_grid_mode.setCurrentIndex(0)
        self.cmb_grid_step_preset.setCurrentIndex(0)
        self.spn_custom_grid_step.setEnabled(False)
        self._update_depth_grid_preview()

        self._on_config_changed()

    def _new_project_clicked(self) -> None:
        self._init_empty_project()

    def _save_project_clicked(self) -> None:
        default_dir = str(PROJECT_ROOT / "projects")
        Path(default_dir).mkdir(parents=True, exist_ok=True)
        fpath, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar Proyecto Well Curator",
            str(self.current_project_path or (Path(default_dir) / "mi_proyecto.wellcurator.json")),
            "Proyectos Well Curator (*.wellcurator.json *.json)",
        )
        if fpath:
            saved = self.save_project_to_path(Path(fpath))
            QMessageBox.information(self, "Proyecto Guardado", f"Proyecto guardado en:\n{saved}")

    def save_project_to_path(self, project_path: Path) -> Path:
        wells_state: List[Dict[str, object]] = []
        included_map: Dict[str, bool] = {}
        for r_idx, entry in enumerate(self.discovered_wells):
            chk = self.tbl_import.item(r_idx, 0)
            included_map[str(entry["well_folder_name"])] = bool(chk and chk.checkState() == Qt.CheckState.Checked)

        for entry in self.discovered_wells:
            fname = str(entry["well_folder_name"])
            llm_meta = None
            if fname in self.llm_wells:
                llm_meta = self.llm_wells[fname][0].llm_metadata
            wells_state.append({
                "well_folder_name": fname,
                "folder_path": str(entry["folder_path"]),
                "source": str(entry.get("source", "")),
                "las_count": int(entry.get("las_count", 0)),
                "included": included_map.get(fname, True),
                "llm_metadata": llm_meta,
                "manual_edited": fname in self.manual_edited_wells,
                "per_curve_overrides": self.per_curve_overrides.get(fname, {}),
            })

        cfg = self._build_llm_config()
        state = {
            "project_name": project_path.stem.replace(".wellcurator", ""),
            "output_dir": str(self.output_dir),
            "field_profile": self.cmb_field_profile.currentText() or "generic",
            "forced_epsg": self._selected_forced_epsg(),
            "repeat_strategy": self.cmb_repeat_strategy.currentData() or "keep_all",
            "grid_mode": self.cmb_grid_mode.currentData() or "per_well",
            "target_grid_step": self._selected_target_grid_step(),
            "incremental": self.chk_incremental.isChecked(),
            "use_cache": self.chk_use_cache.isChecked(),
            "include_input": self.chk_include_input.isChecked(),
            "include_imports": self.chk_include_imports.isChecked(),
            "batch_extra_roots": [str(p) for p in self.batch_extra_roots],
            "llm_config": {
                "provider": cfg.provider,
                "model_name": cfg.model_name,
                "base_url": cfg.base_url,
                "gpu_device": cfg.gpu_device,
                "ctx_size": cfg.ctx_size,
                "model_path": str(cfg.model_path),
            },
            "wells": wells_state,
        }
        saved = save_project_file(project_path, state)
        self.current_project_path = saved
        self.lbl_project_name.setText(f"Proyecto: {saved.name}")
        return saved

    def _open_project_clicked(self) -> None:
        default_dir = str(PROJECT_ROOT / "projects")
        fpath, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir Proyecto Well Curator",
            default_dir if Path(default_dir).exists() else str(PROJECT_ROOT),
            "Proyectos Well Curator (*.wellcurator.json *.json)",
        )
        if fpath:
            try:
                self.load_project_from_path(Path(fpath))
            except Exception as exc:
                QMessageBox.warning(self, "Error al abrir proyecto", str(exc))

    def load_project_from_path(self, project_path: Path) -> None:
        data = load_project_file(project_path)
        self._init_empty_project()
        self.current_project_path = project_path

        out_str = str(data.get("output_dir") or "")
        if out_str:
            self.set_output_dir(Path(out_str), refresh_ui=False)

        prof_name = str(data.get("field_profile") or "generic")
        if self.cmb_field_profile.findText(prof_name) >= 0:
            self.cmb_field_profile.setCurrentText(prof_name)
        self._set_forced_epsg(str(data.get("forced_epsg") or ""))

        rep_s = str(data.get("repeat_strategy") or "keep_all")
        self.cmb_repeat_strategy.setCurrentIndex(0 if rep_s == "keep_all" else 1)

        g_mode = str(data.get("grid_mode") or "per_well")
        g_idx = self.cmb_grid_mode.findData(g_mode)
        self.cmb_grid_mode.setCurrentIndex(g_idx if g_idx >= 0 else 0)
        g_step = data.get("target_grid_step")
        if g_step is None:
            self.cmb_grid_step_preset.setCurrentIndex(0)
        else:
            step_f = float(g_step)
            matched_idx = -1
            for i in range(self.cmb_grid_step_preset.count()):
                val = self.cmb_grid_step_preset.itemData(i)
                if isinstance(val, (int, float)) and abs(float(val) - step_f) < 1e-4:
                    matched_idx = i
                    break
            if matched_idx >= 0:
                self.cmb_grid_step_preset.setCurrentIndex(matched_idx)
            else:
                cust_idx = self.cmb_grid_step_preset.findData("custom")
                self.cmb_grid_step_preset.setCurrentIndex(cust_idx if cust_idx >= 0 else 0)
                self.spn_custom_grid_step.setValue(step_f)

        self.chk_incremental.setChecked(bool(data.get("incremental", False)))
        self.chk_use_cache.setChecked(bool(data.get("use_cache", False)))

        llm_d = data.get("llm_config") or {}
        if llm_d.get("provider"):
            self.cmb_provider.setCurrentText(str(llm_d["provider"]))
        if llm_d.get("base_url"):
            self.txt_base_url.setText(str(llm_d["base_url"]))
        if llm_d.get("model_name"):
            self.txt_model_name.setText(str(llm_d["model_name"]))
        if llm_d.get("gpu_device"):
            self.cmb_gpu.setCurrentText(str(llm_d["gpu_device"]))
        if llm_d.get("ctx_size"):
            self.spn_ctx.setValue(int(llm_d["ctx_size"]))

        self.batch_extra_roots = [Path(p) for p in data.get("batch_extra_roots", []) if Path(p).exists()]
        if self.batch_extra_roots:
            self.lbl_batch_roots.setText("Carpetas batch adicionales: " + ", ".join(r.name for r in self.batch_extra_roots))

        for w in data.get("wells", []):
            wpath = Path(str(w.get("folder_path", "")))
            if wpath.exists() and wpath.is_dir():
                las_n = len([f for f in wpath.iterdir() if f.is_file() and f.suffix.lower() == ".las"])
                fname = str(w.get("well_folder_name") or wpath.name)
                self.project_manual_wells.append({
                    "well_folder_name": fname,
                    "folder_path": wpath,
                    "source": str(w.get("source") or "Proyecto"),
                    "las_count": las_n,
                })
                self.per_curve_overrides[fname] = dict(w.get("per_curve_overrides") or {})
                if w.get("manual_edited"):
                    self.manual_edited_wells.add(fname)

        self._refresh_discovered_wells()
        self._run_prescan()

        # Restaurar metadatos LLM guardados en el proyecto
        for w in data.get("wells", []):
            fname = str(w.get("well_folder_name") or "")
            if fname in self.prescanned_wells and isinstance(w.get("llm_metadata"), dict):
                res, dfs = self.prescanned_wells[fname]
                res.llm_metadata = dict(w["llm_metadata"])
                self.llm_wells[fname] = (res, dfs)

        if self.llm_wells:
            self._populate_review_table()
            self.tbl_review.selectRow(0)
            self._refresh_all_visualizations()
        self._refresh_output_tab()

        self.lbl_project_name.setText(f"Proyecto: {project_path.name}")
        self.lbl_progress.setText(f"Proyecto '{project_path.name}' cargado ({len(self.discovered_wells)} pozos).")
        self._on_config_changed()

    def _selected_forced_epsg(self) -> str:
        idx = self.cmb_force_epsg.currentIndex()
        if idx >= 0 and self.cmb_force_epsg.itemData(idx):
            txt = self.cmb_force_epsg.currentText().strip()
            data_val = str(self.cmb_force_epsg.itemData(idx) or "")
            if data_val and (data_val in txt or txt.startswith("Automático")):
                return data_val if not txt.startswith("Automático") else ""
        import re as _re
        m = _re.search(r"EPSG:\d+", self.cmb_force_epsg.currentText().upper())
        return m.group(0) if m else ""

    def _set_forced_epsg(self, epsg_code: str) -> None:
        norm = epsg_code.strip().upper()
        if not norm:
            self.cmb_force_epsg.setCurrentIndex(0)
            self._on_config_changed()
            return
        for i in range(self.cmb_force_epsg.count()):
            if str(self.cmb_force_epsg.itemData(i) or "").upper() == norm:
                self.cmb_force_epsg.setCurrentIndex(i)
                self._on_config_changed()
                return
        info = get_crs_details(norm)
        label = f"{norm} — {info['name']}" if info else norm
        self.cmb_force_epsg.addItem(label, norm)
        self.cmb_force_epsg.setCurrentIndex(self.cmb_force_epsg.count() - 1)
        self._on_config_changed()

    def _open_crs_map_selector(self) -> None:
        dlg = CRSSelectorDialog(initial_epsg=self._selected_forced_epsg(), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_forced_epsg(dlg.selected_epsg)

    def _active_profile(self) -> FieldProfile:
        prof_name = self.cmb_field_profile.currentText() or "generic"
        profile = load_field_profile(prof_name)
        forced_epsg = self._selected_forced_epsg()
        if forced_epsg:
            profile.forced_epsg = forced_epsg
            faja_num = [k for k, v in POSGAR2007_FAJA_EPSG.items() if v == forced_epsg]
            if faja_num:
                profile.forced_gk_faja = faja_num[0]
        return profile

    def _build_llm_config(self) -> LLMConfig:
        provider = self.cmb_provider.currentText()
        model_path_str = self.cmb_gguf.currentData() or str(DEFAULT_MODEL_PATH)
        model_name = (
            Path(model_path_str).name
            if provider == "llama_cpp_local"
            else (self.txt_model_name.text().strip() or DEFAULT_MODEL_PATH.name)
        )
        return LLMConfig(
            provider=provider,  # type: ignore[arg-type]
            model_name=model_name,
            base_url=self.txt_base_url.text().strip() or "http://127.0.0.1:8089",
            api_key=self.txt_api_key.text().strip() or None,
            model_path=Path(model_path_str),
            gpu_device=self.cmb_gpu.currentText(),
            ctx_size=int(self.spn_ctx.value()),
        )

    def _on_config_changed(self) -> None:
        prof = load_field_profile(self.cmb_field_profile.currentText() or "generic")
        self.lbl_profile_desc.setText(prof.description or prof.profile_name)
        epsg = self._selected_forced_epsg()
        crs_tag = f" | {epsg}" if epsg else " | CRS Auto"
        self.badge_profile.setText(f"Campo: {prof.profile_name}{crs_tag}")
        prov = self.cmb_provider.currentText()
        mod = self.cmb_gguf.currentText() if prov == "llama_cpp_local" else self.txt_model_name.text().strip()
        self.badge_llm.setText(f"LLM: {prov} ({mod})")

    def _test_llm_conn_clicked(self) -> None:
        cfg = self._build_llm_config()
        ok, msg, models = test_llm_connection(cfg)
        self.lbl_conn_status.setText(msg)
        self.lbl_conn_status.setStyleSheet("color: #15803D; font-weight: bold;" if ok else "color: #B91C1C; font-weight: bold;")
        if ok and models and cfg.provider != "llama_cpp_local":
            self.txt_model_name.setText(models[0])

    def _handle_dropped_paths(self, paths: List[Path]) -> None:
        target_name = self.txt_target_well_name.text().strip()
        try:
            staged_groups = stage_grouped_well_drop_items(paths, target_name, WORKSPACE_IMPORTS_DIR)
            self.txt_target_well_name.clear()
            total_files = 0
            for dest_dir, imported in staged_groups:
                total_files += len(imported)
                rp = dest_dir.resolve()
                if not any(Path(str(w["folder_path"])).resolve() == rp for w in self.project_manual_wells):
                    self.project_manual_wells.append({
                        "well_folder_name": dest_dir.name,
                        "folder_path": dest_dir,
                        "source": "Importado (Drag & Drop)",
                        "las_count": len(imported),
                    })
            self._refresh_discovered_wells()
            self._run_prescan()

            if len(staged_groups) == 1:
                d0, f0 = staged_groups[0]
                msg = f"Se importaron {len(f0)} archivos .LAS en:\n{d0}"
            else:
                names = ", ".join(d.name for d, _ in staged_groups)
                msg = (
                    f"Se detectaron {len(staged_groups)} pozos distintos en los archivos soltados.\n"
                    f"Se separaron automáticamente para aislar sus contextos:\n{names} ({total_files} archivos .LAS)."
                )
            QMessageBox.information(self, "Importación Completa", msg)
        except Exception as exc:
            QMessageBox.warning(self, "Error al importar", str(exc))

    def _browse_single_las_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Seleccionar archivos .LAS de un pozo", str(PROJECT_ROOT), "Archivos LAS (*.las *.LAS)")
        if files:
            self._handle_dropped_paths([Path(f) for f in files])

    def _browse_zip_file(self) -> None:
        zfile, _ = QFileDialog.getOpenFileName(self, "Seleccionar archivo .ZIP del pozo", str(PROJECT_ROOT), "Archivos ZIP (*.zip)")
        if zfile:
            self._handle_dropped_paths([Path(zfile)])

    def _browse_single_well_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de un pozo", str(PROJECT_ROOT))
        if folder:
            self._handle_dropped_paths([Path(folder)])

    def _browse_batch_directory(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar directorio raíz batch con carpetas de pozos", str(PROJECT_ROOT))
        if folder:
            p = Path(folder)
            if p not in self.batch_extra_roots:
                self.batch_extra_roots.append(p)
                self.lbl_batch_roots.setText("Carpetas batch adicionales: " + ", ".join(r.name for r in self.batch_extra_roots))
                self._refresh_discovered_wells()

    def _clear_workspace_imports_clicked(self) -> None:
        if not WORKSPACE_IMPORTS_DIR.exists():
            QMessageBox.information(self, "workspace_imports/", "La carpeta workspace_imports/ ya está vacía.")
            return
        reply = QMessageBox.question(
            self,
            "Vaciar workspace_imports/",
            "¿Seguro que querés eliminar las copias importadas dentro de workspace_imports/? (Input/ no se toca)",
        )
        if reply == QMessageBox.StandardButton.Yes:
            import shutil as _shutil
            _shutil.rmtree(WORKSPACE_IMPORTS_DIR, ignore_errors=True)
            self.project_manual_wells = [
                w for w in self.project_manual_wells
                if not str(Path(str(w["folder_path"])).resolve()).startswith(str(WORKSPACE_IMPORTS_DIR.resolve()))
            ]
            self._refresh_discovered_wells()

    def set_output_dir(self, target_dir: Path, refresh_ui: bool = True) -> None:
        """Sincroniza la carpeta de salida en Pestañas 2, 3 y 5, el badge superior y las vistas."""
        self.output_dir = Path(target_dir)
        path_txt = str(self.output_dir)
        for widget_name in ("txt_output_dir", "txt_export_output_dir", "txt_tab5_output_dir"):
            widget = getattr(self, widget_name, None)
            if widget is not None and widget.text() != path_txt:
                widget.blockSignals(True)
                widget.setText(path_txt)
                widget.blockSignals(False)
        self.badge_output.setText(f"Salida: {self.output_dir.name}/")
        if refresh_ui:
            self._refresh_output_tab()
            if not self.llm_wells:
                self._refresh_all_visualizations()

    def _browse_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta output/", str(self.output_dir))
        if folder:
            self.set_output_dir(Path(folder), refresh_ui=True)

    def _selected_target_grid_step(self) -> Optional[float]:
        preset = self.cmb_grid_step_preset.currentData()
        if preset is None:
            return None
        if preset == "custom":
            return round(float(self.spn_custom_grid_step.value()), 4)
        return round(float(preset), 4)

    def get_export_depth_grid_spec(
        self,
    ) -> Tuple[str, Optional[float], Optional[float], Optional[float], Optional[Dict[str, float]]]:
        """
        Calcula `(grid_mode, target_step, target_start, target_stop, bounds_dict)`
        para la previsualización o la exportación regularizada de todos los pozos.
        """
        mode = str(self.cmb_grid_mode.currentData() or "per_well")
        desired_step = self._selected_target_grid_step()
        if mode == "per_well":
            return mode, None, None, None, None

        source_wells = list(self.llm_wells.values()) or list(self.prescanned_wells.values())
        if not source_wells:
            return mode, desired_step, None, None, None

        try:
            bounds = compute_batch_depth_grid_bounds(source_wells, target_step=desired_step)
        except ValueError:
            return mode, desired_step, None, None, None

        if mode == "common_step":
            return mode, bounds["chosen_step"], None, None, bounds
        return mode, bounds["chosen_step"], bounds["global_start"], bounds["global_stop"], bounds

    def _on_grid_option_changed(self) -> None:
        is_custom = self.cmb_grid_step_preset.currentData() == "custom"
        self.spn_custom_grid_step.setEnabled(is_custom)
        self._update_depth_grid_preview()

    def _update_depth_grid_preview(self) -> None:
        mode, t_step, t_start, t_stop, bounds = self.get_export_depth_grid_spec()
        if mode == "per_well":
            self.lbl_grid_preview.setText(
                "Grilla: Por pozo (paso nativo más chico de cada pozo, sin extrapolar)."
            )
            return
        if bounds is None:
            step_txt = f"{t_step:.4g} m" if t_step else "menor automático del lote"
            self.lbl_grid_preview.setText(
                f"Grilla ({mode}): pendiente de pozos escaneados | Paso={step_txt}."
            )
            return
        if mode == "common_step":
            self.lbl_grid_preview.setText(
                f"Mismo Paso Común: step={bounds['chosen_step']:.4g} m "
                f"(rango nativo del lote: [{bounds['min_step']:.4g}, {bounds['max_step']:.4g}] m) | "
                f"Start/Stop individual por pozo ({int(bounds['wells_with_depth'])} pozos)."
            )
        else:
            self.lbl_grid_preview.setText(
                f"Grilla Global Idéntica: [{t_start:.2f} .. {t_stop:.2f}] m | "
                f"step={t_step:.4g} m | {int(bounds['rows']):,} filas idénticas/pozo (NaN fuera de rango)."
            )

    def _refresh_discovered_wells(self) -> None:
        roots: List[Tuple[str, Path]] = []
        if self.chk_include_input.isChecked():
            roots.append(("Input/", PROJECT_ROOT / "Input"))
        if self.chk_include_imports.isChecked():
            roots.append(("workspace_imports/", WORKSPACE_IMPORTS_DIR))
        for extra in self.batch_extra_roots:
            roots.append((f"Batch ({extra.name})", extra))

        combined: List[Dict[str, object]] = []
        seen_paths = set()
        for entry in self.project_manual_wells + discover_well_sources(roots):
            rp = Path(str(entry["folder_path"])).resolve()
            if rp not in seen_paths:
                seen_paths.add(rp)
                combined.append(entry)

        self.discovered_wells = combined
        self.tbl_import.setRowCount(len(self.discovered_wells))

        for r_idx, item in enumerate(self.discovered_wells):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Checked)
            self.tbl_import.setItem(r_idx, 0, chk_item)
            self.tbl_import.setItem(r_idx, 1, QTableWidgetItem(str(item["well_folder_name"])))
            self.tbl_import.setItem(r_idx, 2, QTableWidgetItem(str(item["source"])))
            self.tbl_import.setItem(r_idx, 3, QTableWidgetItem(str(item["las_count"])))
            for c_idx in range(4, 8):
                self.tbl_import.setItem(r_idx, c_idx, QTableWidgetItem("Pendiente pre-escaneo"))

    def _selected_well_entries(self) -> List[Dict[str, object]]:
        selected: List[Dict[str, object]] = []
        for r_idx, entry in enumerate(self.discovered_wells):
            item = self.tbl_import.item(r_idx, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                selected.append(entry)
        return selected

    def _run_prescan(self) -> None:
        profile = self._active_profile()
        entries = self._selected_well_entries()
        for r_idx, entry in enumerate(self.discovered_wells):
            chk = self.tbl_import.item(r_idx, 0)
            if not chk or chk.checkState() != Qt.CheckState.Checked:
                continue
            fname = str(entry["well_folder_name"])
            try:
                res, dfs = explore_well_directory(Path(entry["folder_path"]), target_depth_unit=profile.target_depth_unit)
                self.prescanned_wells[fname] = (res, dfs)
                units = sorted({s.original_index_unit for s in res.las_summaries if s.index_type == "DEPTH"})
                mixed = len(res.detected_wells_in_folder) > 1
                depth_txt = (
                    f"⚠ MEZCLA ({len(res.detected_wells_in_folder)} pozos: {', '.join(res.detected_wells_in_folder[:3])})"
                    if mixed
                    else f"{res.depth_files_count} DEPTH / {res.time_files_count} TIME"
                )
                it_depth = QTableWidgetItem(depth_txt)
                if mixed:
                    it_depth.setBackground(QColor("#FEF3C7"))
                self.tbl_import.setItem(r_idx, 4, it_depth)
                self.tbl_import.setItem(r_idx, 5, QTableWidgetItem(", ".join(units) or "N/A"))
                self.tbl_import.setItem(r_idx, 6, QTableWidgetItem(str(len(res.unique_depth_curves))))
                self.tbl_import.setItem(r_idx, 7, QTableWidgetItem(str(len(res.repeated_depth_curves))))
            except Exception as exc:
                self.tbl_import.setItem(r_idx, 4, QTableWidgetItem(f"Error: {exc}"))
        if entries:
            self._refresh_log_well_selector()
        self._update_depth_grid_preview()

    def _start_llm_extraction(self) -> None:
        entries = self._selected_well_entries()
        if not entries:
            QMessageBox.warning(self, "Sin pozos", "Seleccioná al menos un pozo en la Pestaña 1.")
            return

        self.output_dir = Path(self.txt_output_dir.text().strip() or str(PROJECT_ROOT / "output"))
        self.btn_run_llm.setEnabled(False)
        self.btn_export_output.setEnabled(False)
        self.txt_console.clear()

        self._llm_worker = LLMExtractionWorker(
            well_entries=entries,
            profile=self._active_profile(),
            llm_config=self._build_llm_config(),
            output_dir=self.output_dir,
            use_cache=self.chk_use_cache.isChecked(),
            incremental=self.chk_incremental.isChecked(),
        )
        self._llm_worker.progress_sig.connect(self._on_worker_progress)
        self._llm_worker.log_sig.connect(self.txt_console.appendPlainText)
        self._llm_worker.well_ready_sig.connect(self._on_well_llm_ready)
        self._llm_worker.finished_sig.connect(self._on_llm_finished)
        self._llm_worker.error_sig.connect(lambda err: QMessageBox.critical(self, "Error LLM", err))
        self._llm_worker.start()

    def _on_worker_progress(self, current: int, total: int, text: str) -> None:
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(current)
        self.lbl_progress.setText(text)

    def _on_well_llm_ready(self, fname: str, res: WellExplorationResult, dfs: Dict[str, pd.DataFrame]) -> None:
        self.prescanned_wells[fname] = (res, dfs)
        self.llm_wells[fname] = (res, dfs)
        self.per_curve_overrides.setdefault(fname, {})
        self._populate_review_table()
        self._refresh_log_well_selector()

    def _on_llm_finished(self, failed: List[str]) -> None:
        self.btn_run_llm.setEnabled(True)
        self.btn_export_output.setEnabled(True)
        self.lbl_progress.setText(f"Extracción completa ({len(self.llm_wells)} listos, {len(failed)} errores).")
        if self.tbl_review.rowCount() > 0:
            self.tbl_review.selectRow(0)
        self._update_depth_grid_preview()
        self._refresh_all_visualizations()

    def _populate_review_table(self) -> None:
        self._populating_review_table = True
        self.tbl_review.setRowCount(len(self.llm_wells))
        for r_idx, (fname, (res, _)) in enumerate(self.llm_wells.items()):
            meta = res.llm_metadata or {}
            steps = [s.step for s in res.las_summaries if s.index_type == "DEPTH" and s.step > 0]
            step_txt = f"{min(steps):.4g}" if steps else "auto"

            vals = [
                (fname, False),
                (str(meta.get("nombre_pozo") or fname), True),
                (str(meta.get("uwi") or ""), True),
                (str(meta.get("coord_x") if meta.get("coord_x") is not None else ""), True),
                (str(meta.get("coord_y") if meta.get("coord_y") is not None else ""), True),
                (str(meta.get("cota") if meta.get("cota") is not None else ""), True),
                (str(meta.get("crs_used") or ""), False),
                (step_txt, False),
                (str(len(res.repeated_depth_curves)), False),
            ]
            for c_idx, (text, editable) in enumerate(vals):
                it = QTableWidgetItem(text)
                if not editable:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    it.setBackground(QColor("#F8FAFC"))
                self.tbl_review.setItem(r_idx, c_idx, it)
        self._populating_review_table = False
        self._highlight_review_anomalies()

    def _highlight_review_anomalies(self) -> None:
        """
        Detecta anomalías cruzadas antes de exportar:
        1) Carpetas que mezclaban encabezados de múltiples pozos distintos.
        2) Dos o más filas con el mismo UWI o idénticas coordenadas (X, Y).
        """
        uwi_counts: Dict[str, int] = {}
        xy_counts: Dict[Tuple[str, str], int] = {}
        mixed_folders: List[str] = []

        for r in range(self.tbl_review.rowCount()):
            fname = self.tbl_review.item(r, 0).text().strip() if self.tbl_review.item(r, 0) else ""
            uwi = self.tbl_review.item(r, 2).text().strip().upper() if self.tbl_review.item(r, 2) else ""
            x_s = self.tbl_review.item(r, 3).text().strip() if self.tbl_review.item(r, 3) else ""
            y_s = self.tbl_review.item(r, 4).text().strip() if self.tbl_review.item(r, 4) else ""
            if uwi:
                uwi_counts[uwi] = uwi_counts.get(uwi, 0) + 1
            if x_s and y_s:
                xy_counts[(x_s, y_s)] = xy_counts.get((x_s, y_s), 0) + 1
            if fname in self.llm_wells and len(self.llm_wells[fname][0].detected_wells_in_folder) > 1:
                mixed_folders.append(fname)

        dup_uwis = {k for k, v in uwi_counts.items() if v > 1}
        dup_xys = {k for k, v in xy_counts.items() if v > 1}

        for r in range(self.tbl_review.rowCount()):
            fname = self.tbl_review.item(r, 0).text().strip() if self.tbl_review.item(r, 0) else ""
            uwi_it = self.tbl_review.item(r, 2)
            x_it = self.tbl_review.item(r, 3)
            y_it = self.tbl_review.item(r, 4)
            is_mixed = fname in mixed_folders
            is_dup_uwi = bool(uwi_it and uwi_it.text().strip().upper() in dup_uwis)
            is_dup_xy = bool(x_it and y_it and (x_it.text().strip(), y_it.text().strip()) in dup_xys)

            if self.tbl_review.item(r, 0):
                self.tbl_review.item(r, 0).setBackground(QColor("#FEF3C7" if is_mixed else "#F8FAFC"))
            if uwi_it:
                uwi_it.setBackground(QColor("#FEE2E2" if is_dup_uwi else "#FFFFFF"))
            if x_it and y_it:
                bg = QColor("#FEE2E2" if is_dup_xy else "#FFFFFF")
                x_it.setBackground(bg)
                y_it.setBackground(bg)

        warnings: List[str] = []
        if mixed_folders:
            warnings.append(f"Carpetas con archivos de múltiples pozos mezclados: {', '.join(mixed_folders)}")
        if dup_uwis:
            warnings.append(f"UWI repetido entre filas: {', '.join(sorted(dup_uwis))}")
        if dup_xys:
            warnings.append("Coordenadas (X, Y) idénticas detectadas entre pozos distintos")

        if warnings:
            self.lbl_review_warning.setText("⚠ Alerta de Integridad: " + " | ".join(warnings))
            self.lbl_review_warning.setVisible(True)
        else:
            self.lbl_review_warning.setVisible(False)

    def _on_review_cell_edited(self, item: QTableWidgetItem) -> None:
        if self._populating_review_table:
            return
        row = item.row()
        col = item.column()
        fname_item = self.tbl_review.item(row, 0)
        if not fname_item:
            return
        fname = fname_item.text()
        if fname not in self.llm_wells:
            return

        res, _ = self.llm_wells[fname]
        meta = res.llm_metadata or {}
        val = item.text().strip()
        try:
            if col == 1:
                meta["nombre_pozo"] = val
            elif col == 2:
                meta["uwi"] = val.upper()
            elif col == 3:
                meta["coord_x"] = int(round(float(val))) if val.isdigit() else float(val)
            elif col == 4:
                meta["coord_y"] = int(round(float(val))) if val.isdigit() else float(val)
            elif col == 5:
                meta["cota"] = round(float(val), 2)
            self.manual_edited_wells.add(fname)
            self._highlight_review_anomalies()
        except ValueError:
            pass

    def _on_review_row_selected(self) -> None:
        rows = self.tbl_review.selectionModel().selectedRows()
        if not rows:
            return
        r = rows[0].row()
        fname = self.tbl_review.item(r, 0).text()
        if fname not in self.llm_wells:
            return
        res, _ = self.llm_wells[fname]
        meta = res.llm_metadata or {}

        report_parts = [
            f"POZO: {meta.get('nombre_pozo', fname)} (UWI: {meta.get('uwi', 'S/D')})",
            f"Motor / Origen: {meta.get('_extraction_mode') or ('LLM EN VIVO' if meta.get('_inference_time_s') else 'CACHÉ')}",
            f"CRS / Geodesia: {meta.get('crs_used', 'S/D')} — {meta.get('coord_resolution_note', '')}",
            f"Fuente en Header: {meta.get('fuente_coordenadas', 'S/D')}",
            "",
            "REPORTE DEL LLM:",
            str(meta.get("resumen_normalizacion") or "Sin resumen escrito disponible (cargado desde caché anterior sin ese campo; desmarcá 'Omitir LLM usando Caché' para generarlo en vivo)."),
        ]
        self.txt_llm_report.setPlainText("\n".join(report_parts))

        # Poblar tabla de curvas repetidas con selector keep_all vs merge por curva
        rep = res.repeated_depth_curves
        self.tbl_repeated_curves.setRowCount(len(rep))
        well_ov = self.per_curve_overrides.setdefault(fname, {})
        default_strat = self.cmb_repeat_strategy.currentData() or "keep_all"

        for idx, (mnem, occs) in enumerate(rep.items()):
            self.tbl_repeated_curves.setItem(idx, 0, QTableWidgetItem(mnem))
            files_desc = f"{len(occs)}x (" + ", ".join(str(o["source_file"])[:18] for o in occs[:3]) + ")"
            self.tbl_repeated_curves.setItem(idx, 1, QTableWidgetItem(files_desc))

            cmb = QComboBox()
            cmb.addItem("Conservar (:2, :3)", "keep_all")
            cmb.addItem("Fusionar (merge)", "merge")
            cur_val = well_ov.get(mnem, default_strat)
            cmb.setCurrentIndex(0 if cur_val == "keep_all" else 1)
            cmb.currentIndexChanged.connect(
                lambda _, m=mnem, c=cmb, fn=fname: self.per_curve_overrides[fn].__setitem__(m, c.currentData())
            )
            self.tbl_repeated_curves.setCellWidget(idx, 2, cmb)

    def _start_export(self) -> None:
        if not self.llm_wells:
            QMessageBox.warning(self, "Sin datos", "Primero ejecutá la extracción con el LLM en la Pestaña 3.")
            return
        self.set_output_dir(
            Path(self.txt_export_output_dir.text().strip() or self.txt_output_dir.text().strip() or str(PROJECT_ROOT / "output")),
            refresh_ui=False,
        )
        grid_mode, t_step, t_start, t_stop, _bounds = self.get_export_depth_grid_spec()
        if grid_mode != "per_well":
            self.txt_console.appendPlainText(
                f"[GRILLA] Regularizando exportación ({grid_mode}) | step={t_step}m | "
                f"Start={t_start if t_start is not None else 'pozo'} | "
                f"Stop={t_stop if t_stop is not None else 'pozo'}"
            )
        self.btn_run_llm.setEnabled(False)
        self.btn_export_output.setEnabled(False)

        items = [
            (
                fname,
                res,
                dfs,
                self.per_curve_overrides.get(fname, {}),
                fname in self.manual_edited_wells,
            )
            for fname, (res, dfs) in self.llm_wells.items()
        ]
        self._export_worker = ExportWorker(
            items=items,
            output_dir=self.output_dir,
            global_repeat_strategy=self.cmb_repeat_strategy.currentData() or "keep_all",
            profile=self._active_profile(),
            target_grid_step=t_step,
            target_grid_start=t_start,
            target_grid_stop=t_stop,
        )
        self._export_worker.progress_sig.connect(self._on_worker_progress)
        self._export_worker.log_sig.connect(self.txt_console.appendPlainText)
        self._export_worker.exported_sig.connect(lambda fn, ws: self.export_summaries.__setitem__(fn, ws))
        self._export_worker.finished_sig.connect(self._on_export_finished)
        self._export_worker.start()

    def _on_export_finished(self, summaries: List[WellExportSummary]) -> None:
        self.btn_run_llm.setEnabled(True)
        self.btn_export_output.setEnabled(True)
        self.lbl_progress.setText(f"Exportados {len(summaries)} pozos a {self.output_dir.name}/.")
        self._refresh_all_visualizations()
        self._refresh_output_tab()

    # ------------------------------------------------------------------
    # VISUALIZACIÓN Y MAPA (PESTAÑA 4)
    # ------------------------------------------------------------------
    def _collect_map_records(self) -> List[Dict[str, object]]:
        records: List[Dict[str, object]] = []
        if self.llm_wells:
            for fname, (res, _) in self.llm_wells.items():
                meta = res.llm_metadata or {}
                ws = self.export_summaries.get(fname)
                records.append({
                    "folder": fname,
                    "well_name": meta.get("nombre_pozo", fname),
                    "uwi": meta.get("uwi", ""),
                    "coord_x": meta.get("coord_x"),
                    "coord_y": meta.get("coord_y"),
                    "crs_used": meta.get("crs_used", ""),
                    "coord_note": meta.get("coord_resolution_note", ""),
                    "grid_step": ws.grid_step if ws else "N/D",
                    "curves_count": len(ws.exported_depth_curves) if ws else len(res.unique_depth_curves),
                })
        else:
            inv = load_output_inventory(self.output_dir)
            for c in inv["cache_records"]:
                meta = c.get("metadata", {})
                records.append({
                    "folder": c.get("well_folder_name", ""),
                    "well_name": meta.get("nombre_pozo", c.get("well_name", "")),
                    "uwi": meta.get("uwi", ""),
                    "coord_x": meta.get("coord_x"),
                    "coord_y": meta.get("coord_y"),
                    "crs_used": meta.get("crs_used", ""),
                    "coord_note": meta.get("coord_resolution_note", ""),
                    "grid_step": c.get("grid_step", "N/D"),
                    "curves_count": len(meta.get("curvas_detectadas", [])),
                })
        return records

    def _refresh_all_visualizations(self) -> None:
        self._refresh_geodesic_map()
        self._refresh_log_well_selector()
        self._plot_selected_well_logs()
        self._plot_coverage_and_xpt()
        self._plot_excel_matrix()

    def _refresh_geodesic_map(self) -> None:
        records = self._collect_map_records()
        self.tbl_map_names.setRowCount(len(records))
        for r_idx, rec in enumerate(records):
            wgs = inverse_project_to_wgs84(rec.get("coord_x"), rec.get("coord_y"), str(rec.get("crs_used") or ""))
            lat_s = f"{wgs[0]:.5f}" if wgs else "S/D"
            lon_s = f"{wgs[1]:.5f}" if wgs else "S/D"
            self.tbl_map_names.setItem(r_idx, 0, QTableWidgetItem(str(rec.get("folder", ""))))
            self.tbl_map_names.setItem(r_idx, 1, QTableWidgetItem(str(rec.get("well_name", ""))))
            self.tbl_map_names.setItem(r_idx, 2, QTableWidgetItem(str(rec.get("uwi", ""))))
            self.tbl_map_names.setItem(r_idx, 3, QTableWidgetItem(str(rec.get("crs_used", ""))))
            self.tbl_map_names.setItem(r_idx, 4, QTableWidgetItem(lat_s))
            self.tbl_map_names.setItem(r_idx, 5, QTableWidgetItem(lon_s))

        map_html_path = self.output_dir / "_well_map_preview.html"
        html_str = build_folium_well_map_html(records, map_html_path)
        if self.web_map_view is not None:
            self.web_map_view.setHtml(html_str)
        elif hasattr(self, "canvas_map_fallback"):
            fig = self.canvas_map_fallback.figure
            fig.clear()
            ax = fig.add_subplot(111)
            for rec in records:
                if rec.get("coord_x") is not None and rec.get("coord_y") is not None:
                    x, y = float(rec["coord_x"]), float(rec["coord_y"])
                    ax.scatter(x, y, s=80, color="#2563EB", edgecolor="black")
                    ax.annotate(str(rec["well_name"]), (x, y), xytext=(5, 5), textcoords="offset points", fontsize=8)
            ax.set_title("Mapa Gauss-Krüger de Pozos")
            ax.set_xlabel("X Este [m]")
            ax.set_ylabel("Y Norte [m]")
            ax.grid(True, linestyle=":")
            ax.ticklabel_format(style="plain", useOffset=False)
            fig.tight_layout()
            self.canvas_map_fallback.canvas.draw()

    def _open_map_in_browser(self) -> None:
        map_html_path = self.output_dir / "_well_map_preview.html"
        if not map_html_path.exists():
            self._refresh_geodesic_map()
        if map_html_path.exists():
            webbrowser.open(map_html_path.as_uri())

    def _refresh_log_well_selector(self) -> None:
        source_dict = self.llm_wells or self.prescanned_wells
        current = self.cmb_log_well.currentText()
        self.cmb_log_well.blockSignals(True)
        self.cmb_log_well.clear()
        self.cmb_log_well.addItems(sorted(source_dict.keys()))
        if current and current in source_dict:
            self.cmb_log_well.setCurrentText(current)
        self.cmb_log_well.blockSignals(False)
        self._on_log_well_changed(self.cmb_log_well.currentText())

    def _on_log_well_changed(self, fname: str) -> None:
        source_dict = self.llm_wells or self.prescanned_wells
        self.lst_log_curves.blockSignals(True)
        self.lst_log_curves.clear()
        if fname in source_dict:
            res, _ = source_dict[fname]
            preferred = {"GR", "CAL", "HCAL", "DT", "NEU", "TNPH", "RHOZ", "ZDEN", "SP", "M2R6", "AHT90"}
            curves_sorted = sorted(res.all_assigned_depth_curves)
            checked_count = 0
            for c in curves_sorted:
                it = QListWidgetItem(c)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                if (c in preferred or len(curves_sorted) <= 4) and checked_count < 4:
                    it.setCheckState(Qt.CheckState.Checked)
                    checked_count += 1
                else:
                    it.setCheckState(Qt.CheckState.Unchecked)
                self.lst_log_curves.addItem(it)
            if checked_count == 0 and self.lst_log_curves.count() > 0:
                for i in range(min(4, self.lst_log_curves.count())):
                    self.lst_log_curves.item(i).setCheckState(Qt.CheckState.Checked)
        self.lst_log_curves.blockSignals(False)

    def _plot_selected_well_logs(self) -> None:
        source_dict = self.llm_wells or self.prescanned_wells
        fname = self.cmb_log_well.currentText()
        if not fname or fname not in source_dict:
            return
        res, dfs = source_dict[fname]

        selected: List[str] = []
        for i in range(self.lst_log_curves.count()):
            it = self.lst_log_curves.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                selected.append(it.text())
            if len(selected) >= 6:
                break
        if not selected:
            return

        wname = str((res.llm_metadata or {}).get("nombre_pozo") or fname)
        csv_cand = self.output_dir / "wells" / f"{wname}_selected.csv"
        tracks = prepare_well_log_tracks(res, dfs, csv_cand if csv_cand.exists() else None, selected, max_tracks=6)

        fig = self.canvas_logs.figure
        fig.clear()
        axes = fig.subplots(1, len(tracks), sharey=True)
        if len(tracks) == 1:
            axes = [axes]

        for ax, tr in zip(axes, tracks):
            all_vals: List[np.ndarray] = []
            for idx_s, rs in enumerate(tr["raw_series"]):
                d_arr, v_arr = rs["depth"], rs["values"]
                if len(v_arr) > 0:
                    all_vals.append(v_arr)
                    ax.plot(v_arr, d_arr, color="#94A3B8" if self.chk_overlay_csv.isChecked() else "#2563EB",
                            linewidth=2.4 if self.chk_overlay_csv.isChecked() else 1.2,
                            alpha=0.85, label=rs["label"][:18])
            if self.chk_overlay_csv.isChecked() and tr["csv_depth"] is not None:
                ax.plot(tr["csv_values"], tr["csv_depth"], color="#DC2626", linestyle="--", linewidth=1.1, label="CSV Grilla Común")
                all_vals.append(tr["csv_values"])

            ax.set_title(f"{tr['curve_name']} [{tr['unit']}]", fontsize=9, fontweight="bold")
            ax.grid(True, linestyle=":", alpha=0.6)
            ax.legend(loc="upper right", fontsize=6.5)
            if all_vals:
                merged = np.concatenate(all_vals)
                if len(merged) > 10:
                    p1, p99 = np.percentile(merged, [1, 99])
                    if p99 > p1:
                        margin = (p99 - p1) * 0.12
                        ax.set_xlim(p1 - margin, p99 + margin)

        axes[0].invert_yaxis()
        axes[0].set_ylabel("Profundidad [m]", fontweight="bold")
        fig.suptitle(f"Visor de Registros — {wname}", fontsize=10.5, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        self.canvas_logs.canvas.draw()

    def _plot_coverage_and_xpt(self) -> None:
        source_dict = self.llm_wells or self.prescanned_wells
        fname = self.cmb_log_well.currentText()
        if fname and fname in source_dict:
            res, _ = source_dict[fname]
            fig = self.canvas_coverage.figure
            fig.clear()
            ax = fig.add_subplot(111)
            depth_s = [s for s in res.las_summaries if s.index_type == "DEPTH"]
            for i, s in enumerate(depth_s):
                top = min(s.declared_start, s.declared_stop)
                bot = max(s.declared_start, s.declared_stop)
                ax.bar(i, bot - top, bottom=top, width=0.45, color="#2563EB", alpha=0.82, edgecolor="black")
            ax.set_xticks(range(len(depth_s)))
            ax.set_xticklabels([s.file_name[:18] for s in depth_s], rotation=15, ha="right", fontsize=8)
            ax.invert_yaxis()
            ax.set_ylabel("Profundidad [m]")
            ax.set_title(f"Cobertura .LAS — {fname}", fontweight="bold")
            ax.grid(True, axis="y", linestyle=":")
            fig.tight_layout()
            self.canvas_coverage.canvas.draw()

        xpt_csv = self.output_dir / "time_logs" / "xpt_stations_summary.csv"
        fig_x = self.canvas_xpt.figure
        fig_x.clear()
        ax_x = fig_x.add_subplot(111)
        if xpt_csv.exists():
            df_x = pd.read_csv(xpt_csv)
            p_col = "CP_SAP_final" if "CP_SAP_final" in df_x.columns and df_x["CP_SAP_final"].notna().any() else "CP_HYD_final"
            if p_col in df_x.columns:
                for wn, grp in df_x.groupby("Well"):
                    ax_x.scatter(grp[p_col], grp["Station_Depth_m"], label=str(wn), s=50, edgecolor="black")
                ax_x.invert_yaxis()
                ax_x.set_xlabel("Presión Final [psia]")
                ax_x.set_ylabel("Profundidad Estacionaria [m]")
                ax_x.set_title("Estaciones en Tiempo (XPT)", fontweight="bold")
                ax_x.grid(True, linestyle=":")
                ax_x.legend(fontsize=8)
        else:
            ax_x.text(0.5, 0.5, "Sin estaciones XPT exportadas", ha="center", va="center")
        fig_x.tight_layout()
        self.canvas_xpt.canvas.draw()

    def _plot_excel_matrix(self) -> None:
        excel_path = self.output_dir / "well_metadata.xlsx"
        fig = self.canvas_matrix.figure
        fig.clear()
        ax = fig.add_subplot(111)
        if excel_path.exists():
            df = pd.read_excel(excel_path)
            fixed = {"File", "Well", "UWI", "Start", "Stop", "Step", "X", "Y", "Elevation"}
            curve_cols = [c for c in df.columns if c not in fixed]
            if len(df) > 0 and curve_cols:
                mat = np.array([[1 if str(df.loc[r, c]).strip().lower() == "yes" else 0 for c in curve_cols] for r in range(len(df))])
                cmap = matplotlib.colors.ListedColormap(["#F1F5F9", "#16A34A"])
                ax.imshow(mat, aspect="auto", cmap=cmap, interpolation="nearest")
                ax.set_yticks(range(len(df)))
                ax.set_yticklabels(df["Well"].astype(str).tolist(), fontsize=8.5, fontweight="bold")
                ax.set_xticks(range(len(curve_cols)))
                ax.set_xticklabels(curve_cols, rotation=90, fontsize=6.5)
                ax.set_title(f"Matriz Maestra ({len(df)} pozos x {len(curve_cols)} curvas)", fontweight="bold")
        else:
            ax.text(0.5, 0.5, "Aún no existe output/well_metadata.xlsx", ha="center", va="center")
        fig.tight_layout()
        self.canvas_matrix.canvas.draw()

    # ------------------------------------------------------------------
    # SALIDAS OUTPUT/ (PESTAÑA 5)
    # ------------------------------------------------------------------
    def _fill_table_from_df(self, table: QTableWidget, df: pd.DataFrame, max_rows: int = 200) -> None:
        sub = df.head(max_rows)
        table.setColumnCount(len(sub.columns))
        table.setRowCount(len(sub))
        table.setHorizontalHeaderLabels([str(c) for c in sub.columns])
        for r in range(len(sub)):
            for c in range(len(sub.columns)):
                val = sub.iloc[r, c]
                txt = "NaN" if pd.isna(val) else str(val)
                table.setItem(r, c, QTableWidgetItem(txt))

    def _refresh_output_tab(self) -> None:
        inv = load_output_inventory(self.output_dir)
        ex_df: pd.DataFrame = inv["excel_df"]
        csvs: List[Path] = inv["well_csvs"]
        xpt_df: pd.DataFrame = inv["time_summary_df"]

        self.lbl_output_summary.setText(
            f"[{self.output_dir.name}/] Pozos en Excel: {len(ex_df)} | CSVs en wells/: {len(csvs)} | Estaciones Tiempo (XPT): {len(xpt_df)}"
        )
        if not ex_df.empty:
            self._fill_table_from_df(self.tbl_out_excel, ex_df, max_rows=500)
        else:
            self.tbl_out_excel.setRowCount(0)
            self.tbl_out_excel.setColumnCount(0)

        if not xpt_df.empty:
            self._fill_table_from_df(self.tbl_out_xpt, xpt_df, max_rows=500)
        else:
            self.tbl_out_xpt.setRowCount(0)
            self.tbl_out_xpt.setColumnCount(0)

        self.cmb_out_csv.blockSignals(True)
        self.cmb_out_csv.clear()
        for cp in csvs:
            self.cmb_out_csv.addItem(cp.name, str(cp))
        self.cmb_out_csv.blockSignals(False)
        if csvs:
            self._load_selected_output_csv(csvs[0].name)
        else:
            self.tbl_out_csv.setRowCount(0)
            self.tbl_out_csv.setColumnCount(0)

        self.txt_out_errors.setPlainText(inv["errors_text"] or "Sin errores registrados en batch_errors.log.")

    def _load_selected_output_csv(self, name: str) -> None:
        path_str = self.cmb_out_csv.currentData()
        if path_str and Path(path_str).exists():
            df = pd.read_csv(Path(path_str), nrows=150)
            self._fill_table_from_df(self.tbl_out_csv, df, max_rows=150)

    def _open_output_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(self.output_dir))


def main() -> None:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    win = WellCuratorMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
gui.py
======
Interfaz grafica del motor VFR GO/NO GO.

Flujo — Tab EVALUACION
-----------------------
  1. Seleccionar aeronave
  2. Buscar aeropuerto de origen  (cabeceras se cargan automaticamente)
  3. Buscar aeropuerto de destino (cabeceras se cargan automaticamente)
  4. Ingresar hora de despegue UTC y duracion
  5. Presionar EVALUAR

Flujo — Tab RUTAS
------------------
  1. Buscar origen y destino
  2. Seleccionar modo y presionar CALCULAR
  3. Mapa muestra ruta + 3 alternativas cercanas + 5 lejanas al destino

Flujo — Tab BRIEFING
---------------------
  1. Completar EVALUACION y RUTAS
  2. Presionar GENERAR BRIEFING
"""

import sys
import os
import json
import threading
import logging
from datetime import datetime, timezone, timedelta
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

try:
    import tkintermapview
    HAS_MAP = True
except ImportError:
    HAS_MAP = False

from data.airports      import AIRPORTS, search_airports
from route.performance  import haversine_km
from decision.engine    import DecisionEngine
from output.formatter   import format_decision
from output.briefing    import generate_briefing
from route.optimizer    import optimize
from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER, PROFILES, PROFILE_NAMES


# ─────────────────────────────────────────────────────────────────────────────
# Paleta de colores (Catppuccin Mocha)
# ─────────────────────────────────────────────────────────────────────────────

C = {
    "bg"          : "#1e1e2e",
    "bg_panel"    : "#2a2a3e",
    "bg_input"    : "#252538",
    "bg_widget"   : "#1a1a2e",
    "fg"          : "#cdd6f4",
    "fg_dim"      : "#7f849c",
    "fg_accent"   : "#cba6f7",
    "go"          : "#a6e3a1",
    "go_dark"     : "#1e3a2e",
    "caution"     : "#fab387",
    "caution_dark": "#3a2a1e",
    "nogo"        : "#f38ba8",
    "nogo_dark"   : "#3a1e2a",
    "pending"     : "#585b70",
    "pending_bg"  : "#2a2a3e",
    "border"      : "#45475a",
    "btn_eval"    : "#cba6f7",
    "btn_eval_fg" : "#1e1e2e",
}

DEC_STYLES = {
    "GO"      : (C["go_dark"],      C["go"],      C["go"]),
    "CAUTION" : (C["caution_dark"], C["caution"], C["caution"]),
    "NO GO"   : (C["nogo_dark"],    C["nogo"],    C["nogo"]),
    "---"     : (C["pending_bg"],   C["pending"], C["border"]),
}

MODE_MAP = {
    "Sugerida"   : "suggested",
    "Mas corta"  : "shortest",
    "Mas rapida" : "fastest",
    "Mas segura" : "safest",
}


# ─────────────────────────────────────────────────────────────────────────────
# Aplicacion principal
# ─────────────────────────────────────────────────────────────────────────────

def _bind_tooltip(widget, text: str):
    """Agrega un tooltip simple al pasar el mouse sobre un widget."""
    tip_win = [None]

    def _show(event):
        tip_win[0] = tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
        tk.Label(
            tw, text=text,
            bg="#313244", fg=C["fg"],
            font=("Segoe UI", 8),
            relief="flat", padx=6, pady=4,
            justify="left",
        ).pack()

    def _hide(_event):
        if tip_win[0]:
            tip_win[0].destroy()
            tip_win[0] = None

    widget.bind("<Enter>", _show)
    widget.bind("<Leave>", _hide)


class App(tk.Tk):

    def __init__(self):
        super().__init__()

        self.configure(bg=C["bg"])
        self.minsize(900, 720)
        self.geometry("1200x900")

        self._current_aircraft   = ALPHA_TRAINER
        self._engine             = None
        self._result_queue       = []
        self._route_result_queue = []
        self._route_path_widget   = None
        self._blocked_path_widget = None
        self._map_markers         = []   # lista de objetos marker para limpiar
        self._map_widget         = None
        self._map_zone_polygons  = []   # poligonos de espacios aereos restringidos
        self._svc_map_markers    = []   # markers del mapa de servicios
        self._svc_map_widget     = None
        self._manual_runways     = self._load_manual_runways()  # {code: [hdg, ...]}
        self._svc_weather_request_id = 0  # versión para descartar updates stale

        self._last_origin_result = None
        self._last_dest_result   = None
        self._last_route_result  = None
        self._airport_popup      = None
        self._syncing            = False  # evita bucles en sincronizacion de pickers

        self._configure_styles()
        self._build_ui()

        self._refresh_runways("orig")
        self._refresh_runways("dest")
        self._set_time_now()
        self._update_title()

        self.after(150, self._poll_results)

    # ── Titulo dinamico ──────────────────────────────────────────────────────

    def _update_title(self):
        ac = self._current_aircraft
        self.title(f"VFR GO/NO GO  -  {ac.name}")
        if hasattr(self, "_lbl_aircraft_info"):
            self._lbl_aircraft_info.config(
                text=(f"{ac.name}  |  {ac.cruise_kt:.0f} kt  |  "
                      f"xwind max {ac.crosswind_max_kt:.0f} kt  |  "
                      f"rango ~{ac.range_km:.0f} km")
            )

    # ── Cabeceras manuales (persistencia) ────────────────────────────────────

    _MANUAL_RUNWAYS_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "manual_runways.json"
    )

    def _load_manual_runways(self) -> dict:
        try:
            with open(self._MANUAL_RUNWAYS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: [int(h) for h in v] for k, v in data.items() if isinstance(v, list)}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logging.getLogger(__name__).warning(f"No se pudo leer manual_runways.json: {exc}")
            return {}

    def _save_manual_runways(self):
        try:
            with open(self._MANUAL_RUNWAYS_PATH, "w", encoding="utf-8") as f:
                json.dump(self._manual_runways, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logging.getLogger(__name__).warning(f"No se pudo guardar manual_runways.json: {exc}")

    # ── Estilos ttk ──────────────────────────────────────────────────────────

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("Dark.TCombobox",
            fieldbackground  = C["bg_widget"],
            background       = C["bg_widget"],
            foreground       = C["fg"],
            selectbackground = C["bg_panel"],
            selectforeground = C["fg"],
            arrowcolor       = C["fg_dim"],
            bordercolor      = C["border"],
            lightcolor       = C["border"],
            darkcolor        = C["border"],
        )
        style.map("Dark.TCombobox",
            fieldbackground = [("readonly", C["bg_widget"])],
            foreground      = [("readonly", C["fg"])],
        )
        style.configure("Dark.TSpinbox",
            fieldbackground = C["bg_widget"],
            foreground      = C["fg"],
            background      = C["bg_panel"],
            arrowcolor      = C["fg_dim"],
            bordercolor     = C["border"],
        )
        style.configure("Dark.TCheckbutton",
            background     = C["bg_input"],
            foreground     = C["fg_dim"],
            indicatorcolor = C["bg_widget"],
        )
        style.map("Dark.TCheckbutton",
            background = [("active", C["bg_input"])],
            foreground = [("active", C["fg"])],
        )
        style.configure("Dark.TNotebook",
            background  = C["bg"],
            bordercolor = C["border"],
            tabmargins  = [0, 0, 0, 0],
        )
        style.configure("Dark.TNotebook.Tab",
            background = C["bg_panel"],
            foreground = C["fg_dim"],
            padding    = [20, 7],
            font       = ("Segoe UI", 10),
        )
        style.map("Dark.TNotebook.Tab",
            background = [("selected", C["bg_input"])],
            foreground = [("selected", C["fg_accent"])],
        )

    # ── Construccion de la UI ─────────────────────────────────────────────────

    def _build_ui(self):
        hdr = tk.Frame(self, bg=C["bg"], pady=8)
        hdr.pack(fill="x", padx=24)

        tk.Label(hdr, text="VFR GO/NO GO",
                 bg=C["bg"], fg=C["fg_accent"],
                 font=("Segoe UI", 17, "bold")).pack(side="left")

        self._lbl_utc = tk.Label(hdr, text="",
                 bg=C["bg"], fg=C["fg_dim"],
                 font=("Segoe UI", 9))
        self._lbl_utc.pack(side="right")
        self._tick_clock()

        ac_bar = tk.Frame(self, bg=C["bg_panel"], pady=6, padx=18)
        ac_bar.pack(fill="x", padx=12, pady=(0, 4))

        tk.Label(ac_bar, text="Aeronave:",
                 bg=C["bg_panel"], fg=C["fg"], font=("Segoe UI", 10, "bold")
                 ).pack(side="left", padx=(0, 8))

        self._var_aircraft = tk.StringVar(value=PROFILE_NAMES[0])
        ac_cb = ttk.Combobox(
            ac_bar,
            textvariable = self._var_aircraft,
            values       = PROFILE_NAMES,
            state        = "readonly",
            width        = 25,
            font         = ("Segoe UI", 10),
            style        = "Dark.TCombobox",
        )
        ac_cb.pack(side="left", padx=(0, 16))
        ac_cb.bind("<<ComboboxSelected>>", self._on_aircraft_change)

        self._lbl_aircraft_info = tk.Label(
            ac_bar, text="",
            bg=C["bg_panel"], fg=C["fg_dim"], font=("Segoe UI", 9)
        )
        self._lbl_aircraft_info.pack(side="left")

        self._notebook = ttk.Notebook(self, style="Dark.TNotebook")
        self._notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        eval_frame      = tk.Frame(self._notebook, bg=C["bg"])
        route_frame     = tk.Frame(self._notebook, bg=C["bg"])
        services_frame  = tk.Frame(self._notebook, bg=C["bg"])
        briefing_frame  = tk.Frame(self._notebook, bg=C["bg"])

        self._notebook.add(route_frame,    text="  RUTAS  ")
        self._notebook.add(eval_frame,     text="  EVALUACION  ")
        self._notebook.add(services_frame, text="  SERVICIOS  ")
        self._notebook.add(briefing_frame, text="  BRIEFING  ")

        self._build_rutas_tab(route_frame)
        self._build_evaluacion_tab(eval_frame)
        self._build_servicios_tab(services_frame)
        self._build_briefing_tab(briefing_frame)

    # ── Cambio de aeronave ────────────────────────────────────────────────────

    def _on_aircraft_change(self, event=None):
        name = self._var_aircraft.get()
        self._current_aircraft = PROFILES.get(name, ALPHA_TRAINER)
        self._update_title()

    # ── Tab EVALUACION ────────────────────────────────────────────────────────

    def _build_evaluacion_tab(self, parent):
        inp = tk.Frame(parent, bg=C["bg_input"], padx=20, pady=16)
        inp.pack(fill="x", padx=24, pady=(8, 6))

        orig_frm = self._build_airport_picker(inp, "orig", "ORIGEN", show_runway=True)
        orig_frm.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        tk.Label(inp, text="->",
                 bg=C["bg_input"], fg=C["fg_dim"],
                 font=("Segoe UI", 20)).grid(row=0, column=1, padx=8)

        dest_frm = self._build_airport_picker(inp, "dest", "DESTINO", show_runway=True)
        dest_frm.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        inp.columnconfigure(0, weight=1)
        inp.columnconfigure(2, weight=1)

        ctrl_frm = tk.Frame(inp, bg=C["bg_input"])
        ctrl_frm.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(14, 0))

        tk.Label(ctrl_frm, text="Hora dep. (UTC):",
                 bg=C["bg_input"], fg=C["fg"], font=("Segoe UI", 10)
                 ).pack(side="left")

        self._var_time = tk.StringVar()
        self._entry_time = tk.Entry(
            ctrl_frm, textvariable=self._var_time,
            width=7, font=("Segoe UI", 11),
            bg=C["bg_widget"], fg=C["fg"],
            insertbackground=C["fg"],
            relief="flat", bd=4,
        )
        self._entry_time.pack(side="left", padx=(6, 2))

        tk.Button(ctrl_frm, text="Ahora +30",
                  command=self._set_time_now,
                  bg=C["bg_panel"], fg=C["fg_dim"],
                  font=("Segoe UI", 8), relief="flat", cursor="hand2",
                  activebackground=C["border"],
                  ).pack(side="left", padx=(2, 20))

        self._lbl_duration = tk.Label(
            ctrl_frm, text="",
            bg=C["bg_input"], fg=C["fg_dim"], font=("Segoe UI", 9),
        )
        self._lbl_duration.pack(side="left", padx=(0, 20))

        self._var_mock = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl_frm, text="Modo Mock (sin internet)",
                        variable=self._var_mock,
                        style="Dark.TCheckbutton",
                        ).pack(side="left", padx=(0, 20))

        self._btn_eval = tk.Button(
            ctrl_frm, text="  EVALUAR  ",
            command=self._on_evaluate,
            bg=C["btn_eval"], fg=C["btn_eval_fg"],
            font=("Segoe UI", 11, "bold"),
            relief="flat", padx=14, pady=5,
            cursor="hand2",
            activebackground="#b893e8",
        )
        self._btn_eval.pack(side="right")

        self._lbl_status = tk.Label(
            parent, text="Ingresa los datos y presiona EVALUAR",
            bg=C["bg"], fg=C["fg_dim"], font=("Segoe UI", 9),
            anchor="w",
        )
        self._lbl_status.pack(fill="x", padx=28, pady=(2, 4))

        res_row = tk.Frame(parent, bg=C["bg"])
        res_row.pack(fill="x", padx=24, pady=(0, 8))

        self._orig_card = self._build_result_card(res_row)
        self._orig_card.pack(side="left", fill="both", expand=True, padx=(0, 6))

        self._dest_card = self._build_result_card(res_row)
        self._dest_card.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # Tira de meteorologia en ruta
        self._route_strip_outer = tk.Frame(parent, bg=C["bg_panel"])
        self._route_strip_outer.pack(fill="x", padx=24, pady=(0, 4))

        detail_outer = tk.Frame(parent, bg=C["bg_panel"])
        detail_outer.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        self._txt = scrolledtext.ScrolledText(
            detail_outer,
            bg=C["bg_panel"], fg=C["fg"],
            font=("Consolas", 9),
            relief="flat", wrap="word",
            state="disabled",
        )
        self._txt.pack(fill="both", expand=True, padx=2, pady=2)

        self._txt.tag_config("header",  foreground=C["fg_accent"], font=("Consolas", 9, "bold"))
        self._txt.tag_config("go",      foreground=C["go"])
        self._txt.tag_config("caution", foreground=C["caution"])
        self._txt.tag_config("nogo",    foreground=C["nogo"])
        self._txt.tag_config("dim",     foreground=C["fg_dim"])
        self._txt.tag_config("normal",  foreground=C["fg"])

    # ── Widget buscador de aerodromos ─────────────────────────────────────────

    def _build_airport_picker(self, parent, which: str, title: str,
                              show_runway: bool = True) -> tk.Frame:
        """
        Selector de aerodromos con dropdown.
        Muestra el aeropuerto seleccionado en un boton; al clickear abre un
        dropdown con campo de busqueda y listbox filtrado.
        Atributos generados: _{which}_selected_info, _{which}_lbl_meta,
        _{which}_lbl_sel, _{which}_lb, _{which}_lb_data, _{which}_var_search.
        Si show_runway: tambien _{which}_var_rwy, _{which}_rwy_cb.
        """
        frm = tk.Frame(parent, bg=C["bg_input"])

        # ── Titulo ───────────────────────────────────────────────────────────
        tk.Label(frm, text=title,
                 bg=C["bg_input"], fg=C["fg_accent"],
                 font=("Segoe UI", 9, "bold")
                 ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))

        # ── Boton selector (siempre visible, muestra seleccion actual) ───────
        sel_row = tk.Frame(frm, bg=C["bg_widget"], cursor="hand2")
        sel_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        sel_row.columnconfigure(0, weight=1)

        lbl_sel = tk.Label(
            sel_row, text="Seleccionar...",
            bg=C["bg_widget"], fg=C["fg_dim"],
            font=("Segoe UI", 9), anchor="w", padx=8, pady=5,
        )
        lbl_sel.grid(row=0, column=0, sticky="ew")

        tk.Label(sel_row, text="▾",
                 bg=C["bg_widget"], fg=C["fg_dim"],
                 font=("Segoe UI", 10), padx=6,
                 ).grid(row=0, column=1, sticky="e")

        # ── Meta label (elevacion, municipio) ────────────────────────────────
        lbl_meta = tk.Label(frm, text="",
                            bg=C["bg_input"], fg=C["fg_dim"],
                            font=("Segoe UI", 8))
        lbl_meta.grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 2))

        # ── Dropdown (oculto por defecto) ─────────────────────────────────────
        dd = tk.Frame(frm, bg=C["bg_widget"], bd=1, relief="solid")
        # dd no se hace grid aqui — se muestra/oculta dinamicamente en row=3

        var_search = tk.StringVar()
        entry = tk.Entry(
            dd, textvariable=var_search, width=26,
            bg=C["bg_input"], fg=C["fg"],
            insertbackground=C["fg"], relief="flat", bd=4,
            font=("Segoe UI", 10),
        )
        entry.pack(fill="x", padx=2, pady=(2, 0))

        lb_wrap = tk.Frame(dd, bg=C["bg_widget"])
        lb_wrap.pack(fill="both", expand=True, pady=(2, 2))

        lb = tk.Listbox(
            lb_wrap, height=6,
            bg=C["bg_widget"], fg=C["fg"],
            selectbackground=C["bg_panel"],
            selectforeground=C["fg_accent"],
            font=("Segoe UI", 9),
            relief="flat", bd=0,
            activestyle="none",
        )
        sb = ttk.Scrollbar(lb_wrap, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── Cabecera (opcional) ───────────────────────────────────────────────
        if show_runway:
            rwy_container = tk.Frame(frm, bg=C["bg_input"])
            rwy_container.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 2))
            rwy_container.columnconfigure(1, weight=1)

            # Fila normal: label + combobox
            rwy_normal_row = tk.Frame(rwy_container, bg=C["bg_input"])
            rwy_normal_row.columnconfigure(1, weight=1)

            tk.Label(rwy_normal_row, text="Cabecera:",
                     bg=C["bg_input"], fg=C["fg"], font=("Segoe UI", 10)
                     ).grid(row=0, column=0, sticky="w", padx=(0, 8))
            var_rwy = tk.StringVar()
            rwy_cb = ttk.Combobox(
                rwy_normal_row, textvariable=var_rwy, state="readonly",
                width=24, font=("Segoe UI", 10), style="Dark.TCombobox",
            )
            rwy_cb.grid(row=0, column=1, sticky="ew")

            # Fila manual: aviso + entry + boton (visible cuando no hay datos)
            rwy_manual_frm = tk.Frame(rwy_container, bg=C["bg_input"])
            rwy_manual_frm.columnconfigure(0, weight=1)

            tk.Label(rwy_manual_frm,
                     text="Sin datos de pista — ingresá la cabecera (0-360):",
                     bg=C["bg_input"], fg=C["caution"],
                     font=("Segoe UI", 8),
                     ).pack(anchor="w")

            entry_row_inner = tk.Frame(rwy_manual_frm, bg=C["bg_input"])
            entry_row_inner.pack(fill="x", pady=(2, 0))

            var_manual_hdg = tk.StringVar()
            entry_manual = tk.Entry(
                entry_row_inner, textvariable=var_manual_hdg, width=6,
                bg=C["bg_widget"], fg=C["fg"],
                insertbackground=C["fg"], relief="flat", bd=3,
                font=("Segoe UI", 10),
            )
            entry_manual.pack(side="left", padx=(0, 6))

            def _confirm_manual(which=which):
                info = getattr(self, f"_{which}_selected_info", None)
                if not info:
                    return
                raw = var_manual_hdg.get().strip()
                try:
                    hdg = int(raw)
                    if not (0 <= hdg <= 360):
                        raise ValueError()
                except ValueError:
                    messagebox.showerror(
                        "Cabecera inválida",
                        "Ingresá un valor entre 0 y 360.",
                        parent=self,
                    )
                    return

                headings = [hdg]
                recip = (hdg + 180) % 360
                if messagebox.askyesno(
                    "Cabecera recíproca",
                    f"¿Agregar también la cabecera recíproca {recip:03d}°?",
                    parent=self,
                ):
                    headings.append(recip)

                self._manual_runways[info.code] = headings
                self._save_manual_runways()
                var_manual_hdg.set("")
                self._refresh_runways(which)

            tk.Button(
                entry_row_inner, text="Agregar",
                command=_confirm_manual,
                bg=C["bg_panel"], fg=C["fg_dim"],
                font=("Segoe UI", 8), relief="flat", cursor="hand2",
                activebackground=C["border"],
            ).pack(side="left")
            entry_manual.bind("<Return>", lambda _: _confirm_manual())

            setattr(self, f"_{which}_var_rwy",        var_rwy)
            setattr(self, f"_{which}_rwy_cb",          rwy_cb)
            setattr(self, f"_{which}_rwy_normal_row",  rwy_normal_row)
            setattr(self, f"_{which}_rwy_manual_frm",  rwy_manual_frm)

        frm.columnconfigure(1, weight=1)

        # ── Atributos del picker ──────────────────────────────────────────────
        setattr(self, f"_{which}_lbl_meta",      lbl_meta)
        setattr(self, f"_{which}_lbl_sel",        lbl_sel)
        setattr(self, f"_{which}_selected_info",  None)
        setattr(self, f"_{which}_lb",             lb)
        setattr(self, f"_{which}_lb_data",        [])
        setattr(self, f"_{which}_var_search",     var_search)

        # ── Logica de apertura/cierre del dropdown ────────────────────────────
        _open = [False]

        def _open_dd():
            if _open[0]:
                return
            _open[0] = True
            dd.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 2))
            var_search.set("")
            self._populate_lb(which, "")
            entry.focus_set()

        def _close_dd():
            if not _open[0]:
                return
            _open[0] = False
            dd.grid_remove()

        for w in (sel_row, lbl_sel):
            w.bind("<Button-1>", lambda e: _open_dd())

        entry.bind("<Escape>", lambda e: _close_dd())
        entry.bind("<Return>", lambda e: _close_dd())

        def _on_entry_focus_out(event):
            frm.after(200, lambda: _close_dd() if _open[0] else None)

        entry.bind("<FocusOut>", _on_entry_focus_out)

        # ── Trace: filtrar listbox al escribir ────────────────────────────────
        var_search.trace_add("write",
                             lambda *_: self._populate_lb(which, var_search.get()))

        # ── Seleccion desde la listbox ────────────────────────────────────────
        def on_select(event):
            if self._syncing:
                return
            lb_data = getattr(self, f"_{which}_lb_data")
            sel = lb.curselection()
            if not sel or sel[0] >= len(lb_data):
                return
            info = lb_data[sel[0]]
            setattr(self, f"_{which}_selected_info", info)

            lbl_sel.config(text=info.name, fg=C["fg"])
            muni = f"  |  {info.municipality}" if info.municipality else ""
            lbl_meta.config(text=f"{info.code}  |  {info.elev_ft} ft AMSL{muni}")

            _close_dd()

            if show_runway:
                self._refresh_runways(which)

            # Sincronizar picker par (RUTAS <-> EVALUACION)
            _SYNC = {"rv_orig": "orig", "rv_dest": "dest",
                     "orig": "rv_orig", "dest": "rv_dest"}
            peer = _SYNC.get(which)
            if peer:
                self._sync_airport_silent(peer, info)

            # Callback post-seleccion (ej: tab Servicios)
            cb = getattr(self, f"_{which}_select_cb", None)
            if callable(cb):
                cb(info)

        lb.bind("<<ListboxSelect>>", on_select)

        # ── Seleccion inicial (sin event_generate para evitar cascadas) ───────
        self._populate_lb(which, "")
        lb_data0 = getattr(self, f"_{which}_lb_data", [])
        if lb_data0:
            info0 = lb_data0[0]
            lb.selection_set(0)
            setattr(self, f"_{which}_selected_info", info0)
            lbl_sel.config(text=info0.name, fg=C["fg"])
            muni = f"  |  {info0.municipality}" if info0.municipality else ""
            lbl_meta.config(text=f"{info0.code}  |  {info0.elev_ft} ft AMSL{muni}")
            if show_runway:
                self._refresh_runways(which)

        return frm

    def _populate_lb(self, which: str, query: str):
        """
        Rellena la listbox del dropdown con resultados del buscador.
        Si el aeropuerto actualmente seleccionado no aparece en los
        resultados, lo pinea al tope para que sea siempre visible.
        No dispara <<ListboxSelect>> (la seleccion inicial se maneja
        de forma explicita en _build_airport_picker).
        """
        results = list(search_airports(query)[:100])

        lb   = getattr(self, f"_{which}_lb")
        prev = getattr(self, f"_{which}_selected_info", None)

        # Pinear el aeropuerto seleccionado si no aparece en los resultados
        if prev and not any(r.code == prev.code for r in results):
            results.insert(0, prev)

        lb.delete(0, "end")
        lb_data      = []
        reselect_idx = None

        for i, info in enumerate(results):
            muni = f" — {info.municipality}" if info.municipality else ""
            lb.insert("end", f"{info.name}{muni}  ({info.code})")
            lb_data.append(info)
            if prev and info.code == prev.code:
                reselect_idx = i

        setattr(self, f"_{which}_lb_data", lb_data)

        if reselect_idx is not None:
            lb.selection_set(reselect_idx)
            lb.see(reselect_idx)

    def _build_result_card(self, parent) -> tk.Frame:
        card = tk.Frame(parent, bg=C["bg_panel"], pady=10, padx=14)

        lbl_station = tk.Label(card, text="---",
                               bg=C["bg_panel"], fg=C["fg_dim"],
                               font=("Segoe UI", 9))
        lbl_station.pack(anchor="w")

        dec_box = tk.Label(card, text="---",
                           bg=DEC_STYLES["---"][0], fg=DEC_STYLES["---"][1],
                           font=("Segoe UI", 26, "bold"),
                           pady=16, padx=10, relief="flat")
        dec_box.pack(fill="x", pady=(4, 2))

        lbl_r = tk.Label(card, text="",
                         bg=C["bg_panel"], fg=C["fg_dim"],
                         font=("Segoe UI", 9))
        lbl_r.pack(anchor="w")

        lbl_dom = tk.Label(card, text="",
                           bg=C["bg_panel"], fg=C["fg_dim"],
                           font=("Segoe UI", 9))
        lbl_dom.pack(anchor="w")

        card._lbl_station = lbl_station
        card._dec_box     = dec_box
        card._lbl_r       = lbl_r
        card._lbl_dom     = lbl_dom

        return card

    # ── Tab RUTAS ─────────────────────────────────────────────────────────────

    def _build_rutas_tab(self, parent):
        # Controles superiores en grid: fila 0 = buscadores, fila 1 = modo+boton
        ctrl = tk.Frame(parent, bg=C["bg_input"], padx=16, pady=8)
        ctrl.pack(fill="x", padx=16, pady=(8, 0))
        ctrl.columnconfigure(0, weight=1)
        ctrl.columnconfigure(1, weight=1)

        orig_picker = self._build_airport_picker(ctrl, "rv_orig", "ORIGEN", show_runway=False)
        orig_picker.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 6))

        dest_picker = self._build_airport_picker(ctrl, "rv_dest", "DESTINO", show_runway=False)
        dest_picker.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 6))

        # Fila 1: modo + opciones + boton
        row1 = tk.Frame(ctrl, bg=C["bg_input"])
        row1.grid(row=1, column=0, columnspan=2, sticky="ew")

        tk.Label(row1, text="Modo:", bg=C["bg_input"], fg=C["fg"],
                 font=("Segoe UI", 10)).pack(side="left")
        self._rv_mode = tk.StringVar(value="Sugerida")
        ttk.Combobox(row1, textvariable=self._rv_mode,
                     values=list(MODE_MAP.keys()), state="readonly", width=12,
                     font=("Segoe UI", 10), style="Dark.TCombobox",
                     ).pack(side="left", padx=(4, 14))

        self._rv_avoid_restricted = tk.BooleanVar(value=False)
        ttk.Checkbutton(row1, text="Evitar espacios aereos restringidos",
                        variable=self._rv_avoid_restricted,
                        style="Dark.TCheckbutton",
                        ).pack(side="left", padx=(0, 14))

        self._btn_calc = tk.Button(
            row1, text="  CALCULAR  ",
            command=self._on_calculate,
            bg=C["btn_eval"], fg=C["btn_eval_fg"],
            font=("Segoe UI", 11, "bold"),
            relief="flat", padx=14, pady=5,
            cursor="hand2",
            activebackground="#b893e8",
        )
        self._btn_calc.pack(side="right")

        self._lbl_route_status = tk.Label(
            parent,
            text="Selecciona origen y destino, luego presiona CALCULAR",
            bg=C["bg"], fg=C["fg_dim"], font=("Segoe UI", 9), anchor="w",
        )
        self._lbl_route_status.pack(fill="x", padx=28, pady=(4, 0))

        # Area principal: mapa (izq.) + panel de resultados (der.)
        main_area = tk.Frame(parent, bg=C["bg"])
        main_area.pack(fill="both", expand=True, padx=16, pady=(4, 12))

        # Panel de resultados (derecha, ancho fijo)
        results_frame = tk.Frame(main_area, bg=C["bg_panel"], width=310)
        results_frame.pack(side="right", fill="y", padx=(8, 0))
        results_frame.pack_propagate(False)

        hdr_row = tk.Frame(results_frame, bg=C["bg_panel"])
        hdr_row.pack(fill="x")
        tk.Label(hdr_row, text="RESUMEN DE RUTA",
                 bg=C["bg_panel"], fg=C["fg_accent"],
                 font=("Segoe UI", 9, "bold"), anchor="w",
                 pady=8, padx=12,
                 ).pack(side="left")
        self._btn_change_alt = tk.Button(
            hdr_row, text="Cambiar alternativo",
            command=self._on_change_alternate,
            bg=C["bg_panel"], fg=C["fg_dim"],
            font=("Segoe UI", 8), relief="flat", cursor="hand2",
            activebackground=C["border"],
        )
        self._btn_change_alt.pack(side="right", padx=8)

        self._route_summary_txt = scrolledtext.ScrolledText(
            results_frame,
            bg=C["bg_panel"], fg=C["fg"],
            font=("Consolas", 9),
            relief="flat", wrap="word",
            state="disabled",
        )
        self._route_summary_txt.pack(fill="both", expand=True, padx=2, pady=2)
        self._route_summary_txt.tag_config("hdr",    foreground=C["fg_accent"], font=("Consolas", 9, "bold"))
        self._route_summary_txt.tag_config("warn",   foreground=C["caution"])
        self._route_summary_txt.tag_config("danger", foreground=C["nogo"])
        self._route_summary_txt.tag_config("ok",     foreground=C["go"])
        self._route_summary_txt.tag_config("dim",    foreground=C["fg_dim"])

        # Mapa (izquierda) — sin markers iniciales
        map_frame = tk.Frame(main_area, bg=C["bg"])
        map_frame.pack(side="left", fill="both", expand=True)

        if HAS_MAP:
            self._map_widget = tkintermapview.TkinterMapView(map_frame)
            self._map_widget.set_tile_server(
                "https://tile.opentopomap.org/{z}/{x}/{y}.png",
                max_zoom=17,
            )
            self._map_widget.set_position(-32.0, -64.0)
            self._map_widget.set_zoom(5)
            self._map_widget.pack(fill="both", expand=True)
            self.after(800, self._draw_airspace_zones)
        else:
            tk.Label(
                map_frame,
                text="Mapa no disponible.\nInstalar: pip install tkintermapview",
                bg=C["bg"], fg=C["fg_dim"],
                font=("Segoe UI", 12), justify="center",
            ).pack(expand=True)

    # ── Tab SERVICIOS ─────────────────────────────────────────────────────────

    def _build_servicios_tab(self, parent):
        self._svc_map_markers = []

        # ── Buscador superior ─────────────────────────────────────────────────
        ctrl = tk.Frame(parent, bg=C["bg_input"], padx=16, pady=8)
        ctrl.pack(fill="x", padx=16, pady=(8, 0))
        ctrl.columnconfigure(0, weight=1)

        svc_picker = self._build_airport_picker(
            ctrl, "svc", "PUNTO DE REFERENCIA", show_runway=False,
        )
        svc_picker.grid(row=0, column=0, sticky="nsew")

        # Registrar callback post-seleccion
        self._svc_select_cb = self._on_svc_airport_selected

        self._lbl_svc_status = tk.Label(
            parent,
            text="Buscá un aeródromo de referencia para ver puntos de repostaje cercanos.",
            bg=C["bg"], fg=C["fg_dim"], font=("Segoe UI", 9), anchor="w",
        )
        self._lbl_svc_status.pack(fill="x", padx=28, pady=(4, 0))

        # ── Área principal: mapa (izq.) + panel (der.) ───────────────────────
        main_area = tk.Frame(parent, bg=C["bg"])
        main_area.pack(fill="both", expand=True, padx=16, pady=(4, 12))

        # Panel lateral derecho
        right = tk.Frame(main_area, bg=C["bg_panel"], width=310)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)

        # Canvas + scrollbar para contenido dinámico del panel
        svc_canvas = tk.Canvas(right, bg=C["bg_panel"], highlightthickness=0)
        svc_vsb    = ttk.Scrollbar(right, orient="vertical", command=svc_canvas.yview)
        svc_canvas.configure(yscrollcommand=svc_vsb.set)
        svc_vsb.pack(side="right", fill="y")
        svc_canvas.pack(side="left", fill="both", expand=True)

        svc_inner    = tk.Frame(svc_canvas, bg=C["bg_panel"])
        svc_inner_id = svc_canvas.create_window((0, 0), window=svc_inner, anchor="nw")

        def _on_configure(_event):
            svc_canvas.configure(scrollregion=svc_canvas.bbox("all"))

        def _on_canvas_resize(event):
            svc_canvas.itemconfig(svc_inner_id, width=event.width)

        svc_inner.bind("<Configure>", _on_configure)
        svc_canvas.bind("<Configure>", _on_canvas_resize)

        def _on_mousewheel(event):
            svc_canvas.yview_scroll(-1 * (event.delta // 120), "units")

        svc_canvas.bind("<Enter>",  lambda _: svc_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        svc_canvas.bind("<Leave>",  lambda _: svc_canvas.unbind_all("<MouseWheel>"))

        self._svc_info_frame  = svc_inner
        self._svc_canvas      = svc_canvas

        # Mensaje inicial
        tk.Label(
            svc_inner,
            text="Seleccioná un aeródromo\npara ver servicios.",
            bg=C["bg_panel"], fg=C["fg_dim"],
            font=("Segoe UI", 9), justify="center",
        ).pack(expand=True, pady=40)

        # Mapa izquierdo
        map_frame = tk.Frame(main_area, bg=C["bg"])
        map_frame.pack(side="left", fill="both", expand=True)

        if HAS_MAP:
            self._svc_map_widget = tkintermapview.TkinterMapView(map_frame)
            self._svc_map_widget.set_tile_server(
                "https://tile.opentopomap.org/{z}/{x}/{y}.png",
                max_zoom=17,
            )
            self._svc_map_widget.set_position(-32.0, -64.0)
            self._svc_map_widget.set_zoom(5)
            self._svc_map_widget.pack(fill="both", expand=True)
        else:
            tk.Label(
                map_frame,
                text="Mapa no disponible.\nInstalar: pip install tkintermapview",
                bg=C["bg"], fg=C["fg_dim"],
                font=("Segoe UI", 12), justify="center",
            ).pack(expand=True)

    def _on_svc_airport_selected(self, info):
        """Callback cuando el usuario selecciona un aeródromo en la tab Servicios."""
        self._svc_weather_request_id += 1
        req_id = self._svc_weather_request_id
        self._svc_current_info = info
        self._populate_svc_panel(info)
        self._draw_svc_fuel_markers(info)
        self._lbl_svc_status.config(
            text=f"Repostaje cercano a {info.name} ({info.code})  —  radio 150 km",
            fg=C["fg_dim"],
        )
        threading.Thread(
            target=self._fetch_svc_weather_thread,
            args=(info, req_id),
            daemon=True,
        ).start()

    def _populate_svc_panel(self, info):
        """Rellena el panel lateral de Servicios con info del aeródromo y cercanos con combustible."""
        frm = self._svc_info_frame
        for w in frm.winfo_children():
            w.destroy()

        def _sep():
            tk.Frame(frm, bg=C["border"], height=1).pack(fill="x", padx=8, pady=(6, 0))

        def _section(title):
            f = tk.Frame(frm, bg=C["bg_panel"], padx=10, pady=3)
            f.pack(fill="x")
            tk.Label(f, text=title.upper(),
                     bg=C["bg_panel"], fg=C["fg_dim"],
                     font=("Segoe UI", 8, "bold"), anchor="w",
                     ).pack(fill="x")
            return f

        # ── Cabecera del aeródromo buscado ────────────────────────────────────
        hdr = tk.Frame(frm, bg=C["bg_panel"], padx=10, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text=info.name,
                 bg=C["bg_panel"], fg=C["fg_accent"],
                 font=("Segoe UI", 11, "bold"), anchor="w", wraplength=270,
                 ).pack(fill="x")
        tk.Label(hdr, text=info.code,
                 bg=C["bg_panel"], fg=C["fg_dim"],
                 font=("Segoe UI", 9), anchor="w",
                 ).pack(fill="x")

        # ── Pistas ───────────────────────────────────────────────────────────
        _sep()
        rwy_frm = _section("Pistas")
        runways = list(info.runways)
        manual_hdgs = self._manual_runways.get(info.code, [])
        if not runways and not manual_hdgs:
            tk.Label(rwy_frm, text="Sin datos de pistas",
                     bg=C["bg_panel"], fg=C["fg_dim"],
                     font=("Segoe UI", 9), anchor="w",
                     ).pack(fill="x")
        else:
            for rwy in runways:
                parts = [f"{rwy.heading:03d}°", rwy.label]
                if rwy.length_m:
                    parts.append(f"{rwy.length_m} m")
                if rwy.width_m:
                    parts.append(f"ancho {rwy.width_m} m")
                if rwy.surface:
                    parts.append(rwy.surface)
                tk.Label(rwy_frm,
                         text="  ·  ".join(p for p in parts if p),
                         bg=C["bg_panel"], fg=C["fg"],
                         font=("Segoe UI", 8), anchor="w",
                         ).pack(fill="x", pady=1)
            for hdg in manual_hdgs:
                tk.Label(rwy_frm, text=f"{hdg:03d}°  (manual)",
                         bg=C["bg_panel"], fg=C["fg_dim"],
                         font=("Segoe UI", 8, "italic"), anchor="w",
                         ).pack(fill="x", pady=1)

        # ── Meteorología (NWP) ────────────────────────────────────────────────
        _sep()
        met_frm = _section("Meteorología  ·  NWP")
        met_content = tk.Frame(met_frm, bg=C["bg_panel"])
        met_content.pack(fill="x")
        self._svc_met_content = met_content
        tk.Label(met_content, text="Consultando Open-Meteo…",
                 bg=C["bg_panel"], fg=C["fg_dim"],
                 font=("Segoe UI", 8, "italic"), anchor="w",
                 ).pack(fill="x")

        # ── Combustible propio ────────────────────────────────────────────────
        _sep()
        fuel_frm = _section("Combustible")
        if info.fuel:
            tk.Label(fuel_frm, text=info.fuel,
                     bg=C["bg_panel"], fg=C["go"],
                     font=("Segoe UI", 9), anchor="w", wraplength=270,
                     ).pack(fill="x")
        else:
            tk.Label(fuel_frm, text="Sin servicio de repostaje",
                     bg=C["bg_panel"], fg=C["fg_dim"],
                     font=("Segoe UI", 9), anchor="w",
                     ).pack(fill="x")

        if hasattr(info, "schedule") and info.schedule:
            tk.Label(fuel_frm, text=f"Horario: {info.schedule}",
                     bg=C["bg_panel"], fg=C["fg_dim"],
                     font=("Segoe UI", 8), anchor="w", wraplength=270,
                     ).pack(fill="x", pady=(2, 0))

        if hasattr(info, "phones") and info.phones:
            for ph in info.phones[:2]:
                tk.Label(fuel_frm, text=f"Tel: {ph}",
                         bg=C["bg_panel"], fg=C["fg_dim"],
                         font=("Segoe UI", 8), anchor="w",
                         ).pack(fill="x")

        # ── Cercanos con combustible ───────────────────────────────────────────
        fuel_nearby = []
        for code, ap in AIRPORTS.items():
            if code == info.code:
                continue
            if not getattr(ap, "fuel", ""):
                continue
            dist = haversine_km(info.lat, info.lon, ap.lat, ap.lon)
            if dist <= 150:
                fuel_nearby.append((dist, ap))
        fuel_nearby.sort(key=lambda x: x[0])

        _sep()
        section_title = (
            f"Repostaje cercano  ({len(fuel_nearby)} dentro de 150 km)"
            if fuel_nearby else "Repostaje cercano  (150 km)"
        )
        near_frm = _section(section_title)

        if not fuel_nearby:
            tk.Label(near_frm, text="Sin aeródromos con combustible\nen un radio de 150 km.",
                     bg=C["bg_panel"], fg=C["fg_dim"],
                     font=("Segoe UI", 8), anchor="w", justify="left",
                     ).pack(fill="x", pady=(2, 0))
        else:
            def _make_popup_cmd(airport_info):
                return lambda _=None: self._show_airport_popup(airport_info)

            for dist, ap in fuel_nearby:
                row = tk.Frame(near_frm, bg=C["bg_panel"], cursor="hand2")
                row.pack(fill="x", pady=(5, 0))

                top_row = tk.Frame(row, bg=C["bg_panel"])
                top_row.pack(fill="x")
                tk.Label(top_row, text=ap.name,
                         bg=C["bg_panel"], fg=C["fg"],
                         font=("Segoe UI", 9, "bold"), anchor="w", wraplength=200,
                         ).pack(side="left")
                tk.Label(top_row, text=f"{dist:.0f} km",
                         bg=C["bg_panel"], fg=C["fg_dim"],
                         font=("Segoe UI", 8),
                         ).pack(side="right")

                tk.Label(row, text=f"{ap.code}  ·  {ap.fuel}",
                         bg=C["bg_panel"], fg=C["go"],
                         font=("Segoe UI", 8), anchor="w", wraplength=270,
                         ).pack(fill="x")

                details = []
                if hasattr(ap, "schedule") and ap.schedule:
                    details.append(ap.schedule)
                if hasattr(ap, "phones") and ap.phones:
                    details.append(ap.phones[0])
                if details:
                    tk.Label(row, text="  ·  ".join(details),
                             bg=C["bg_panel"], fg=C["fg_dim"],
                             font=("Segoe UI", 7), anchor="w", wraplength=270,
                             ).pack(fill="x")

                cmd = _make_popup_cmd(ap)
                row.bind("<Button-1>", cmd)
                for child in row.winfo_children():
                    child.bind("<Button-1>", cmd)

                tk.Frame(near_frm, bg=C["border"], height=1).pack(
                    fill="x", padx=4, pady=(5, 0),
                )

        # Forzar scroll al tope
        self._svc_canvas.after(50, lambda: self._svc_canvas.yview_moveto(0))

    def _fetch_svc_weather_thread(self, info, req_id):
        """Fetch NWP en background para el aeródromo seleccionado en SERVICIOS."""
        import time
        try:
            from data.fetcher_openmeteo    import OpenMeteoFetcher
            from parsers.openmeteo_adapter import OpenMeteoAdapter

            fetcher = OpenMeteoFetcher(mock=False)
            adapter = OpenMeteoAdapter()
            raw = fetcher.get_forecast(lat=info.lat, lon=info.lon, elevation_m=0)
            if raw is None:
                self.after(0, lambda: self._update_svc_met(None, "Sin datos NWP", req_id))
                return
            all_wx = adapter.adapt_all(raw, station_id=info.code)
            if not all_wx:
                self.after(0, lambda: self._update_svc_met(None, "Sin datos NWP", req_id))
                return
            now = int(time.time())
            wx = min(all_wx, key=lambda w: abs(w.obs_time - now))
            self.after(0, lambda wx=wx: self._update_svc_met(wx, None, req_id))
        except Exception as exc:
            logger.warning(f"NWP servicios fallido: {exc}")
            self.after(0, lambda: self._update_svc_met(None, f"Error: {exc}", req_id))

    def _update_svc_met(self, wx, error, req_id):
        """Actualiza la sección MET del panel de Servicios con datos NWP."""
        if req_id != self._svc_weather_request_id:
            return
        frm = getattr(self, "_svc_met_content", None)
        if frm is None or not frm.winfo_exists():
            return
        for w in frm.winfo_children():
            w.destroy()

        if error or wx is None:
            tk.Label(frm, text=error or "Sin datos NWP disponibles",
                     bg=C["bg_panel"], fg=C["fg_dim"],
                     font=("Segoe UI", 8, "italic"), anchor="w",
                     ).pack(fill="x")
            return

        def _row(label, value, color=None):
            r = tk.Frame(frm, bg=C["bg_panel"])
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label,
                     bg=C["bg_panel"], fg=C["fg_dim"],
                     font=("Segoe UI", 8), anchor="w", width=14,
                     ).pack(side="left")
            tk.Label(r, text=value,
                     bg=C["bg_panel"], fg=color or C["fg"],
                     font=("Segoe UI", 9), anchor="w",
                     ).pack(side="left")

        _DIRS = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]

        # Viento
        if wx.wind_spd_kt == 0:
            wind_str = "Calma"
        elif wx.wind_variable or wx.wind_dir is None:
            spd = f"  ·  {wx.wind_spd_kt:.0f} kt" if wx.wind_spd_kt else ""
            wind_str = f"Variable{spd}"
        else:
            d = _DIRS[int((wx.wind_dir + 22.5) / 45) % 8]
            spd = f"  ·  {wx.wind_spd_kt:.0f} kt" if wx.wind_spd_kt else ""
            wind_str = f"{wx.wind_dir:03d}° ({d}){spd}"
        _row("Viento", wind_str)

        if wx.wind_gust_kt and wx.wind_gust_kt > 0:
            _row("Ráfagas", f"{wx.wind_gust_kt:.0f} kt", C["caution"])

        # Temperatura y spread
        if wx.temp_c is not None:
            _row("Temperatura", f"{wx.temp_c:.0f}°C")
        if wx.dewpoint_c is not None:
            _row("Punto de rocío", f"{wx.dewpoint_c:.0f}°C")
        if wx.spread_c is not None:
            spr_color = (C["nogo"]    if wx.spread_c < 2.0 else
                         C["caution"] if wx.spread_c < 3.0 else
                         "#f9e2af"    if wx.spread_c < 5.0 else C["fg_dim"])
            _row("Spread T/Td", f"{wx.spread_c:.1f}°C", spr_color)

        # Visibilidad
        if wx.visibility_km is not None:
            vis_str = ("> 10 km" if wx.visibility_km >= 9.9
                       else f"{wx.visibility_km:.0f} km")
            vis_color = (C["nogo"]    if wx.visibility_km < 1.5 else
                         C["caution"] if wx.visibility_km < 3.0 else
                         "#f9e2af"    if wx.visibility_km < 5.0 else C["fg_dim"])
            _row("Visibilidad", vis_str, vis_color)

        # Techo
        if wx.ceiling_ft is not None:
            ceil_color = (C["nogo"]    if wx.ceiling_ft < 500  else
                          C["caution"] if wx.ceiling_ft < 1000 else
                          "#f9e2af"    if wx.ceiling_ft < 2000 else C["fg_dim"])
            _row("Techo", f"{wx.ceiling_ft:,} ft AGL", ceil_color)
        else:
            _row("Techo", "Despejado", C["fg_dim"])

        # QNH
        if wx.altimeter_hpa is not None:
            _row("QNH", f"{wx.altimeter_hpa:.0f} hPa")

        # Fenómenos wx
        if wx.wx_codes:
            _row("Fenómenos", "  ".join(wx.wx_codes), C["caution"])

        # Categoría ANAC
        if wx.flight_category:
            cat_colors = {
                "VFR" : C["go"],
                "MVFR": "#f9e2af",
                "IFR" : C["caution"],
                "LIFR": C["nogo"],
            }
            _row("Categoría", wx.flight_category,
                 cat_colors.get(wx.flight_category, C["fg"]))

        # Density altitude
        info = getattr(self, "_svc_current_info", None)
        if info and wx.temp_c is not None:
            try:
                from features.density_altitude import compute_density_altitude, advisory
                da = compute_density_altitude(wx.temp_c, info.elev_ft, wx.altimeter_hpa,
                                              getattr(info, "elev_estimated", False))
                adv = advisory(da)
                da_color = (C["nogo"]    if adv == "HIGH"     else
                            C["caution"] if adv == "ELEVATED" else C["fg_dim"])
                tk.Frame(frm, bg=C["border"], height=1).pack(fill="x", pady=(6, 2))
                _row("Density Alt.", f"{da.density_alt_ft:,} ft  ({adv})", da_color)
                _row("Presión Alt.", f"{da.pressure_alt_ft:,} ft")
                _row("Desvío ISA", f"{da.isa_deviation_c:+.1f}°C")
            except Exception:
                pass

    def _draw_svc_fuel_markers(self, info):
        """Dibuja en el mapa de Servicios: marker del aeródromo buscado + cercanos con combustible."""
        if not self._svc_map_widget:
            return

        for m in self._svc_map_markers:
            try:
                m.delete()
            except Exception:
                pass
        self._svc_map_markers = []

        def _make_cmd(airport_info):
            def _cmd(_marker):
                self._show_airport_popup(airport_info)
            return _cmd

        # Marker del aeródromo de referencia (violeta)
        m = self._svc_map_widget.set_marker(
            info.lat, info.lon,
            text=info.code,
            marker_color_circle="#cba6f7",
            marker_color_outside="#313244",
            command=_make_cmd(info),
        )
        self._svc_map_markers.append(m)

        # Cercanos con combustible (amarillo/dorado)
        for code, ap in AIRPORTS.items():
            if code == info.code:
                continue
            if not getattr(ap, "fuel", ""):
                continue
            dist = haversine_km(info.lat, info.lon, ap.lat, ap.lon)
            if dist <= 150:
                m = self._svc_map_widget.set_marker(
                    ap.lat, ap.lon,
                    text=f"{ap.code} ({dist:.0f}km)",
                    marker_color_circle="#f9e2af",
                    marker_color_outside="#3a3000",
                    command=_make_cmd(ap),
                )
                self._svc_map_markers.append(m)

        self._svc_map_widget.set_position(info.lat, info.lon)
        self._svc_map_widget.set_zoom(8)

    # ── Tab BRIEFING ──────────────────────────────────────────────────────────

    def _build_briefing_tab(self, parent):
        ctrl = tk.Frame(parent, bg=C["bg_input"], padx=16, pady=10)
        ctrl.pack(fill="x", padx=16, pady=(8, 0))

        self._lbl_briefing_status = tk.Label(
            ctrl, text="Completa la EVALUACION y las RUTAS, luego genera el briefing.",
            bg=C["bg_input"], fg=C["fg_dim"], font=("Segoe UI", 9),
        )
        self._lbl_briefing_status.pack(side="left")

        self._btn_briefing = tk.Button(
            ctrl, text="  GENERAR BRIEFING  ",
            command=self._on_generate_briefing,
            bg=C["btn_eval"], fg=C["btn_eval_fg"],
            font=("Segoe UI", 11, "bold"),
            relief="flat", padx=14, pady=5,
            cursor="hand2",
            activebackground="#b893e8",
        )
        self._btn_briefing.pack(side="right")

        outer = tk.Frame(parent, bg=C["bg_panel"])
        outer.pack(fill="both", expand=True, padx=16, pady=(6, 16))

        self._briefing_txt = scrolledtext.ScrolledText(
            outer,
            bg=C["bg_panel"], fg=C["fg"],
            font=("Consolas", 9),
            relief="flat", wrap="word",
            state="disabled",
        )
        self._briefing_txt.pack(fill="both", expand=True, padx=2, pady=2)
        self._briefing_txt.tag_config("go",      foreground=C["go"],      font=("Consolas", 9, "bold"))
        self._briefing_txt.tag_config("caution", foreground=C["caution"], font=("Consolas", 9, "bold"))
        self._briefing_txt.tag_config("nogo",    foreground=C["nogo"],    font=("Consolas", 9, "bold"))
        self._briefing_txt.tag_config("hdr",     foreground=C["fg_accent"])
        self._briefing_txt.tag_config("dim",     foreground=C["fg_dim"])

    # ── Logica de UI — EVALUACION ─────────────────────────────────────────────

    def _refresh_runways(self, which: str):
        rwy_cb         = getattr(self, f"_{which}_rwy_cb",         None)
        var_rwy        = getattr(self, f"_{which}_var_rwy",        None)
        lbl_meta       = getattr(self, f"_{which}_lbl_meta",       None)
        rwy_normal_row = getattr(self, f"_{which}_rwy_normal_row", None)
        rwy_manual_frm = getattr(self, f"_{which}_rwy_manual_frm", None)
        info           = getattr(self, f"_{which}_selected_info",  None)

        if info:
            if lbl_meta:
                muni = f"  |  {info.municipality}" if info.municipality else ""
                lbl_meta.config(text=f"{info.code}  |  {info.elev_ft} ft AMSL{muni}")

            madhel_labels  = [r.label for r in info.runways]
            manual_hdgs    = self._manual_runways.get(info.code, [])
            manual_labels  = [f"{h:03d}°  (manual)" for h in manual_hdgs]
            labels         = madhel_labels or manual_labels
            has_data       = bool(labels)
        else:
            labels   = []
            has_data = False
            if lbl_meta:
                lbl_meta.config(text="")

        if rwy_cb is None:
            return

        if has_data:
            if rwy_manual_frm:
                rwy_manual_frm.pack_forget()
            if rwy_normal_row:
                rwy_normal_row.pack(fill="x")
            rwy_cb["values"] = labels
            if labels:
                rwy_cb.current(0)
                if var_rwy:
                    var_rwy.set(labels[0])
        else:
            if rwy_normal_row:
                rwy_normal_row.pack_forget()
            if rwy_manual_frm:
                rwy_manual_frm.pack(fill="x")

    def _sync_airport_silent(self, which: str, info) -> None:
        """
        Propaga la seleccion de un aerodromo al picker par sin abrir ni
        repoblar la listbox (evita cascadas y freezes).
        Solo actualiza el estado interno y los labels visibles.
        """
        self._syncing = True
        try:
            setattr(self, f"_{which}_selected_info", info)
            lbl_sel = getattr(self, f"_{which}_lbl_sel", None)
            if lbl_sel:
                lbl_sel.config(text=info.name, fg=C["fg"])
            lbl_meta = getattr(self, f"_{which}_lbl_meta", None)
            if lbl_meta:
                muni = f"  |  {info.municipality}" if info.municipality else ""
                lbl_meta.config(text=f"{info.code}  |  {info.elev_ft} ft AMSL{muni}")
            self._refresh_runways(which)
        finally:
            self._syncing = False

    def _get_airport_info(self, which: str):
        return getattr(self, f"_{which}_selected_info", None)

    def _get_airport_code(self, which: str) -> str:
        info = self._get_airport_info(which)
        return info.code if info else ""

    def _get_runway_heading(self, which: str) -> int:
        var_rwy = getattr(self, f"_{which}_var_rwy", None)
        info    = self._get_airport_info(which)
        if not info or not var_rwy:
            return 0
        label = var_rwy.get()
        # Buscar en pistas MADHEL
        for r in info.runways:
            if r.label == label:
                return r.heading
        # Interpretar cabecera manual (formato "180°  (manual)")
        try:
            return int(label.split("°")[0].strip())
        except (ValueError, IndexError):
            return 0

    def _set_time_now(self):
        t = (datetime.now(tz=timezone.utc) + timedelta(minutes=30)).strftime("%H:%M")
        self._var_time.set(t)

    def _tick_clock(self):
        now = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y  %H:%M UTC")
        self._lbl_utc.config(text=now)
        self.after(30_000, self._tick_clock)

    def _parse_dep_time(self) -> int:
        raw     = self._var_time.get().strip()
        now_utc = datetime.now(tz=timezone.utc)
        try:
            hh, mm = map(int, raw.split(":"))
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError()
        except Exception:
            raise ValueError(f"Hora invalida: '{raw}'.  Use formato HH:MM (ej: 19:30).")

        dep = now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dep <= now_utc:
            dep += timedelta(days=1)
        return int(dep.timestamp())

    # ── Evaluacion en thread ──────────────────────────────────────────────────

    def _on_evaluate(self):
        try:
            dep_time = self._parse_dep_time()
        except ValueError as e:
            messagebox.showerror("Entrada invalida", str(e), parent=self)
            return

        orig_id   = self._get_airport_code("orig")
        dest_id   = self._get_airport_code("dest")
        orig_rwy  = self._get_runway_heading("orig")
        dest_rwy  = self._get_runway_heading("dest")
        mock      = self._var_mock.get()
        orig_info = self._get_airport_info("orig")
        dest_info = self._get_airport_info("dest")

        if not orig_id or not dest_id:
            messagebox.showerror("Sin aerodromos",
                                 "Selecciona un aerodromo de origen y destino.",
                                 parent=self)
            return

        # Duracion calculada automaticamente: distancia / velocidad de crucero del avion seleccionado
        dist_km  = haversine_km(orig_info.lat, orig_info.lon, dest_info.lat, dest_info.lon)
        dist_nm  = dist_km * 0.539957
        duration = max(0.3, dist_nm / self._current_aircraft.cruise_kt)
        arrival_time = dep_time + int(duration * 3600)

        h = int(duration)
        m = int((duration - h) * 60)
        self._lbl_duration.config(
            text=f"Vuelo estimado: {h}h {m:02d}min  /  {dist_nm:.0f} nm",
            fg=C["fg_dim"],
        )

        self._btn_eval.config(state="disabled", text="  Evaluando...  ")
        self._lbl_status.config(text="Consultando datos meteorologicos...", fg=C["fg_dim"])
        self._set_card_pending(self._orig_card, orig_id)
        self._set_card_pending(self._dest_card, dest_id)
        self._clear_detail()

        self._engine = DecisionEngine(mock=mock, aircraft=self._current_aircraft)
        self._result_queue.clear()

        threading.Thread(
            target = self._worker,
            args   = (orig_id, orig_rwy, dep_time,
                      dest_id, dest_rwy, arrival_time, duration, mock),
            daemon = True,
        ).start()

    def _worker(self, orig_id, orig_rwy, dep_time,
                dest_id, dest_rwy, arrival_time, duration, mock):
        try:
            r_orig = self._engine.evaluate(orig_id, orig_rwy, dep_time, duration)
        except Exception as e:
            r_orig, e_orig = None, str(e)
        else:
            e_orig = ""

        try:
            r_dest = self._engine.evaluate(dest_id, dest_rwy, arrival_time, 1.0)
        except Exception as e:
            r_dest, e_dest = None, str(e)
        else:
            e_dest = ""

        # Meteorologia en ruta: usar waypoints de la ruta calculada si coincide
        route_pts = []
        try:
            from route.weather_sampler import sample_route_weather
            route_path = [orig_id, dest_id]
            last_r = self._last_route_result
            if (last_r and last_r.found
                    and last_r.path[0] == orig_id
                    and last_r.path[-1] == dest_id):
                route_path = last_r.path
            route_pts = sample_route_weather(
                path      = route_path,
                airports  = AIRPORTS,
                dep_time  = dep_time,
                cruise_kt = self._current_aircraft.cruise_kt,
                aircraft  = self._current_aircraft,
                mock      = mock,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(f"Route weather sampling fallido: {exc}")

        self._result_queue.append((r_orig, e_orig, r_dest, e_dest, route_pts))

    # ── Calculo de ruta en thread ─────────────────────────────────────────────

    def _on_calculate(self):
        orig_info = self._get_airport_info("rv_orig")
        dest_info = self._get_airport_info("rv_dest")

        if not orig_info or not dest_info:
            messagebox.showerror("Error", "Selecciona origen y destino validos.", parent=self)
            return

        if orig_info.code == dest_info.code:
            messagebox.showerror("Error", "El origen y el destino deben ser diferentes.", parent=self)
            return

        mode             = MODE_MAP.get(self._rv_mode.get(), "suggested")
        avoid_restricted = self._rv_avoid_restricted.get()
        mock             = self._var_mock.get()
        aircraft         = self._current_aircraft

        try:
            dep_time = self._parse_dep_time()
        except ValueError:
            import time as _t
            dep_time = int(_t.time()) + 1800

        self._btn_calc.config(state="disabled", text="  Calculando...  ")
        self._lbl_route_status.config(
            text=(f"Calculando {orig_info.name} -> {dest_info.name} "
                  f"(modo: {self._rv_mode.get()})..."),
            fg=C["fg_dim"],
        )
        self._route_result_queue.clear()

        threading.Thread(
            target = self._route_worker,
            args   = (orig_info.code, dest_info.code, mode, avoid_restricted,
                      aircraft, mock, dep_time),
            daemon = True,
        ).start()

    def _route_worker(self, orig: str, dest: str, mode: str, avoid_restricted: bool,
                      aircraft: AircraftProfile, mock: bool, dep_time: int = 0):
        try:
            result = optimize(
                orig, dest,
                mode                   = mode,
                avoid_restricted_zones = avoid_restricted,
                aircraft               = aircraft,
                evaluate_intermediate  = True,
                suggest_alternate      = True,
                mock                   = mock,
                dep_time               = dep_time,
                weather_reroute        = True,
            )
            err = ""
        except Exception as e:
            result = None
            err    = str(e)
        self._route_result_queue.append((result, err))

    # ── Poll de resultados ────────────────────────────────────────────────────

    def _poll_results(self):
        if self._result_queue:
            r_orig, e_orig, r_dest, e_dest, route_pts = self._result_queue.pop(0)
            self._show_results(r_orig, e_orig, r_dest, e_dest)
            self._update_route_strip(route_pts)
            self._btn_eval.config(state="normal", text="  EVALUAR  ")

        if self._route_result_queue:
            result, err = self._route_result_queue.pop(0)
            self._show_route_result(result, err)
            self._btn_calc.config(state="normal", text="  CALCULAR  ")

        self.after(150, self._poll_results)

    # ── Actualizacion de UI — EVALUACION ──────────────────────────────────────

    def _show_results(self, r_orig, e_orig, r_dest, e_dest):
        if r_orig:
            self._last_origin_result = r_orig
        if r_dest:
            self._last_dest_result = r_dest

        if r_orig:
            self._update_card(self._orig_card, r_orig)
        else:
            self._set_card_error(self._orig_card, e_orig)

        if r_dest:
            self._update_card(self._dest_card, r_dest)
        else:
            self._set_card_error(self._dest_card, e_dest)

        decisions = [r.decision for r in [r_orig, r_dest] if r]
        overall   = self._overall(decisions)
        ts        = datetime.now(tz=timezone.utc).strftime("%H:%M UTC")

        color_map = {"GO": C["go"], "CAUTION": C["caution"], "NO GO": C["nogo"]}
        self._lbl_status.config(
            text=f"Evaluado {ts}  |  Decision global: {overall}",
            fg=color_map.get(overall, C["fg_dim"]),
        )

        self._append_detail(r_orig, e_orig, "ORIGEN")
        self._append_detail(r_dest, e_dest, "DESTINO")

        self._lbl_briefing_status.config(
            text="Evaluacion lista. Calcula la ruta para un briefing completo, o genera ahora.",
            fg=C["fg_dim"],
        )

    def _update_route_strip(self, points: list):
        """Muestra la tira horizontal de puntos meteorologicos en ruta."""
        outer = self._route_strip_outer
        for w in outer.winfo_children():
            w.destroy()

        if not points:
            return

        dec_color = {"GO": C["go"], "CAUTION": C["caution"], "NO GO": C["nogo"],
                     "SIN DATOS": C["pending"]}
        dec_bg    = {"GO": C["go_dark"], "CAUTION": C["caution_dark"],
                     "NO GO": C["nogo_dark"], "SIN DATOS": C["pending_bg"]}

        tk.Label(outer, text="Ruta:", bg=C["bg_panel"],
                 fg=C["fg_dim"], font=("Segoe UI", 8)).pack(side="left", padx=(8, 4), pady=4)

        for i, pt in enumerate(points):
            if i > 0:
                tk.Label(outer, text="—", bg=C["bg_panel"],
                         fg=C["border"], font=("Segoe UI", 8)).pack(side="left")

            fg  = dec_color.get(pt.decision, C["pending"])
            bg  = dec_bg.get(pt.decision,    C["pending_bg"])
            lbl = tk.Label(
                outer,
                text    = pt.name if pt.is_airport else "·",
                bg      = bg,
                fg      = fg,
                font    = ("Segoe UI", 8, "bold"),
                padx    = 5,
                pady    = 2,
                relief  = "flat",
                cursor  = "hand2",
            )
            lbl.pack(side="left", padx=1)

            # Tooltip con detalle
            tip = f"{pt.name}\n{pt.decision}  R={pt.r_total:.2f}"
            if pt.temp_c is not None:
                tip += f"\nT={pt.temp_c:.0f}°C"
            if pt.wind_dir is not None and pt.wind_spd_kt is not None:
                tip += f"  Vto {pt.wind_dir:03d}°/{pt.wind_spd_kt:.0f}kt"
            _bind_tooltip(lbl, tip)

    def _update_card(self, card, result):
        dec       = result.decision
        bg, fg, _ = DEC_STYLES.get(dec, DEC_STYLES["---"])

        ap   = AIRPORTS.get(result.station_id)
        name = ap.name if ap else result.station_id
        card._lbl_station.config(text=f"{name}  ({result.station_id})")
        card._dec_box.config(text=dec, bg=bg, fg=fg)

        if result.score_breakdown:
            s = result.score_breakdown
            card._lbl_r.config(
                text=f"R = {result.r_total:.3f}   (NWP / Open-Meteo)",
                fg=C["fg"],
            )
            card._lbl_dom.config(
                text=f"Factor dominante: {s.dominant_factor}",
                fg=C["fg_dim"],
            )
        elif result.hard_blocked:
            brief = result.blocker_summary.split("|")[0].strip()
            card._lbl_r.config(text=f"Blocker: {brief[:48]}", fg=C["nogo"])
            card._lbl_dom.config(text="", fg=C["fg_dim"])
        elif not result.fetch_ok:
            card._lbl_r.config(text="Sin datos disponibles", fg=C["fg_dim"])
            card._lbl_dom.config(text="", fg=C["fg_dim"])

    def _set_card_pending(self, card, station_id: str):
        ap   = AIRPORTS.get(station_id)
        name = ap.name if ap else station_id
        card._lbl_station.config(text=f"{name}  ({station_id})")
        card._dec_box.config(text="...",
                             bg=DEC_STYLES["---"][0],
                             fg=DEC_STYLES["---"][1])
        card._lbl_r.config(text="")
        card._lbl_dom.config(text="")

    def _set_card_error(self, card, err: str):
        card._dec_box.config(text="ERROR", bg="#2a1a1a", fg=C["nogo"])
        card._lbl_r.config(text=err[:60], fg=C["nogo"])
        card._lbl_dom.config(text="", fg=C["fg_dim"])

    def _clear_detail(self):
        self._txt.config(state="normal")
        self._txt.delete("1.0", "end")
        self._txt.config(state="disabled")

    def _append_detail(self, result, err: str, label: str):
        self._txt.config(state="normal")
        self._txt.insert("end", f"\n{'=' * 56}\n", "header")
        self._txt.insert("end", f"  {label}\n", "header")
        self._txt.insert("end", f"{'=' * 56}\n", "header")
        if result:
            dec = result.decision
            tag = {"GO": "go", "CAUTION": "caution", "NO GO": "nogo"}.get(dec, "normal")
            self._txt.insert("end", format_decision(result), tag)

            wx = result.weather
            sb = result.score_breakdown

            # ── Componentes de viento ──────────────────────────────────────
            if wx and sb:
                try:
                    from features.crosswind import compute_crosswind
                    xw = compute_crosswind(
                        wx.wind_dir, wx.wind_spd_kt, sb.runway_heading,
                        wx.wind_gust_kt, wx.wind_variable,
                    )
                    self._txt.insert("end", "\n── Componentes de viento ──────────────────────\n", "header")
                    _DIRS = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]

                    if wx.wind_variable or wx.wind_dir is None:
                        wind_str = f"Variable  {wx.wind_spd_kt or 0:.0f} kt"
                    elif wx.wind_spd_kt == 0:
                        wind_str = "Calma"
                    else:
                        d = _DIRS[int((wx.wind_dir + 22.5) / 45) % 8]
                        gust = f"  ráfagas {wx.wind_gust_kt:.0f} kt" if wx.wind_gust_kt else ""
                        wind_str = f"{wx.wind_dir:03d}° ({d})  {wx.wind_spd_kt:.0f} kt{gust}"

                    self._txt.insert("end", f"  Viento: {wind_str}   Pista: {sb.runway_heading:03d}°\n", "normal")
                    self._txt.insert("end", f"  Crosswind : {xw.crosswind_kt:+.1f} kt", "normal")
                    if xw.crosswind_gust_kt:
                        self._txt.insert("end", f"  (ráfagas {xw.crosswind_gust_kt:.1f} kt)", "caution")
                    self._txt.insert("end", "\n", "normal")
                    if xw.headwind_kt >= 0:
                        self._txt.insert("end", f"  Headwind  : {xw.headwind_kt:.1f} kt\n", "normal")
                    else:
                        self._txt.insert("end", f"  Tailwind  : {abs(xw.headwind_kt):.1f} kt\n", "caution")
                except Exception:
                    pass

            # ── Density altitude ───────────────────────────────────────────
            if wx and wx.temp_c is not None:
                try:
                    from features.density_altitude import compute_density_altitude, advisory
                    ap = AIRPORTS.get(result.station_id)
                    if ap:
                        da = compute_density_altitude(wx.temp_c, ap.elev_ft, wx.altimeter_hpa, ap.elev_estimated)
                        adv = advisory(da)
                        adv_tag = {"NORMAL": "go", "ELEVATED": "caution", "HIGH": "nogo"}.get(adv, "normal")
                        self._txt.insert("end", "\n── Density Altitude ────────────────────────────\n", "header")
                        self._txt.insert("end",
                            f"  Temp: {wx.temp_c:.0f}°C   "
                            f"QNH: {wx.altimeter_hpa:.0f} hPa   "
                            f"Elevacion: {ap.elev_ft:,} ft\n",
                            "normal",
                        )
                        self._txt.insert("end",
                            f"  Presion: {da.pressure_alt_ft:,} ft   "
                            f"DA: {da.density_alt_ft:,} ft   →  ",
                            "normal",
                        )
                        self._txt.insert("end", f"{adv}\n", adv_tag)
                except Exception:
                    pass
        else:
            self._txt.insert("end", f"  ERROR: {err}\n", "nogo")
        self._txt.config(state="disabled")
        self._txt.see("end")

    # ── Actualizacion de UI — RUTAS ───────────────────────────────────────────

    def _show_route_result(self, result, err: str):
        if not result or not result.found:
            msg = err if err else (result.error if result else "Error desconocido")
            self._lbl_route_status.config(text=f"Error: {msg}", fg=C["nogo"])
            return

        self._last_route_result = result

        h   = int(result.total_time_h)
        m_  = int((result.total_time_h - h) * 60)
        path_str = " -> ".join(result.path)
        self._lbl_route_status.config(
            text=(f"{path_str}  |  {result.total_dist_km:.0f} km  "
                  f"{h}h {m_:02d}min  {result.total_fuel_l:.0f} L"),
            fg=C["fg"],
        )

        if self._map_widget:
            self._draw_route_on_map(
                result.path,
                blocked_path = result.blocked_path,
                weather_pts  = result.route_weather_points or [],
            )

        self._update_route_summary(result)

        self._lbl_briefing_status.config(
            text="Evaluacion y ruta listas. Presiona GENERAR BRIEFING.",
            fg=C["go"],
        )

    def _clear_map_markers(self):
        """Elimina todos los markers actuales del mapa."""
        for m in self._map_markers:
            try:
                m.delete()
            except Exception:
                pass
        self._map_markers = []

    def _draw_route_on_map(
        self,
        path         : list,
        blocked_path : list = None,
        weather_pts  : list = None,
    ):
        if not self._map_widget:
            return

        # Limpiar markers y paths anteriores
        self._clear_map_markers()
        for attr in ("_route_path_widget", "_blocked_path_widget"):
            w = getattr(self, attr, None)
            if w:
                try:
                    w.delete()
                except Exception:
                    pass
                setattr(self, attr, None)

        # Ruta bloqueada en rojo (se dibuja primero, queda debajo)
        if blocked_path:
            blocked_coords = [
                (AIRPORTS[c].lat, AIRPORTS[c].lon)
                for c in blocked_path if AIRPORTS.get(c)
            ]
            if len(blocked_coords) >= 2:
                self._blocked_path_widget = self._map_widget.set_path(
                    blocked_coords, color="#f38ba8", width=3,
                )

        # Ruta activa en verde
        coords = [(AIRPORTS[c].lat, AIRPORTS[c].lon) for c in path if AIRPORTS.get(c)]
        if len(coords) >= 2:
            self._route_path_widget = self._map_widget.set_path(
                coords, color="#a6e3a1", width=3,
            )

        # Markers de los waypoints de la ruta
        def _make_cmd(airport_info):
            def _cmd(_marker):
                self._show_airport_popup(airport_info)
            return _cmd

        for code in path:
            info = AIRPORTS.get(code)
            if info:
                m = self._map_widget.set_marker(
                    info.lat, info.lon,
                    text=code,
                    marker_color_circle="#a6e3a1",
                    marker_color_outside="#1e3a2e",
                    command=_make_cmd(info),
                )
                self._map_markers.append(m)

        # Centrar y hacer zoom
        if coords:
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            center_lat = (min(lats) + max(lats)) / 2
            center_lon = (min(lons) + max(lons)) / 2
            self._map_widget.set_position(center_lat, center_lon)
            span = max(max(lats) - min(lats), max(lons) - min(lons))
            zoom = 10 if span < 1.0 else 9 if span < 2.5 else 8 if span < 5.0 else 7
            self._map_widget.set_zoom(zoom)

        # Alternativas: 3 cercanas (naranja) + 5 lejanas (azul) al destino
        dest_code = path[-1]
        dest_info = AIRPORTS.get(dest_code)
        if not dest_info:
            return

        path_set  = set(path)
        distances = []
        for code, info in AIRPORTS.items():
            if code in path_set:
                continue
            dist = haversine_km(dest_info.lat, dest_info.lon, info.lat, info.lon)
            distances.append((dist, code, info))
        distances.sort(key=lambda x: x[0])

        # 3 mas cercanas al destino (hasta 150 km)
        nearby = [(d, c, i) for d, c, i in distances if d <= 150][:3]
        nearby_codes = {c for _, c, _ in nearby}

        # 5 relativamente lejanas (150-500 km)
        far = [(d, c, i) for d, c, i in distances
               if 150 < d <= 500 and c not in nearby_codes][:5]

        for dist, code, info in nearby:
            m = self._map_widget.set_marker(
                info.lat, info.lon,
                text=f"{code} ({dist:.0f}km)",
                marker_color_circle="#fab387",
                marker_color_outside="#3a2a1e",
                command=_make_cmd(info),
            )
            self._map_markers.append(m)

        for dist, code, info in far:
            m = self._map_widget.set_marker(
                info.lat, info.lon,
                text=f"{code} ({dist:.0f}km)",
                marker_color_circle="#89b4fa",
                marker_color_outside="#1e2a3a",
                command=_make_cmd(info),
            )
            self._map_markers.append(m)

    def _update_route_summary(self, result):
        txt = self._route_summary_txt
        txt.config(state="normal")
        txt.delete("1.0", "end")

        h  = int(result.total_time_h)
        m_ = int((result.total_time_h - h) * 60)

        if result.aircraft:
            ac = result.aircraft
            txt.insert("end", f"Aeronave: {ac.name}\n", "hdr")
            txt.insert("end", f"  {ac.cruise_kt:.0f} kt  |  {ac.fuel_flow_lph:.0f} L/hr  |  rango ~{ac.range_km:.0f} km\n\n", "dim")

        txt.insert("end", f"Modo: {result.mode.upper()}\n", "hdr")

        if result.was_rerouted and result.blocked_path:
            txt.insert("end", "\n[!] RUTA MODIFICADA POR METEO\n", "danger")
            txt.insert("end", "  Original bloqueada (NO GO):\n", "dim")
            txt.insert("end", f"  {' -> '.join(result.blocked_path)}\n", "danger")
            txt.insert("end", "  Ruta alternativa calculada (ver mapa)\n\n", "warn")
        elif result.was_rerouted:
            txt.insert("end", "\n[!] Ruta modificada por condiciones meteorologicas\n\n", "warn")
        else:
            txt.insert("end", "\n", "dim")

        txt.insert("end", f"Distancia total : {result.total_dist_km:.1f} km\n")
        txt.insert("end", f"Tiempo total    : {h}h {m_:02d}min\n")
        txt.insert("end", f"Combustible     : {result.total_fuel_l:.1f} L\n")

        fuel_tag = "ok" if result.fuel_ok else "danger"
        fuel_txt = "OK" if result.fuel_ok else "INSUFICIENTE"
        txt.insert("end", "Combustible ok  : ")
        txt.insert("end", f"{fuel_txt}\n", fuel_tag)

        stop_tag = "warn" if result.needs_fuel_stop else "ok"
        stop_txt = "SI"   if result.needs_fuel_stop else "NO"
        txt.insert("end", "Escala comb.    : ")
        txt.insert("end", f"{stop_txt}\n", stop_tag)

        txt.insert("end", f"\nTRAMOS ({len(result.legs)}):\n", "hdr")
        for leg in result.legs:
            h_l = int(leg.time_hours)
            m_l = int((leg.time_hours - h_l) * 60)
            txt.insert("end", f"  {leg.origin}->{leg.dest}\n", "dim")
            txt.insert("end",
                f"    {leg.distance_km:.1f}km  {h_l}h{m_l:02d}m  "
                f"brg={leg.bearing_deg:.0f}  {leg.fuel_liters:.1f}L\n")

        if result.airspace_conflicts:
            txt.insert("end", f"\nESPACIO AEREO ({len(result.airspace_conflicts)}):\n", "hdr")
            for z in result.airspace_conflicts:
                if z.is_restricted:
                    tag = "danger"; flag = "[R]"
                elif z.is_controlled:
                    tag = "warn";   flag = "[C]"
                else:
                    tag = "warn";   flag = "[D]"
                txt.insert("end", f"  {flag} ", tag)
                txt.insert("end", f"{z.name}\n")
        else:
            txt.insert("end", "\nEspacio aereo: sin conflictos\n", "ok")

        if result.intermediate_results:
            txt.insert("end", f"\nINTERMEDIOS ({len(result.intermediate_results)}):\n", "hdr")
            for ir in result.intermediate_results:
                dec_tag = {"GO": "ok", "CAUTION": "warn", "NO GO": "danger"}.get(ir.decision, "dim")
                txt.insert("end", f"  {ir.code} - {ir.name}\n", "dim")
                txt.insert("end", "    ")
                txt.insert("end", f"{ir.decision}", dec_tag)
                txt.insert("end", f"  R={ir.r_total:.2f}\n")
        elif len(result.path) <= 2:
            txt.insert("end", "\nRuta directa (sin intermedios)\n", "dim")

        if result.alternate:
            alt = result.alternate
            dec_tag = {"GO": "ok", "CAUTION": "warn", "NO GO": "danger"}.get(alt.decision, "dim")
            h_a = int(alt.time_from_dest_h)
            m_a = int((alt.time_from_dest_h - h_a) * 60)
            txt.insert("end", "\nALTERNATIVO SUGERIDO:\n", "hdr")
            txt.insert("end", f"  {alt.code} - {alt.name}\n", "dim")
            txt.insert("end", "  ")
            txt.insert("end", f"{alt.decision}", dec_tag)
            txt.insert("end",
                f"  R={alt.r_total:.2f}  "
                f"{alt.dist_from_dest_km:.0f}km  {h_a}h{m_a:02d}m desde destino\n")
        else:
            txt.insert("end", "\nAlternativo: no calculado\n", "dim")

        txt.insert("end", "\n-- Leyenda mapa --\n", "dim")
        txt.insert("end", "  Naranja: cercano al destino (<150km)\n", "dim")
        txt.insert("end", "  Azul: desvio por meteo (150-500km)\n", "dim")

        txt.config(state="disabled")
        txt.see("1.0")

    # ── Zonas de espacio aereo restringido en el mapa ────────────────────────

    def _draw_airspace_zones(self):
        """Dibuja en el mapa todos los espacios aereos R/P/D con sus poligonos reales."""
        if not self._map_widget:
            return
        from data.airspace import AIRSPACE_ZONES
        for zone in AIRSPACE_ZONES:
            if zone.zone_type not in ("R", "P", "D"):
                continue
            if zone.polygon is None:
                continue
            color = C["nogo"] if zone.is_restricted else C["caution"]  # R/P=rojo, D=naranja
            try:
                poly = self._map_widget.set_polygon(
                    list(zone.polygon),
                    fill_color="",
                    outline_color=color,
                    border_width=2,
                    name=zone.name,
                )
                self._map_zone_polygons.append(poly)
            except Exception:
                pass

    # ── Ficha de aerodromo (popup interactivo del mapa) ───────────────────────

    def _show_airport_popup(self, info):
        """Abre una ficha flotante con todos los datos del aerodromo."""
        if self._airport_popup and self._airport_popup.winfo_exists():
            self._airport_popup.destroy()

        popup = tk.Toplevel(self)
        self._airport_popup = popup
        popup.title(f"{info.code}  —  {info.name}")
        popup.configure(bg=C["bg"])
        popup.resizable(True, True)
        popup.transient(self)
        popup.minsize(480, 200)

        # Canvas + scrollbar para contenido variable
        canvas = tk.Canvas(popup, bg=C["bg"], highlightthickness=0)
        vsb    = tk.Scrollbar(popup, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=C["bg"])
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(inner_id, width=canvas.winfo_width())

        inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(inner_id, width=e.width))

        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        popup.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # ── helpers ──────────────────────────────────────────────────────────
        def _sep():
            tk.Frame(inner, bg=C["border"], height=1).pack(fill="x", padx=14, pady=(6, 0))

        def _section(title):
            f = tk.Frame(inner, bg=C["bg"], padx=14, pady=2)
            f.pack(fill="x")
            tk.Label(f, text=title.upper(),
                     bg=C["bg"], fg=C["fg_dim"],
                     font=("Segoe UI", 8, "bold"), anchor="w",
                     ).pack(fill="x")
            return f

        def _row(parent, label, value, mono=False):
            r = tk.Frame(parent, bg=C["bg"])
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label,
                     bg=C["bg"], fg=C["fg_dim"],
                     font=("Segoe UI", 9), width=18, anchor="w",
                     ).pack(side="left")
            font = ("Consolas", 9) if mono else ("Segoe UI", 9)
            tk.Label(r, text=str(value),
                     bg=C["bg"], fg=C["fg"],
                     font=font, anchor="w", wraplength=320, justify="left",
                     ).pack(side="left", fill="x")

        # ── Cabecera ──────────────────────────────────────────────────────────
        hdr = tk.Frame(inner, bg=C["bg_panel"], padx=16, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text=info.name,
                 bg=C["bg_panel"], fg=C["fg_accent"],
                 font=("Segoe UI", 12, "bold"), anchor="w",
                 ).pack(fill="x")

        # Linea de codigos
        codes_parts = [info.code]
        if hasattr(info, "local_id") and info.local_id and info.local_id != info.code:
            codes_parts.append(f"local: {info.local_id}")
        if info.icao_code and info.icao_code != info.code:
            codes_parts.append(f"ICAO: {info.icao_code}")
        if hasattr(info, "iata_code") and info.iata_code:
            codes_parts.append(f"IATA: {info.iata_code}")
        tk.Label(hdr, text="  /  ".join(codes_parts),
                 bg=C["bg_panel"], fg=C["fg_dim"],
                 font=("Segoe UI", 9), anchor="w",
                 ).pack(fill="x")

        # Estado operacional
        cond_parts = []
        if hasattr(info, "condition") and info.condition:
            cond_parts.append(info.condition.capitalize())
        if hasattr(info, "control") and info.control:
            cond_parts.append("Controlado" if "CONTROL" in info.control.upper()
                              else "No controlado")
        if cond_parts:
            color = C["caution"] if "No control" in (cond_parts[-1] if cond_parts else "") else C["go"]
            tk.Label(hdr, text="  ·  ".join(cond_parts),
                     bg=C["bg_panel"], fg=color,
                     font=("Segoe UI", 9), anchor="w",
                     ).pack(fill="x")

        # ── Ubicacion ─────────────────────────────────────────────────────────
        _sep()
        frm = _section("Ubicacion")

        _row(frm, "Coordenadas:", f"{info.lat:.6f},  {info.lon:.6f}", mono=True)

        elev_txt = f"{info.elev_ft} ft AMSL"
        if hasattr(info, "elev_ft") and info.elev_ft > 0:
            elev_m = int(round(info.elev_ft / 3.28084))
            elev_txt = f"{info.elev_ft} ft  ({elev_m} m) AMSL"
        if info.elev_estimated:
            elev_txt += "  (estimada)"
        _row(frm, "Elevacion:", elev_txt)

        if info.province:
            _row(frm, "Provincia:", info.province)
        if info.municipality:
            _row(frm, "Municipio:", info.municipality)

        # ── Pistas ────────────────────────────────────────────────────────────
        _sep()
        rwy_frm = _section("Pistas")
        if info.runways:
            for rwy in info.runways:
                tk.Label(rwy_frm, text=f"  {rwy.label}",
                         bg=C["bg"], fg=C["fg"],
                         font=("Consolas", 9), anchor="w",
                         ).pack(fill="x")
                if hasattr(rwy, "thr_lat") and rwy.thr_lat is not None:
                    thr_str = f"    umbral: {rwy.thr_lat:.6f},  {rwy.thr_lon:.6f}"
                    tk.Label(rwy_frm, text=thr_str,
                             bg=C["bg"], fg=C["fg_dim"],
                             font=("Consolas", 8), anchor="w",
                             ).pack(fill="x")
        else:
            tk.Label(rwy_frm, text="  Sin datos de pista disponibles",
                     bg=C["bg"], fg=C["fg_dim"],
                     font=("Segoe UI", 9), anchor="w",
                     ).pack(fill="x")

        # ── Servicios ─────────────────────────────────────────────────────────
        has_services = (
            (hasattr(info, "fuel") and info.fuel)
            or (hasattr(info, "schedule") and info.schedule)
        )
        if has_services:
            _sep()
            svc_frm = _section("Servicios")
            if hasattr(info, "fuel") and info.fuel:
                _row(svc_frm, "Combustible:", info.fuel)
            if hasattr(info, "schedule") and info.schedule:
                _row(svc_frm, "Horario:", info.schedule)

        # ── Contacto ──────────────────────────────────────────────────────────
        if hasattr(info, "phones") and info.phones:
            _sep()
            tel_frm = _section("Contacto")
            for ph in info.phones:
                tk.Label(tel_frm, text=f"  {ph}",
                         bg=C["bg"], fg=C["fg"],
                         font=("Segoe UI", 9), anchor="w",
                         ).pack(fill="x")

        # ── Normas particulares ───────────────────────────────────────────────
        if hasattr(info, "norms_particular") and info.norms_particular:
            _sep()
            nrm_frm = _section("Normas particulares")
            nrm_txt = tk.Text(nrm_frm,
                              bg=C["bg_input"], fg=C["fg"],
                              font=("Segoe UI", 8),
                              relief="flat", wrap="word",
                              height=4, padx=6, pady=4,
                              state="normal")
            nrm_txt.insert("1.0", info.norms_particular)
            nrm_txt.config(state="disabled")
            nrm_txt.pack(fill="x", padx=0, pady=(2, 0))

        # ── Fuente de datos ───────────────────────────────────────────────────
        src_txt = "MADHEL (ANAC)"
        tk.Label(inner, text=f"Fuente: {src_txt}",
                 bg=C["bg"], fg=C["fg_dim"],
                 font=("Segoe UI", 7), anchor="e",
                 ).pack(fill="x", padx=14, pady=(8, 0))

        # ── Boton cerrar ──────────────────────────────────────────────────────
        tk.Button(inner, text="Cerrar",
                  command=popup.destroy,
                  bg=C["bg_panel"], fg=C["fg_dim"],
                  font=("Segoe UI", 9), relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  activebackground=C["border"],
                  ).pack(pady=(6, 12))

        popup.update_idletasks()
        h = min(popup.winfo_reqheight() + 20, 700)
        popup.geometry(f"500x{h}")

    # ── Cambio de alternativo ─────────────────────────────────────────────────

    def _on_change_alternate(self):
        if not self._last_route_result or not self._last_route_result.found:
            messagebox.showinfo("Sin ruta", "Primero calcula una ruta.", parent=self)
            return

        current_dest = self._last_route_result.path[-1]
        current_orig = self._last_route_result.path[0]

        dlg = tk.Toplevel(self)
        dlg.title("Seleccionar alternativo")
        dlg.configure(bg=C["bg"])
        dlg.geometry("440x320")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text="Buscar aerodromo alternativo:",
                 bg=C["bg"], fg=C["fg"], font=("Segoe UI", 10)
                 ).pack(pady=(14, 4), padx=20, anchor="w")

        # Buscador dentro del dialog
        var_search = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=var_search, width=36,
                         bg=C["bg_widget"], fg=C["fg"],
                         insertbackground=C["fg"], relief="flat", bd=3,
                         font=("Segoe UI", 10))
        entry.pack(padx=20, pady=(0, 4), fill="x")

        lb_frame = tk.Frame(dlg, bg=C["bg"])
        lb_frame.pack(padx=20, fill="both", expand=True)

        lb = tk.Listbox(
            lb_frame, height=8,
            bg=C["bg_widget"], fg=C["fg"],
            selectbackground=C["bg_panel"],
            selectforeground=C["fg_accent"],
            font=("Segoe UI", 9),
            relief="flat", bd=0, activestyle="none",
        )
        sb_dlg = ttk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb_dlg.set)
        lb.pack(side="left", fill="both", expand=True)
        sb_dlg.pack(side="right", fill="y")

        # Datos de la listbox del dialog
        dlg_lb_data = []

        def populate_dlg(query=""):
            results = search_airports(query)[:100]
            lb.delete(0, "end")
            dlg_lb_data.clear()
            for info in results:
                if info.code in (current_orig, current_dest):
                    continue
                muni = f" — {info.municipality}" if info.municipality else ""
                lb.insert("end", f"{info.name}{muni}  ({info.code})")
                dlg_lb_data.append(info)
            if dlg_lb_data:
                lb.selection_set(0)

        populate_dlg()
        var_search.trace_add("write", lambda *_: populate_dlg(var_search.get()))

        def _confirm():
            sel = lb.curselection()
            if not sel or sel[0] >= len(dlg_lb_data):
                dlg.destroy()
                return
            info = dlg_lb_data[sel[0]]
            dlg.destroy()
            self._apply_alternate_override(info.code)

        btn_row = tk.Frame(dlg, bg=C["bg"])
        btn_row.pack(pady=10)
        tk.Button(btn_row, text="Confirmar", command=_confirm,
                  bg=C["btn_eval"], fg=C["btn_eval_fg"],
                  font=("Segoe UI", 10, "bold"), relief="flat", padx=12, pady=4,
                  cursor="hand2").pack(side="left", padx=8)
        tk.Button(btn_row, text="Cancelar", command=dlg.destroy,
                  bg=C["bg_panel"], fg=C["fg_dim"],
                  font=("Segoe UI", 10), relief="flat", padx=12, pady=4,
                  cursor="hand2").pack(side="left", padx=8)

    def _apply_alternate_override(self, alt_code: str):
        if not self._last_route_result:
            return
        mock = self._var_mock.get()
        ac   = self._current_aircraft

        self._btn_change_alt.config(state="disabled", text="Evaluando...")

        def _worker():
            from route.optimizer import _evaluate_airport, AlternateInfo
            from route.performance import leg_time_hours
            decision, r_val = _evaluate_airport(alt_code, ac, mock=mock)
            dest_code = self._last_route_result.path[-1]
            dest_ap   = AIRPORTS.get(dest_code)
            alt_ap    = AIRPORTS.get(alt_code)
            if dest_ap and alt_ap:
                dist   = haversine_km(dest_ap.lat, dest_ap.lon, alt_ap.lat, alt_ap.lon)
                time_h = leg_time_hours(dist, ac.cruise_kt)
            else:
                dist   = 0.0
                time_h = 0.0
            new_alt = AlternateInfo(
                code              = alt_code,
                name              = alt_ap.name if alt_ap else alt_code,
                decision          = decision,
                r_total           = round(r_val, 3),
                dist_from_dest_km = round(dist, 1),
                time_from_dest_h  = round(time_h, 3),
            )
            from dataclasses import replace
            self._last_route_result = replace(self._last_route_result, alternate=new_alt)
            self.after(0, self._finish_alternate_override)

        threading.Thread(target=_worker, daemon=True).start()

    def _finish_alternate_override(self):
        self._btn_change_alt.config(state="normal", text="Cambiar alternativo")
        if self._last_route_result:
            self._update_route_summary(self._last_route_result)

    # ── Tab BRIEFING — generacion ─────────────────────────────────────────────

    def _on_generate_briefing(self):
        if not self._last_origin_result or not self._last_dest_result:
            messagebox.showinfo(
                "Sin datos",
                "Primero evalua las condiciones meteorologicas en la tab EVALUACION.",
                parent=self,
            )
            return

        self._btn_briefing.config(state="disabled", text="  Generando...  ")

        mock = self._var_mock.get()

        def _worker():
            try:
                from data.fetcher_aviationweather import AviationWeatherFetcher
                av_fetcher = AviationWeatherFetcher(mock=mock)

                def _get_icao(station_id):
                    ap = AIRPORTS.get(station_id)
                    if ap and getattr(ap, "icao_code", None):
                        return ap.icao_code
                    return station_id  # intentar directamente con el código

                notams_orig = av_fetcher.get_notams(_get_icao(self._last_origin_result.station_id))
                notams_dest = av_fetcher.get_notams(_get_icao(self._last_dest_result.station_id))

                text = generate_briefing(
                    self._last_origin_result,
                    self._last_dest_result,
                    self._last_route_result,
                    notams_orig=notams_orig,
                    notams_dest=notams_dest,
                )
            except Exception as exc:
                text = f"Error al generar el briefing:\n{exc}"
            self.after(0, lambda: self._show_briefing(text))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_briefing(self, text: str):
        self._btn_briefing.config(state="normal", text="  GENERAR BRIEFING  ")

        txt = self._briefing_txt
        txt.config(state="normal")
        txt.delete("1.0", "end")

        for line in text.splitlines(keepends=True):
            upper = line.upper()
            if any(k in upper for k in ("NO REALIZAR", "NO GO", "CRITICO", "BLOQUEADOR")):
                tag = "nogo"
            elif any(k in upper for k in ("PRECAUCION", "CAUTION", "MARGINAL")):
                tag = "caution"
            elif any(k in upper for k in ("VUELO APTO", "GO (APTO)", "SIN CONFLICTOS")):
                tag = "go"
            elif line.startswith("---") or line.startswith("==="):
                tag = "hdr"
            elif line.isupper() and len(line.strip()) > 3:
                tag = "hdr"
            else:
                tag = ""
            if tag:
                txt.insert("end", line, tag)
            else:
                txt.insert("end", line)

        txt.config(state="disabled")
        txt.see("1.0")
        self._notebook.select(3)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _overall(decisions: list) -> str:
        if not decisions:
            return "---"
        if "NO GO" in decisions:
            return "NO GO"
        if "CAUTION" in decisions:
            return "CAUTION"
        return "GO"


# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

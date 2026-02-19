# cn616a_log_viewer_tabs_pv_only.py
#
# PV-only plot per zone (tabs). Live “tail” updates from the end of the file.
# Right-side margin shows *latest (last line)* values for:
#   PV, SP_abs, Output, Method, Mode, Autotune, Autotune Setpoint
#
# - Opens the most recent cn616a_log_*.jsonl in the script folder by default
# - Or choose any log file via "Open log..."
# - "Clear plot" button per zone (clears the PV trace only; status text remains live)
#
# Requirements:
#   pip install matplotlib
#
# Run:
#   python cn616a_log_viewer_tabs_pv_only.py

from __future__ import annotations

import json
import os
import re
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Los_Angeles")  # Pacific time
except Exception:
    LOCAL_TZ = None


LOG_GLOB = "cn616a_log_*.jsonl"
DEFAULT_ZONES = [1, 2, 3, 4, 5, 6]
TAIL_POLL_MS = 800  # update frequency for tailing the log file


def is_nan(x: Any) -> bool:
    try:
        return x is None or (isinstance(x, float) and (x != x))  # NaN check
    except Exception:
        return False


def fmt_float(x: Any, nd: int = 2) -> str:
    if x is None or is_nan(x):
        return "—"
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)


def epoch_to_dt_local(t_epoch_s: float) -> datetime:
    dt_utc = datetime.fromtimestamp(float(t_epoch_s), tz=timezone.utc)
    if LOCAL_TZ is not None:
        return dt_utc.astimezone(LOCAL_TZ)
    return dt_utc.astimezone()  # system local


def find_most_recent_log(search_dir: Path) -> Optional[Path]:
    candidates = list(search_dir.glob(LOG_GLOB))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


@dataclass
class ZoneSeries:
    zone: int
    t: List[datetime] = field(default_factory=list)
    pv: List[float] = field(default_factory=list)

    # Last-known status (from the last telemetry line for this zone)
    last_pv: Any = None
    last_sp: Any = None
    last_out: Any = None
    last_method: str = "—"
    last_mode: str = "—"
    last_autotune: Any = None
    last_autotune_sp: Any = None  # may arrive via telemetry or an event

    def clear_trace(self) -> None:
        self.t.clear()
        self.pv.clear()


class ZoneTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, series: ZoneSeries):
        super().__init__(parent)
        self.series = series

        # Layout: left = plot, right = status margin
        self.main = ttk.Frame(self)
        self.main.pack(side="top", fill="both", expand=True, padx=8, pady=8)

        self.left = ttk.Frame(self.main)
        self.left.pack(side="left", fill="both", expand=True)

        self.right = ttk.Frame(self.main)
        self.right.pack(side="right", fill="y", padx=(10, 0))

        # Buttons (top-left)
        btn_row = ttk.Frame(self.left)
        btn_row.pack(side="top", fill="x", pady=(0, 6))

        self.clear_btn = ttk.Button(btn_row, text="Clear plot", command=self.on_clear)
        self.clear_btn.pack(side="left")

        # PV-only figure
        self.fig = Figure(figsize=(7.2, 4.8), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title(f"Zone {series.zone} PV")
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("PV (°C)")
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        self.ax.grid(True)

        (self.line_pv,) = self.ax.plot([], [], label="PV (°C)")
        self.ax.legend(loc="upper left")

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.left)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        toolbar = NavigationToolbar2Tk(self.canvas, self.left)
        toolbar.update()

        # Status margin (right)
        title = ttk.Label(self.right, text="Latest (last line)", font=("Segoe UI", 10, "bold"))
        title.pack(side="top", anchor="w")

        self.status_var = tk.StringVar(value="")
        self.status_lbl = ttk.Label(
            self.right,
            textvariable=self.status_var,
            justify="left",
            font=("Consolas", 10),
        )
        self.status_lbl.pack(side="top", anchor="w", pady=(6, 0))

        self.update_status_text()

    def on_clear(self) -> None:
        self.series.clear_trace()
        self.redraw(full_rescale=True)

    def update_status_text(self) -> None:
        s = self.series
        autotune_yes = bool(s.last_autotune)
        autotune_txt = "YES" if autotune_yes else "NO"
        auto_sp_txt = fmt_float(s.last_autotune_sp, 1) if autotune_yes else "—"

        # fixed-width style
        lines = [
            f"PV (°C)     : {fmt_float(s.last_pv, 2)}",
            f"SP_abs (°C) : {fmt_float(s.last_sp, 2)}",
            f"Output (%)  : {fmt_float(s.last_out, 2)}",
            f"Method      : {s.last_method or '—'}",
            f"Mode        : {s.last_mode or '—'}",
            f"Autotune    : {autotune_txt}",
            f"AT SP (°C)  : {auto_sp_txt}",
        ]
        self.status_var.set("\n".join(lines))
        
    def redraw(self, full_rescale: bool = False) -> None:
        s = self.series

        if not s.t:
            self.line_pv.set_data([], [])
            self.ax.relim()
            self.ax.autoscale_view()
            self.update_status_text()
            self.canvas.draw_idle()
            return

        # Update PV line
        x = mdates.date2num(s.t)
        y = s.pv
        self.line_pv.set_data(x, y)

        # Always keep x showing the full available range (or whatever you prefer)
        xmin, xmax = float(min(x)), float(max(x))
        self.ax.set_xlim(xmin, xmax)

        # Compute y-limits from *visible* data (within current x-limits)
        x0, x1 = self.ax.get_xlim()
        y_vis = [
            float(yy) for xx, yy in zip(x, y)
            if (x0 <= float(xx) <= x1) and (yy is not None) and (not (isinstance(yy, float) and yy != yy))
        ]

        if y_vis:
            ymin = min(y_vis)
            ymax = max(y_vis)

            # Padding based on data range (NOT on current axis limits)
            yr = ymax - ymin
            pad = 0.05 * yr if yr > 0 else 0.5  # if flat, give a small fixed cushion
            self.ax.set_ylim(ymin - pad, ymax + pad)

        # Update status and redraw
        self.update_status_text()
        self.canvas.draw_idle()




class CN616ALogViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CN616A PV Viewer (Zones as Tabs)")
        self.geometry("1150x780")

        self.current_log_path: Optional[Path] = None
        self._tail_fp = None
        self._tail_pos = 0

        self.series_by_zone: Dict[int, ZoneSeries] = {z: ZoneSeries(zone=z) for z in DEFAULT_ZONES}
        self.tabs_by_zone: Dict[int, ZoneTab] = {}

        self._build_ui()

        # Open most recent log in script folder by default
        script_dir = Path(__file__).resolve().parent
        logs_dir = script_dir / "logs"   # <--- NEW

        most_recent = find_most_recent_log(logs_dir)

        if most_recent is not None:
            self.open_log(most_recent)
        else:
            self.set_status(f"No {LOG_GLOB} found in {script_dir}. Use Open log...")

        self.after(TAIL_POLL_MS, self._tail_tick)

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=8, pady=8)

        self.status_bar = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.status_bar).pack(side="left", fill="x", expand=True)

        ttk.Button(top, text="Open log...", command=self.on_open_dialog).pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 8))

        for z in DEFAULT_ZONES:
            tab = ZoneTab(self.nb, self.series_by_zone[z])
            self.nb.add(tab, text=f"Zone {z}")
            self.tabs_by_zone[z] = tab

    def set_status(self, msg: str) -> None:
        self.status_bar.set(msg)

    def on_open_dialog(self) -> None:
        initial_dir = str(self.current_log_path.parent) if self.current_log_path else str(Path.cwd())
        file_path = filedialog.askopenfilename(
            title="Open CN616A JSONL log",
            initialdir=initial_dir,
            filetypes=[("JSONL logs", "*.jsonl"), ("All files", "*.*")],
        )
        if file_path:
            self.open_log(Path(file_path))

    def _close_tail_file(self) -> None:
        try:
            if self._tail_fp is not None:
                self._tail_fp.close()
        except Exception:
            pass
        self._tail_fp = None
        self._tail_pos = 0

    def open_log(self, path: Path) -> None:
        if not path.exists():
            self.set_status(f"File not found: {path}")
            return

        self._close_tail_file()
        self.current_log_path = path

        # Reset series & last-known values
        for s in self.series_by_zone.values():
            s.clear_trace()
            s.last_pv = None
            s.last_sp = None
            s.last_out = None
            s.last_method = "—"
            s.last_mode = "—"
            s.last_autotune = None
            s.last_autotune_sp = None

        # Load entire file once
        n_lines = 0
        n_tel = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    n_lines += 1
                    if '"type": "telemetry"' in line:
                        n_tel += 1
                    self._ingest_line(line)
        except Exception as e:
            self.set_status(f"Failed to read log: {e}")
            return

        # Start tailing from end
        try:
            self._tail_fp = open(path, "r", encoding="utf-8")
            self._tail_fp.seek(0, os.SEEK_END)
            self._tail_pos = self._tail_fp.tell()
        except Exception as e:
            self.set_status(f"Opened, but failed to tail file: {e}")

        # Redraw all tabs
        for tab in self.tabs_by_zone.values():
            tab.redraw(full_rescale=True)

        self.set_status(f"Opened: {path.name}  |  lines={n_lines} telemetry={n_tel}")

    def _ensure_zone(self, z: int) -> None:
        if z in self.series_by_zone:
            return
        self.series_by_zone[z] = ZoneSeries(zone=z)
        tab = ZoneTab(self.nb, self.series_by_zone[z])
        self.nb.add(tab, text=f"Zone {z}")
        self.tabs_by_zone[z] = tab

    def _ingest_line(self, line: str) -> Optional[int]:
        """Returns zone if telemetry line parsed, else None."""
        line = line.strip()
        if not line:
            return None

        try:
            obj = json.loads(line)
        except Exception:
            return None

        typ = obj.get("type")

        # Optional event path: capture autotune setpoint if your logger writes it
        if typ == "event":
            z = obj.get("zone")
            if isinstance(z, int):
                self._ensure_zone(z)
                sp = (
                    obj.get("autotune_sp_c")
                    or obj.get("autotune_setpoint_c")
                    or obj.get("autotune_setpoint")
                    or obj.get("sp")
                    or obj.get("setpoint")
                )
                if sp is not None:
                    self.series_by_zone[z].last_autotune_sp = sp
            return None

        if typ != "telemetry":
            return None

        z = obj.get("zone")
        if not isinstance(z, int):
            return None
        self._ensure_zone(z)

        s = self.series_by_zone[z]

        # time
        t_epoch = obj.get("t_epoch_s")
        if t_epoch is None:
            ts = obj.get("ts")
            if isinstance(ts, str):
                try:
                    # if ts contains offset, keep it; else treat as naive local
                    dt = datetime.fromisoformat(ts)
                except Exception:
                    return z
            else:
                return z
        else:
            try:
                dt = epoch_to_dt_local(float(t_epoch))
            except Exception:
                return z

        pv = obj.get("pv_c")
        sp = obj.get("sp_abs_c")
        out = obj.get("output_pct")

        pv_val = None if is_nan(pv) else float(pv)

        # Append PV trace (PV-only plot)
        if pv_val is not None:
            s.t.append(dt)
            s.pv.append(pv_val)

        # Update last-known status from THIS line
        s.last_pv = pv
        s.last_sp = sp
        s.last_out = out
        s.last_method = obj.get("method", "—")
        s.last_mode = obj.get("mode", "—")
        s.last_autotune = obj.get("autotune", None)

        # If telemetry includes autotune setpoint, capture it (future-proof)
        s.last_autotune_sp = (
            obj.get("autotune_sp_c")
            or obj.get("autotune_setpoint_c")
            or obj.get("autotune_setpoint")
            or s.last_autotune_sp
        )

        return z

    def _tail_tick(self) -> None:
        try:
            if self._tail_fp is not None and self.current_log_path is not None:
                self._tail_fp.seek(self._tail_pos, os.SEEK_SET)
                new_lines = self._tail_fp.readlines()
                if new_lines:
                    self._tail_pos = self._tail_fp.tell()

                    zones_touched = set()
                    for line in new_lines:
                        # fast zone guess
                        z_guess = None
                        if '"type": "telemetry"' in line:
                            m = re.search(r'"zone"\s*:\s*(\d+)', line)
                            if m:
                                try:
                                    z_guess = int(m.group(1))
                                except Exception:
                                    z_guess = None

                        z = self._ingest_line(line)
                        if isinstance(z, int):
                            zones_touched.add(z)
                        elif isinstance(z_guess, int):
                            zones_touched.add(z_guess)

                    for z in zones_touched:
                        tab = self.tabs_by_zone.get(z)
                        if tab:
                            tab.redraw(full_rescale=False)

        except Exception as e:
            self.set_status(f"Tailing error: {e}")

        self.after(TAIL_POLL_MS, self._tail_tick)


def main() -> int:
    app = CN616ALogViewer()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

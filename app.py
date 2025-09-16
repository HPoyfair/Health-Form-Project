# app.py
import csv
import os
import sys
import json
import time
import shutil
import ctypes
import hashlib
import platform
import tempfile
import threading
import subprocess
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---------------------------------------------------------------------------
# App constants
# ---------------------------------------------------------------------------
APP_NAME = "HealthForm"
APP_VERSION = "0.1.8"  # bump per release
UPDATE_MANIFEST_URL = (
    "https://gist.githubusercontent.com/HPoyfair/429ed78559d6247b16f8386acb6e8330/raw/manifest.json"
)
COLOR_BG = "#1e90ff"  # DodgerBlue

# ---------------------------------------------------------------------------
# Drag & Drop (optional)
# ---------------------------------------------------------------------------
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
    BaseTk = TkinterDnD.Tk
except Exception:
    DND_AVAILABLE = False
    DND_FILES = None
    BaseTk = tk.Tk


# ========================= Self-replace bootstrap ============================
def _resource_path(name: str) -> str:
    """Return absolute path to resource (works in PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / name)


def _known_desktop_dir() -> Path:
    """
    Try to get the real Desktop path on Windows (localized / redirected).
    Fallback to ~/Desktop elsewhere.
    """
    if platform.system() != "Windows":
        return Path.home() / "Desktop"

    # SHGetKnownFolderPath(FOLDERID_Desktop, 0, 0, *ppszPath)
    try:
        from uuid import UUID

        _ole32 = ctypes.windll.ole32
        _shell32 = ctypes.windll.shell32
        _ole32.CoTaskMemFree.restype = None

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

            def __init__(self, uuidstr):
                u = UUID(uuidstr)
                ctypes.Structure.__init__(
                    self,
                    u.fields[0],
                    u.fields[1],
                    u.fields[2],
                    (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]),
                )

        # FOLDERID_Desktop
        fid = GUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")
        ppath = ctypes.c_wchar_p()
        if _shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, 0, ctypes.byref(ppath)) == 0:
            p = Path(ppath.value)
            _ole32.CoTaskMemFree(ppath)
            return p
    except Exception:
        pass

    return Path.home() / "Desktop"


def _create_or_update_shortcut(target_exe: Path, icon_path: Path | None = None):
    """
    Create/refresh a desktop shortcut named APP_NAME.lnk pointing to target_exe.
    Uses PowerShell (no extra Python packages required).
    """
    if platform.system() != "Windows":
        return

    desktop = _known_desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    lnk_path = desktop / f"{APP_NAME}.lnk"

    # Build a tiny PowerShell script to (re)create the shortcut.
    ps = (
        "$W = New-Object -ComObject WScript.Shell; "
        f"$S = $W.CreateShortcut('{str(lnk_path)}'); "
        f"$S.TargetPath = '{str(target_exe)}'; "
        f"$S.WorkingDirectory = '{str(target_exe.parent)}'; "
        + (f"$S.IconLocation = '{str(icon_path)},0'; " if icon_path else "")
        + "$S.Save()"
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # Completely optional; ignore failures.
        pass


def _self_replace_if_needed() -> bool:
    """
    If launched as a staged updater, perform replacement then start the final EXE.
    Returns True if we handled replacement and already spawned the final app.
    """
    if "--self-replace" not in sys.argv:
        return False

    # Args: --self-replace <target-name> [--cleanup <old-staged-path>]
    args = sys.argv[:]
    try:
        i = args.index("--self-replace")
        target_name = args[i + 1]
    except Exception:
        target_name = Path(sys.executable).with_suffix(".exe").name

    staged = Path(sys.executable).resolve()
    app_dir = staged.parent
    target = app_dir / target_name

    # If we were also passed a path to clean up after we hand off, keep it.
    cleanup_path = None
    if "--cleanup" in args:
        j = args.index("--cleanup")
        if j + 1 < len(args):
            cleanup_path = Path(args[j + 1])

    # Try a few times in case the old EXE hasn't closed yet.
    for _ in range(60):  # up to ~6 seconds
        try:
            # Replace target atomically via temp -> replace
            tmp = target.with_suffix(target.suffix + ".tmp")
            shutil.copy2(staged, tmp)
            os.replace(tmp, target)
            break
        except Exception:
            time.sleep(0.1)
    else:
        # Give up; run staged so the app at least launches.
        subprocess.Popen([str(staged)], close_fds=True)
        os._exit(0)

    # Launch the final EXE, asking it to delete this staged file.
    cmd = [str(target)]
    if staged.exists():
        cmd += ["--cleanup", str(staged)]
    subprocess.Popen(cmd, close_fds=True)

    # Exit this staged process.
    os._exit(0)


def _cleanup_if_requested():
    """If launched with --cleanup <path>, try to delete that file quietly."""
    if "--cleanup" in sys.argv:
        try:
            i = sys.argv.index("--cleanup")
            if i + 1 < len(sys.argv):
                p = Path(sys.argv[i + 1])
                for _ in range(50):
                    try:
                        if p.exists():
                            p.unlink()
                        break
                    except Exception:
                        time.sleep(0.1)
        except Exception:
            pass


# Early hooks before the GUI starts.
if _self_replace_if_needed():
    # Already handled; process will exit inside the helper.
    pass
_cleanup_if_requested()


# =============================== Main GUI ===================================
class CsvCombinerGUI(BaseTk):
    def __init__(self):
        super().__init__()

        # ---- Window basics
        self.title(f"CSV Combiner — GUI Shell v{APP_VERSION}")
        self.geometry("900x520")

        # ---- Blue theme (frames/labels)
        self.configure(bg=COLOR_BG)
        style = ttk.Style(self)
        style.configure("Blue.TFrame", background=COLOR_BG)
        style.configure("Blue.TLabel", background=COLOR_BG, foreground="white")

        # ---- App state (model)
        self.input_files: list[Path] = []
        self.output_path_var = tk.StringVar(self, "")

        # ---- Root layout
        self.columnconfigure(0, weight=1, uniform="cols")
        self.columnconfigure(1, weight=1, uniform="cols")
        self.rowconfigure(0, weight=1)

        # ---- Build UI
        self._build_left_panel()
        self._build_right_panel()
        self._build_statusbar()
        self._install_menu()

    # ========================= Left panel (inputs)
    def _build_left_panel(self):
        left = ttk.Frame(self, padding=12, style="Blue.TFrame")
        left.grid(row=0, column=0, sticky="nsew")

        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Input CSV files", style="Blue.TLabel").grid(row=0, column=0, sticky="w")

        list_wrap = ttk.Frame(left, style="Blue.TFrame")
        list_wrap.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        list_wrap.rowconfigure(0, weight=1)
        list_wrap.columnconfigure(0, weight=1)

        self.input_list = tk.Listbox(
            list_wrap, selectmode=tk.EXTENDED, exportselection=False
        )
        self.input_list.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.input_list.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.input_list.configure(yscrollcommand=yscroll.set)

        if DND_AVAILABLE:
            self.input_list.drop_target_register(DND_FILES)
            self.input_list.dnd_bind("<<Drop>>", self._on_drop_files)

        self.input_list.bind("<Double-1>", self._on_input_double_click)

        btns = ttk.Frame(left, style="Blue.TFrame")
        btns.grid(row=2, column=0, sticky="ew")
        for c in range(3):
            btns.columnconfigure(c, weight=1)

        ttk.Button(btns, text="Add CSVs…", command=self.add_csvs).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Remove selected", command=self.remove_selected).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(btns, text="Clear", command=self.clear_inputs).grid(row=0, column=2, sticky="ew", padx=(6, 0))

    # ========================= Right panel (output + logo)
    def _build_right_panel(self):
        right = ttk.Frame(self, padding=12, style="Blue.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="Output CSV file", style="Blue.TLabel").grid(row=0, column=0, sticky="w")

        row = ttk.Frame(right, style="Blue.TFrame")
        row.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        row.columnconfigure(0, weight=1)

        self.output_entry = ttk.Entry(row, textvariable=self.output_path_var)
        self.output_entry.grid(row=0, column=0, sticky="ew")

        ttk.Button(row, text="Choose…", command=self.choose_output).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(right, text="Tip: pick a name like combined.csv", style="Blue.TLabel").grid(row=2, column=0, sticky="w")

        # Centered, larger dino logo under the tip
        try:
            logo_path = _resource_path("dinologo.png")
            src = tk.PhotoImage(file=logo_path)

            target_w = 450
            z = max(1, round(target_w / src.width()))
            img = src.zoom(z, z)
            if img.width() > target_w:
                div = max(1, round(img.width() / target_w))
                if div > 1:
                    img = img.subsample(div, div)

            self._logo_image = img
            right.rowconfigure(3, weight=1)
            logo_area = ttk.Frame(right, style="Blue.TFrame")
            logo_area.grid(row=3, column=0, sticky="nsew", pady=(8, 8))
            logo_area.columnconfigure(0, weight=1)
            logo_area.rowconfigure(0, weight=1)
            ttk.Label(logo_area, image=self._logo_image, style="Blue.TLabel").grid(row=0, column=0)
        except Exception as e:
            print("Logo load failed:", e)

    # ========================= Status bar
    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(12, 6))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(self, "Ready")
        ttk.Label(bar, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")

    # ========================= Button callbacks
    def add_csvs(self):
        paths = filedialog.askopenfilenames(
            title="Select CSV files",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not paths:
            self.status_var.set("Add cancelled.")
            return

        existing = set(self.input_files)
        added = 0
        for s in paths:
            p = Path(s)
            if p.suffix.lower() != ".csv":
                continue
            if p not in existing:
                self.input_files.append(p)
                existing.add(p)
                added += 1

        self._refresh_input_list()
        self.status_var.set(f"Added {added} file(s). Total: {len(self.input_files)}")

        if not self.output_path_var.get() and self.input_files:
            suggested = self.input_files[0].parent / "combined.csv"
            self.output_path_var.set(str(suggested))

    def remove_selected(self):
        sel = list(self.input_list.curselection())
        if not sel:
            self.status_var.set("Nothing selected.")
            return
        for idx in reversed(sel):
            del self.input_files[idx]
        self._refresh_input_list()
        self.status_var.set(f"Removed {len(sel)} file(s). Total: {len(self.input_files)}")

    def clear_inputs(self):
        if not self.input_files:
            self.status_var.set("List already empty.")
            return
        self.input_files.clear()
        self._refresh_input_list()
        self.status_var.set("Cleared all input files.")

    def choose_output(self):
        initial_dir = str(self.input_files[0].parent) if self.input_files else str(Path.home())
        path = filedialog.asksaveasfilename(
            title="Save combined CSV as…",
            defaultextension=".csv",
            initialfile="combined.csv",
            initialdir=initial_dir,
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            self.status_var.set("Output selection cancelled.")
            return
        self.output_path_var.set(path)
        self.status_var.set(f"Output set: {path}")

    # ========================= Listbox / DnD
    def _on_input_double_click(self, event):
        index = self.input_list.nearest(event.y)
        if index < 0 or index >= len(self.input_files):
            return
        self._open_csv_preview(self.input_files[index])

    def _on_drop_files(self, event):
        if not event.data:
            return
        raw_paths = self.tk.splitlist(event.data)
        existing = set(self.input_files)
        added = 0
        for s in raw_paths:
            p = Path(s)
            if p.suffix.lower() != ".csv":
                continue
            if p not in existing:
                self.input_files.append(p)
                existing.add(p)
                added += 1
        self._refresh_input_list()
        self.status_var.set(f"Added {added} file(s) via drag & drop. Total: {len(self.input_files)}")
        if not self.output_path_var.get() and self.input_files:
            suggested = self.input_files[0].parent / "combined.csv"
            self.output_path_var.set(str(suggested))

    # ========================= CSV preview
    def _open_csv_preview(self, path: Path, max_rows: int = 200):
        if not path.exists():
            messagebox.showerror("Preview error", f"File not found:\n{path}")
            return

        encodings_to_try = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
        chosen_enc = encodings_to_try[0]
        dialect = csv.excel
        header, rows = [], []
        last_err = None

        for enc in encodings_to_try:
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    sample = f.read(4096)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample)
                    except Exception:
                        dialect = csv.excel
                    try:
                        has_header = csv.Sniffer().has_header(sample)
                    except Exception:
                        has_header = True

                    reader = csv.reader(f, dialect)
                    if has_header:
                        header = next(reader, [])
                    else:
                        first = next(reader, [])
                        header = [f"col{i+1}" for i in range(len(first))]
                        rows.append(first)

                    for _ in range(max_rows - len(rows)):
                        r = next(reader, None)
                        if r is None:
                            break
                        rows.append(r)
                chosen_enc = enc
                last_err = None
                break
            except Exception as e:
                last_err = e
                rows.clear()
                header = []

        if last_err is not None:
            messagebox.showerror("Preview error", f"Could not read file:\n{path}\n\n{last_err}")
            return

        num_cols = max(len(header), max((len(r) for r in rows), default=0))
        if not header:
            header = [f"col{i+1}" for i in range(num_cols)]
        header = list(header) + [""] * (num_cols - len(header))
        for r in rows:
            r += [""] * (num_cols - len(r))

        win = tk.Toplevel(self)
        win.title(f"Preview — {path.name}")
        win.geometry("900x500")
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        info = ttk.Label(
            win,
            text=f"{path}   •   encoding={chosen_enc}   •   delimiter='{getattr(dialect,'delimiter',',')}'   "
                 f"•   showing {len(rows)} row(s) (max {max_rows})",
            foreground="#555", anchor="w", padding=(12, 8)
        )
        info.grid(row=0, column=0, sticky="ew")

        frame = ttk.Frame(win, padding=(12, 0, 12, 12))
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        col_ids = [f"c{i}" for i in range(num_cols)]
        tree = ttk.Treeview(frame, columns=col_ids, show="headings")
        tree.grid(row=0, column=0, sticky="nsew")

        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        xbar.grid(row=1, column=0, sticky="ew")

        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)

        for i, cid in enumerate(col_ids):
            title = header[i] if i < len(header) else f"col{i+1}"
            tree.heading(cid, text=title)
            tree.column(cid, width=140, minwidth=60, stretch=True, anchor="w")

        for r in rows:
            tree.insert("", tk.END, values=r)

    # ========================= Helpers
    def _refresh_input_list(self):
        self.input_list.delete(0, tk.END)
        for p in self.input_files:
            self.input_list.insert(tk.END, f"{p.name}   —   {p.parent}")

    def _install_menu(self):
        menubar = tk.Menu(self)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Check for updates…", command=self.check_for_updates_unified)
        helpmenu.add_separator()
        helpmenu.add_command(label="Check for updates (client)…", command=self.check_for_updates_packaged)
        helpmenu.add_command(label="Check for updates (dev/git)…", command=self.check_for_updates)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.config(menu=menubar)

    # ========================= Dev/git updater
    def check_for_updates(self):
        def worker():
            try:
                app_dir = Path(__file__).resolve().parent
                if not (app_dir / ".git").exists():
                    self.after(0, lambda: tk.messagebox.showinfo(
                        "Updates",
                        "This folder is not a git repository.\nUse packaged updates instead."
                    ))
                    return

                branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=app_dir)
                self._git(["fetch", "origin", branch], cwd=app_dir)

                local = self._git(["rev-parse", "--short", "HEAD"], cwd=app_dir)
                remote = self._git(["rev-parse", "--short", f"origin/{branch}"], cwd=app_dir)

                if local == remote:
                    self.after(0, lambda: tk.messagebox.showinfo(
                        "You're up to date", f"Local {branch}: {local}\nRemote {branch}: {remote}"
                    ))
                    return

                ahead = self._git(["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"], cwd=app_dir)
                try:
                    ahead_n, behind_n = map(int, ahead.split())
                except Exception:
                    ahead_n = behind_n = None

                def prompt():
                    msg = [f"Local {branch}:  {local}", f"Remote {branch}: {remote}", ""]
                    if behind_n is not None:
                        msg.append(f"Your branch is {behind_n} commit(s) behind, {ahead_n} ahead.")
                    msg.append("\nFast-forward pull now?")
                    if tk.messagebox.askyesno("Update available", "\n".join(msg)):
                        self.update_now(branch)
                self.after(0, prompt)

            except Exception as e:
                self.after(0, lambda: tk.messagebox.showerror("Update check failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def update_now(self, branch: str):
        if hasattr(self, "status_var"):
            self.status_var.set("Updating from origin…")

        def worker():
            try:
                app_dir = Path(__file__).resolve().parent
                self._git(["checkout", branch], cwd=app_dir)
                try:
                    self._git(["branch", "-u", f"origin/{branch}", branch], cwd=app_dir)
                except Exception:
                    pass
                self._git(["pull", "--ff-only", "origin", branch], cwd=app_dir)

                def ok():
                    if hasattr(self, "status_var"):
                        self.status_var.set("Updated. Please restart the app.")
                    tk.messagebox.showinfo("Updated", "Pulled latest changes.\nPlease restart the app.")
                self.after(0, ok)

            except Exception as e:
                def fail():
                    if hasattr(self, "status_var"):
                        self.status_var.set("Update failed.")
                    tk.messagebox.showerror(
                        "Update failed",
                        f"{e}\n\nIf you have local edits, commit or stash them, then try again."
                    )
                self.after(0, fail)

        threading.Thread(target=worker, daemon=True).start()

    def _git(self, args, cwd: Path) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            shell=False
        )
        if proc.returncode != 0:
            msg = proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed"
            raise RuntimeError(msg)
        return proc.stdout.strip()

    def check_for_updates_unified(self, silent: bool = False):
        app_dir = Path(__file__).resolve().parent
        in_git = (app_dir / ".git").exists()
        is_frozen = getattr(sys, "frozen", False)
        if in_git and not is_frozen:
            return self.check_for_updates()
        else:
            return self.check_for_updates_packaged(silent=silent)

    # ========================= Client/manifest updater
    def check_for_updates_packaged(self, silent: bool = False):
        if hasattr(self, "status_var"):
            self.status_var.set("Checking for updates…")

        def worker():
            data = None
            try:
                req = urllib.request.Request(
                    UPDATE_MANIFEST_URL,
                    headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                if not silent:
                    self.after(0, lambda: messagebox.showerror("Update check failed", str(e)))
                return
            self.after(0, lambda: self._handle_update_manifest(data, silent))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_update_manifest(self, data: dict, silent: bool):
        latest = data.get("latest", "").strip()
        notes = data.get("changelog", "")

        if self._version_tuple(latest) <= self._version_tuple(APP_VERSION):
            if not silent:
                messagebox.showinfo("You're up to date", f"{APP_NAME} {APP_VERSION} is the latest.")
            if hasattr(self, "status_var"):
                self.status_var.set("Ready")
            return

        plat = platform.system().lower()
        section = data["windows"] if plat == "windows" else data.get("mac", {})
        url = section.get("url")
        page = section.get("page")
        sha = section.get("sha256", "")
        if not url:
            messagebox.showwarning("Update available", f"{latest} is available, but no download URL for your platform.")
            return

        dlg = tk.Toplevel(self); dlg.title(f"Update available — {latest}")
        dlg.geometry("520x300"); dlg.transient(self); dlg.grab_set()
        dlg.columnconfigure(0, weight=1); dlg.rowconfigure(1, weight=1)

        frm = ttk.Frame(dlg, padding=12)
        frm.grid(sticky="nsew")
        frm.columnconfigure(0, weight=1); frm.rowconfigure(1, weight=1)
        ttk.Label(frm, text=f"A new version is available: {latest}", font=("", 11, "bold")).grid(sticky="w")
        txt = tk.Text(frm, height=10, wrap="word"); txt.grid(sticky="nsew", pady=(8, 8))
        txt.insert("1.0", notes or "(no changelog provided)"); txt.configure(state="disabled")
        btns = ttk.Frame(frm); btns.grid(sticky="e", pady=(8, 0))

        def open_page():
            webbrowser.open(page or url); dlg.destroy()

        def auto_download():
            dlg.destroy(); self._download_update(url, sha, latest)

        ttk.Button(btns, text="Open download page", command=open_page).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="Auto-download", command=auto_download).grid(row=0, column=1)
        ttk.Button(btns, text="Later", command=dlg.destroy).grid(row=0, column=2, padx=(8, 0))

    def _download_update(self, url: str, expected_sha256: str, latest_version: str = ""):
        if hasattr(self, "status_var"):
            self.status_var.set("Downloading update…")

        def worker():
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    chunk = 1024 * 128

                    filename = url.split("/")[-1] or "update.bin"
                    cd = resp.headers.get("Content-Disposition", "")
                    if "filename=" in cd:
                        fn = cd.split("filename=", 1)[1].strip().strip('";')
                        if fn:
                            filename = fn

                    tmpdir = Path(tempfile.gettempdir()) / f"{APP_NAME.replace(' ', '')}_updates"
                    tmpdir.mkdir(parents=True, exist_ok=True)
                    out_path = tmpdir / filename

                    h = hashlib.sha256()
                    with open(out_path, "wb") as f:
                        while True:
                            buf = resp.read(chunk)
                            if not buf:
                                break
                            f.write(buf)
                            h.update(buf)

                digest = h.hexdigest()
                if expected_sha256 and digest.lower() != expected_sha256.lower():
                    raise RuntimeError(f"SHA256 mismatch. Expected {expected_sha256}, got {digest}")

                def done():
                    if hasattr(self, "status_var"):
                        self.status_var.set(f"Update downloaded: {out_path}")
                    # Offer to apply now
                    if platform.system() == "Windows":
                        if messagebox.askyesno(
                            "Update downloaded",
                            "The update has been downloaded.\n\nApply it now and restart?"
                        ):
                            self._apply_update_now(out_path)
                            return
                    # Fallback message
                    messagebox.showinfo(
                        "Update downloaded",
                        f"Saved:\n{out_path}\n\nClose the app and run the new EXE to update."
                    )
                self.after(0, done)

            except Exception as e:
                if hasattr(self, "status_var"):
                    self.status_var.set("Download failed.")
                err_lines = [
                    "Auto-download failed.",
                    f"URL: {url!r}",
                    f"Type: {type(e).__name__}",
                    f"Detail: {getattr(e, 'reason', None) or getattr(e, 'msg', None) or str(e) or 'unknown'}",
                ]
                self.after(0, lambda: messagebox.showerror("Download failed", "\n".join(err_lines)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_now(self, downloaded_path: Path):
        """Stage the update next to the running EXE and restart via a .new helper."""
        if platform.system() != "Windows":
            messagebox.showinfo("Unsupported", "Auto-apply is only implemented on Windows.")
            return

        # Where is the currently running EXE (works in frozen and script runs)?
        app_exe = Path(sys.executable).resolve()
        app_dir = app_exe.parent

        # Copy the downloaded file into the app folder as "<current-name>.new.exe"
        staged = app_exe.with_name(app_exe.stem + ".new" + app_exe.suffix)
        try:
            shutil.copy2(downloaded_path, staged)
        except Exception as e:
            messagebox.showerror("Apply failed", f"Couldn't stage update:\n{e}")
            return

        # Ensure/refresh a desktop shortcut (points to *actual* current exe name).
        icon = Path(_resource_path("dinologo.ico")) if Path(_resource_path("dinologo.ico")).exists() else None
        _create_or_update_shortcut(app_exe, icon)

        # Launch staged helper with --self-replace to swap itself into the current EXE name.
        cmd = [str(staged), "--self-replace", app_exe.name, "--cleanup", str(staged)]
        try:
            subprocess.Popen(cmd, close_fds=True)
        except Exception as e:
            messagebox.showerror("Apply failed", f"Couldn't launch updater:\n{e}")
            return

        # Quit this process immediately (the helper will take over).
        try:
            self.destroy()
        finally:
            os._exit(0)

    @staticmethod
    def _version_tuple(s: str):
        parts = []
        for p in s.split("."):
            num = "".join(ch for ch in p if ch.isdigit())
            parts.append(int(num) if num else 0)
        return tuple(parts + [0] * (3 - len(parts)))


if __name__ == "__main__":
    app = CsvCombinerGUI()
    app.mainloop()

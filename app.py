# app.py
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import subprocess, threading


# --- Drag & Drop support (optional) -----------------------------------------
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
    BaseTk = TkinterDnD.Tk
except Exception:
    DND_AVAILABLE = False
    DND_FILES = None
    BaseTk = tk.Tk


class CsvCombinerGUI(BaseTk):
    def __init__(self):
        super().__init__()

        # ---- Window basics
        self.title("CSV Combiner — GUI Shell")
        self.geometry("900x520")

        # ---- App state (model)
        self.input_files = []                           # list[Path]
        self.output_path_var = tk.StringVar(self, "")   # bound to Entry on the right

        # ---- Root layout: 2 equal columns that stretch
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
        left = ttk.Frame(self, padding=12)
        left.grid(row=0, column=0, sticky="nsew")

        # In this frame, row 1 (the list area) will stretch
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Input CSV files").grid(row=0, column=0, sticky="w")

        # Listbox + vertical scrollbar
        list_wrap = ttk.Frame(left)
        list_wrap.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        list_wrap.rowconfigure(0, weight=1)
        list_wrap.columnconfigure(0, weight=1)

        self.input_list = tk.Listbox(
            list_wrap,
            selectmode=tk.EXTENDED,     # ctrl/shift multi-select
            exportselection=False       # keep selection when focus changes
        )
        self.input_list.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.input_list.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.input_list.configure(yscrollcommand=yscroll.set)

        # Enable OS drag & drop if available
        if DND_AVAILABLE:
            self.input_list.drop_target_register(DND_FILES)
            self.input_list.dnd_bind("<<Drop>>", self._on_drop_files)

        # Double-click to preview
        self.input_list.bind("<Double-1>", self._on_input_double_click)

        # Buttons row
        btns = ttk.Frame(left)
        btns.grid(row=2, column=0, sticky="ew")
        for c in range(3):
            btns.columnconfigure(c, weight=1)

        ttk.Button(btns, text="Add CSVs…", command=self.add_csvs).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Remove selected", command=self.remove_selected).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(btns, text="Clear", command=self.clear_inputs).grid(row=0, column=2, sticky="ew", padx=(6, 0))

    # ========================= Right panel (output)
    def _build_right_panel(self):
        right = ttk.Frame(self, padding=12)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)  # Entry should stretch horizontally

        ttk.Label(right, text="Output CSV file").grid(row=0, column=0, sticky="w")

        row = ttk.Frame(right)
        row.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        row.columnconfigure(0, weight=1)    # make the Entry expand

        # Entry <-> StringVar two-way binding
        self.output_entry = ttk.Entry(row, textvariable=self.output_path_var)
        self.output_entry.grid(row=0, column=0, sticky="ew")

        ttk.Button(row, text="Choose…", command=self.choose_output).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(right, text="Tip: pick a name like combined.csv", foreground="#666").grid(row=2, column=0, sticky="w")

    # ========================= Status bar
    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(12, 6))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(self, "Ready")
        ttk.Label(bar, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")

    # ========================= Button callbacks
    def add_csvs(self):
        """Ask for CSV files and append unique ones to the model."""
        paths = filedialog.askopenfilenames(
            title="Select CSV files",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not paths:
            self.status_var.set("Add cancelled.")
            return

        existing = set(self.input_files)  # Paths are hashable
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
        for idx in reversed(sel):   # delete from back so indices don't shift
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
        """Pick an output .csv via Save As; suggest first input's folder when available."""
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
        self.output_path_var.set(path)          # Entry updates automatically
        self.status_var.set(f"Output set: {path}")

    # ========================= Listbox events
    def _on_input_double_click(self, event):
        """Open preview for the row under the mouse."""
        # Which visual row was double-clicked?
        index = self.input_list.nearest(event.y)
        if index < 0 or index >= len(self.input_files):
            return
        path = self.input_files[index]
        self._open_csv_preview(path)

    # ========================= Drag & Drop handler
    def _on_drop_files(self, event):
        """Handle files dropped from the OS onto the Listbox."""
        if not event.data:
            return
        raw_paths = self.tk.splitlist(event.data)  # handles braces/spaces
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
        """
        Open a Toplevel window showing a preview of the CSV.
        - Tries a few encodings.
        - Auto-detects delimiter with csv.Sniffer (fallback to comma).
        - Shows up to max_rows rows in a Treeview with H/V scrollbars.
        """
        if not path.exists():
            messagebox.showerror("Preview error", f"File not found:\n{path}")
            return

        # Try encodings in order
        encodings_to_try = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
        chosen_enc = encodings_to_try[0]
        dialect = csv.excel
        header = []
        rows = []

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

        # Normalize widths
        num_cols = max(len(header), max((len(r) for r in rows), default=0))
        if not header:
            header = [f"col{i+1}" for i in range(num_cols)]
        header = list(header) + [""] * (num_cols - len(header))
        for r in rows:
            r += [""] * (num_cols - len(r))

        # Build the preview window
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

        # Treeview with scrollbars
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

        # Headings & column sizing
        for i, cid in enumerate(col_ids):
            title = header[i] if i < len(header) else f"col{i+1}"
            tree.heading(cid, text=title)
            tree.column(cid, width=140, minwidth=60, stretch=True, anchor="w")

        # Insert rows
        for r in rows:
            tree.insert("", tk.END, values=r)

    # ========================= View helper
    def _refresh_input_list(self):
        """Render self.input_files (list[Path]) into the Listbox."""
        self.input_list.delete(0, tk.END)
        for p in self.input_files:
            self.input_list.insert(tk.END, f"{p.name}   —   {p.parent}")

        # ======== Menu
    def _install_menu(self):
        menubar = tk.Menu(self)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Check for updates…", command=self.check_for_updates)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.config(menu=menubar)

    # ======== Update: compare local HEAD to remote and prompt
    def check_for_updates(self):
        """Check whether the current branch is behind origin/<branch> (non-blocking)."""
        # Run in background so UI stays responsive
        def worker():
            try:
                app_dir = Path(__file__).resolve().parent
                if not (app_dir / ".git").exists():
                    self.after(0, lambda: tk.messagebox.showinfo(
                        "Updates",
                        "This folder is not a git repository.\nUse packaged updates instead."
                    ))
                    return

                # Which branch are we on?
                branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=app_dir)

                # Fetch remote info for this branch
                self._git(["fetch", "origin", branch], cwd=app_dir)

                local = self._git(["rev-parse", "--short", "HEAD"], cwd=app_dir)
                remote = self._git(["rev-parse", "--short", f"origin/{branch}"], cwd=app_dir)

                if local == remote:
                    self.after(0, lambda: tk.messagebox.showinfo(
                        "You're up to date",
                        f"Local {branch}: {local}\nRemote {branch}: {remote}"
                    ))
                    return

                # How many commits behind?
                ahead = self._git(["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"], cwd=app_dir)
                # Format: "<ahead> <behind>" when comparing HEAD (left) vs origin/branch (right)
                try:
                    ahead_n, behind_n = map(int, ahead.split())
                except Exception:
                    ahead_n = behind_n = None

                def prompt():
                    msg = [f"Local {branch}:  {local}",
                        f"Remote {branch}: {remote}",
                        ""]
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
        """Do a fast-forward pull from origin/<branch> (non-blocking)."""
        if hasattr(self, "status_var"):
            self.status_var.set("Updating from origin…")

        def worker():
            try:
                app_dir = Path(__file__).resolve().parent
                # Make sure we’re on the right branch and have an upstream
                self._git(["checkout", branch], cwd=app_dir)
                # Ensure our local branch tracks origin/branch (safe if already set)
                try:
                    self._git(["branch", "-u", f"origin/{branch}", branch], cwd=app_dir)
                except Exception:
                    pass
                # Fast-forward only (no merge commits)
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

    # ======== Helper to run git and capture output (raises on error)
    def _git(self, args, cwd: Path) -> str:
        """Run a git command and return stdout (stripped). Raises on failure."""
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            shell=False
        )
        if proc.returncode != 0:
            # Prefer stderr; fall back to stdout
            msg = proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed"
            raise RuntimeError(msg)
        return proc.stdout.strip()



        


if __name__ == "__main__":
    app = CsvCombinerGUI()
    app.mainloop()

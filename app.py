"""
Qaff Digital Professional — Unified Control Center
Premium dark-theme desktop app merging:
1. Qaff Auto Set Channels (Channel Setup)
2. Auto Delete Live and Shorts
3. Auto See Hours (Monetization Hours Tracker)
"""

from __future__ import annotations

import sys
import os
import re
import queue
import csv
import json
import threading
import builtins
import subprocess
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# ── Runtime paths ─────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# Add BASE_DIR to system path so we can import local scripts
sys.path.insert(0, str(BASE_DIR))

# ── Import automation scripts ──────────────────────────────────────────────────
import auto_set_script
import auto_delete_script
import see_hours_script
import end_screen_script
import publish_posts_script

# ── Asset finder ──────────────────────────────────────────────────────────────
def find_asset(name: str) -> Path | None:
    """Look in _MEIPASS (frozen) then BASE_DIR/assets for an asset file."""
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        p = meipass / name
        if p.exists():
            return p
        p = meipass / "assets" / name
        if p.exists():
            return p
    
    # Dev mode
    p = BASE_DIR / "assets" / name
    if p.exists():
        return p
    p = BASE_DIR / name
    return p if p.exists() else None

# ── CustomTkinter configuration ───────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Palette
C = {
    "bg_dark":    "#09090b",   # Deep black
    "sidebar":    "#161619",   # Dark charcoal
    "card":       "#18181b",   # Surface dark
    "border":     "#27272a",   # Zinc 800
    
    "primary":         "#0891b2",   # Cyan 600
    "primary_hover":   "#06b6d4",   # Cyan 500
    "purple":          "#7c3aed",   # Violet 600
    "purple_hover":    "#8b5cf6",   # Violet 500
    "success":         "#059669",   # Emerald 600
    "success_hover":   "#10b981",   # Emerald 500
    "warning":         "#d97706",   # Amber 600
    "warning_hover":   "#f59e0b",   # Amber 500
    "error":           "#dc2626",   # Red 600
    "error_hover":     "#ef4444",   # Red 500

    "fg":              "#ffffff",
    "fg2":             "#a1a1aa",
    "fg3":             "#52525b",
    "disabled_fg":     "#27272a",
    "disabled_text":   "#52525b",
}

# ── Thread IO Interceptor ──────────────────────────────────────────────────────
class ThreadIOContext:
    """Redirects stdout, stderr and custom input for background threads."""
    def __init__(self, log_func, input_needed_func, release_event, input_value_holder, default_tag=None):
        self.log_func = log_func
        self.input_needed_func = input_needed_func
        self.release_event = release_event
        self.input_value_holder = input_value_holder
        self.default_tag = default_tag
        
        self.old_stdin = None
        self.old_stdout = None
        self.old_stderr = None
        self.old_input = None

    def __enter__(self):
        self.old_stdin = sys.stdin
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr
        self.old_input = builtins.input

        sys.stdout = self
        sys.stderr = self
        sys.stdin = self
        builtins.input = self.custom_input
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdin = self.old_stdin
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr
        builtins.input = self.old_input

    def write(self, text):
        if text.strip():
            self.log_func(text.strip(), self.default_tag)

    def flush(self):
        pass

    def readline(self):
        self.input_needed_func()
        self.release_event.clear()
        self.release_event.wait()
        return self.input_value_holder[0] + "\n"

    def custom_input(self, prompt=""):
        if prompt:
            self.log_func(prompt, "warning")
        return self.readline()


# ── License Activation Dialog ──────────────────────────────────────────────────
class LicenseActivationDialog(ctk.CTkToplevel):
    def __init__(self, parent, hwid, verify_callback):
        super().__init__(parent)
        self.parent = parent
        self.hwid = hwid
        self.verify_callback = verify_callback
        self.success = False
        
        self.title("Activation Required")
        self.geometry("500x320")
        self.resizable(False, False)
        self.configure(fg_color=C["bg_dark"])
        
        # Center the window
        self.update_idletasks()
        width = 500
        height = 320
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        
        # Make modal
        self.transient(parent)
        self.grab_set()
        
        # Prevent normal window closing
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Brand Title area
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", padx=30, pady=(30, 20))
        
        logo_png = find_asset("logo.png")
        if logo_png:
            try:
                from PIL import Image
                img = ctk.CTkImage(light_image=Image.open(logo_png), dark_image=Image.open(logo_png), size=(36, 36))
                ctk.CTkLabel(title_frame, image=img, text="").pack(side="left", padx=(0, 10))
                self._logo_img = img
            except Exception:
                pass
                
        ctk.CTkLabel(
            title_frame,
            text="Qaff Digital Professional",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=C["fg"],
        ).pack(side="left")
        
        # HWID label and copy button
        lbl_hwid_desc = ctk.CTkLabel(
            self,
            text="Provide this Hardware ID (HWID) to the developer to get your key:",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C["fg2"],
            anchor="w"
        )
        lbl_hwid_desc.pack(fill="x", padx=30, pady=(0, 4))
        
        hwid_row = ctk.CTkFrame(self, fg_color="transparent")
        hwid_row.pack(fill="x", padx=30, pady=(0, 15))
        
        self.entry_hwid = ctk.CTkEntry(
            hwid_row,
            font=ctk.CTkFont("Consolas", 12, "bold"),
            fg_color=C["card"],
            border_color=C["border"],
            text_color=C["primary"],
            height=30
        )
        self.entry_hwid.insert(0, self.hwid)
        self.entry_hwid.configure(state="readonly")
        self.entry_hwid.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        btn_copy = ctk.CTkButton(
            hwid_row,
            text="Copy",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color=C["primary"],
            hover_color=C["primary_hover"],
            width=70,
            height=30,
            command=self.copy_hwid
        )
        btn_copy.pack(side="left")
        
        # License key entry
        lbl_key_desc = ctk.CTkLabel(
            self,
            text="Enter Serial Key:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=C["fg"],
            anchor="w"
        )
        lbl_key_desc.pack(fill="x", padx=30, pady=(0, 4))
        
        self.entry_key = ctk.CTkEntry(
            self,
            placeholder_text="XXXX-XXXX-XXXX-XXXX",
            font=ctk.CTkFont("Consolas", 12, "bold"),
            fg_color=C["card"],
            border_color=C["border"],
            height=30
        )
        self.entry_key.pack(fill="x", padx=30, pady=(0, 20))
        
        # Action buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=30, pady=(0, 20))
        
        btn_close = ctk.CTkButton(
            btn_row,
            text="Exit",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color="#37373f",
            hover_color="#4f4f5a",
            width=100,
            height=32,
            command=self.on_close
        )
        btn_close.pack(side="left")
        
        btn_activate = ctk.CTkButton(
            btn_row,
            text="Activate Software",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["success"],
            hover_color=C["success_hover"],
            width=180,
            height=32,
            command=self.activate
        )
        btn_activate.pack(side="right")

    def copy_hwid(self):
        self.clipboard_clear()
        self.clipboard_append(self.hwid)
        from tkinter import messagebox
        messagebox.showinfo("Copied", "Hardware ID copied to clipboard successfully!")
        
    def activate(self):
        key = self.entry_key.get().strip()
        if not key:
            from tkinter import messagebox
            messagebox.showwarning("Empty Key", "Please enter a serial key.")
            return
            
        if self.verify_callback(key):
            self.success = True
            self.entered_key = key
            self.grab_release()
            self.destroy()
        else:
            from tkinter import messagebox
            messagebox.showerror("Activation Failed", "Invalid Serial Key for this machine.\nPlease make sure you copied it correctly.")

    def on_close(self):
        self.grab_release()
        self.destroy()
        import sys
        sys.exit(0)


# ── Main Application ──────────────────────────────────────────────────────────
class QaffDigitalProfessional(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Window settings
        self.title("Qaff Digital Professional")
        self.geometry("1150x760")
        self.minsize(950, 650)
        self.configure(fg_color=C["bg_dark"])
        # Application state
        self.active_tool: str | None = None # None | "setup" | "delete" | "hours" | "endscreen"
        self.global_chrome_profile = ctk.StringVar(value=r"C:\QaffChromeProfile")
        self.update_btn = None

        # Profile Manager State
        self.base_profiles_dir = r"C:\QaffChromeProfile"
        self.profiles_list = ["Default"]
        self.active_profile_name = "Default"
        self._load_profiles_config()
        
        # License check gate
        self.license_key = getattr(self, "license_key", "")
        
        # Check blacklist first
        if self._is_blacklisted():
            self.license_key = ""
            self._save_profiles_config()
            from tkinter import messagebox
            messagebox.showerror(
                "Access Denied",
                "This device has been deactivated/blacklisted by the developer.\n"
                "Please contact the developer for assistance."
            )
            import sys
            sys.exit(0)
            
        hwid = self._get_hwid()
        formatted_hwid = "-".join(hwid[i:i+4] for i in range(0, len(hwid), 4))
        
        if not self._verify_license_key(self.license_key):
            self.withdraw()
            dialog = LicenseActivationDialog(self, formatted_hwid, self._verify_license_key)
            self.wait_window(dialog)
            
            if dialog.success:
                self.license_key = dialog.entered_key
                self._save_profiles_config()
                self.deiconify()
        
        # Stdin buffers for each tool
        self.setup_input_gate = threading.Event()
        self.setup_input_value = [""]
        self.delete_input_gate = threading.Event()
        self.delete_input_value = [""]
        self.hours_input_gate = threading.Event()
        self.hours_input_value = [""]
        self.endscreen_input_gate = threading.Event()
        self.endscreen_input_value = [""]
        self.publish_input_gate = threading.Event()
        self.publish_input_value = [""]

        # Thread log queues
        self.log_queue = queue.Queue()

        # Thread signals
        self.setup_stop_event = threading.Event()
        self.setup_pause_event = threading.Event()
        self.setup_pause_event.set()

        self.delete_stop_event = threading.Event()
        self.delete_pause_event = threading.Event()
        self.delete_pause_event.set()

        self.hours_stop_event = threading.Event()
        self.hours_pause_event = threading.Event()
        self.hours_pause_event.set()

        self.endscreen_stop_event = threading.Event()
        self.endscreen_pause_event = threading.Event()
        self.endscreen_pause_event.set()

        self.publish_stop_event = threading.Event()
        self.publish_pause_event = threading.Event()
        self.publish_pause_event.set()

        # Set Window Icons
        white_ico = find_asset("logo_white.ico")
        if white_ico:
            try:
                self.iconbitmap(str(white_ico))
            except Exception:
                pass
        else:
            logo_png = find_asset("logo.png")
            if logo_png:
                try:
                    from PIL import Image, ImageTk
                    img = Image.open(logo_png).resize((32, 32), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self.iconphoto(True, photo)
                    self._icon_photo = photo
                except Exception:
                    pass

        # Build UI layout
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()
        self._build_home_panel()
        self._build_setup_panel()
        self._build_delete_panel()
        self._build_hours_panel()
        self._build_endscreen_panel()
        self._build_publish_panel()

        # Show Home Panel initially
        self.select_panel("home")
        self.after(200, self._poll_log_queue)
        self._setup_global_keyboard_shortcuts()
        
        # Optimize window dragging to prevent movement lag
        self._last_configure_width = None
        self._last_configure_height = None
        self.bind("<Configure>", self._optimized_configure)
        
        self._auto_check_and_install_playwright()

        # Update checker initialization
        self.current_version = "1.1.0"
        self.update_btn = None
        self.temp_update_path = None
        self.check_for_updates()

    def _optimized_configure(self, event):
        if event.widget == self:
            if (self._last_configure_width != event.width) or (self._last_configure_height != event.height):
                self._last_configure_width = event.width
                self._last_configure_height = event.height
                self._update_dimensions_event(event)

    def check_for_updates(self):
        def _check_thread():
            import urllib.request
            import json
            import tempfile
            import os
            
            repo = "mahmoudmousa8/Qaff-Digital-Professional"
            api_url = f"https://api.github.com/repos/{repo}/releases/latest"
            headers = {"User-Agent": "Mozilla/5.0"}
            
            try:
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode())
                    remote_version = data.get("tag_name", "1.0.0").strip("v")
                    
                    # Parse version strings into lists of integers for safe comparison
                    local_parts = list(map(int, self.current_version.split(".")))
                    remote_parts = list(map(int, remote_version.split(".")))
                    
                    # Pad lists to equal length if necessary
                    max_len = max(len(local_parts), len(remote_parts))
                    local_parts += [0] * (max_len - len(local_parts))
                    remote_parts += [0] * (max_len - len(remote_parts))
                    
                    if remote_parts > local_parts:
                        # Find the EXE asset
                        assets = data.get("assets", [])
                        download_url = None
                        for asset in assets:
                            if asset.get("name", "").endswith(".exe"):
                                download_url = asset.get("browser_download_url")
                                break
                        
                        if not download_url and assets:
                            download_url = assets[0].get("browser_download_url")
                            
                        if download_url:
                            # Start downloading in the background
                            temp_dir = tempfile.gettempdir()
                            self.temp_update_path = os.path.join(temp_dir, "Qaff_Digital_Professional_update.exe")
                            
                            req_dl = urllib.request.Request(download_url, headers=headers)
                            with urllib.request.urlopen(req_dl, timeout=60) as dl_resp:
                                with open(self.temp_update_path, "wb") as f:
                                    f.write(dl_resp.read())
                                    
                            # Download completed successfully, show update button!
                            self.after(0, lambda: self.show_update_button(remote_version))
            except Exception:
                pass

        import threading
        threading.Thread(target=_check_thread, daemon=True).start()

    def show_update_button(self, remote_version):
        self.update_btn = ctk.CTkButton(
            self,
            text=f"🔄 Update v{remote_version}",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color=C["warning"],
            hover_color=C["warning_hover"],
            text_color="#ffffff",
            width=130,
            height=28,
            corner_radius=6,
            command=self.apply_update
        )
        self.update_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=10)
        self.update_btn.lift()

    def apply_update(self):
        from tkinter import messagebox
        import os
        import sys
        import subprocess

        ans = messagebox.askyesno(
            "Apply Update",
            "A new update has been downloaded in the background.\n\n"
            "Would you like to restart the application now to apply the update?"
        )
        if not ans:
            return

        current_exe = sys.executable
        if not current_exe.endswith(".exe"):
            messagebox.showinfo(
                "Update Info",
                f"Running from Python source code. The updated executable has been downloaded to:\n{self.temp_update_path}"
            )
            return

        batch_path = os.path.join(os.path.dirname(current_exe), "apply_update.bat")
        batch_content = f"""@echo off
chcp 65001 >nul
title Updating Qaff Digital Professional...
echo Waiting for application to close...
:loop
tasklist /FI "PID eq {os.getpid()}" 2>NUL | find /I "{os.getpid()}" >NUL
if "%ERRORLEVEL%"=="0" (
    timeout /t 1 /nobreak >nul
    goto loop
)
echo Applying update...
del /f /q "{current_exe}"
move /y "{self.temp_update_path}" "{current_exe}"
echo Starting updated application...
start "" "{current_exe}"
del "%~f0"
"""
        try:
            with open(batch_path, "w", encoding="utf-8") as f:
                f.write(batch_content)
            
            subprocess.Popen(
                ["cmd.exe", "/c", batch_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
            )
            self.destroy()
            sys.exit(0)
        except Exception as e:
            messagebox.showerror("Update Error", f"Could not apply update:\n{e}")

    def _get_hwid(self) -> str:
        import subprocess
        import uuid
        import hashlib
        
        # Try motherboard UUID
        try:
            out = subprocess.check_output("wmic csproduct get uuid", shell=True).decode().split()
            if len(out) >= 2:
                val = out[1].strip()
                if val and "FFFF" not in val.upper() and len(val) > 10:
                    return hashlib.sha256(val.encode()).hexdigest()[:16].upper()
        except Exception:
            pass
            
        # Try C: drive serial number
        try:
            out = subprocess.check_output("wmic diskdrive get serialnumber", shell=True).decode().split()
            if len(out) >= 2:
                val = "".join(out[1:]).strip()
                if val:
                    return hashlib.sha256(val.encode()).hexdigest()[:16].upper()
        except Exception:
            pass
            
        # Fallback to MAC address
        node_id = str(uuid.getnode())
        return hashlib.sha256(node_id.encode()).hexdigest()[:16].upper()

    def _verify_license_key(self, entered_key: str) -> bool:
        import hashlib
        SECRET_SALT = "QaffDigitalProfessionalLicenseKeySalt2026#!"
        hwid = self._get_hwid()
        
        full_hash = hashlib.sha256((hwid + SECRET_SALT).encode()).hexdigest().upper()
        expected_key = "-".join(full_hash[i:i+4] for i in range(0, 16, 4))
        
        clean_entered = entered_key.replace("-", "").replace(" ", "").upper()
        clean_expected = expected_key.replace("-", "").replace(" ", "").upper()
        
        return clean_entered == clean_expected

    def _is_blacklisted(self) -> bool:
        import urllib.request
        
        hwid = self._get_hwid()
        formatted_hwid = "-".join(hwid[i:i+4] for i in range(0, len(hwid), 4))
        
        blacklist_url = "https://raw.githubusercontent.com/mahmoudmousa8/Qaff-Digital-Professional/main/blacklist.txt"
        headers = {"User-Agent": "Mozilla/5.0"}
        
        try:
            req = urllib.request.Request(blacklist_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                content = response.read().decode("utf-8")
                
                # Check both raw HWID and formatted HWID in the blacklist file (case-insensitive)
                lines = [line.strip().upper() for line in content.split("\n")]
                if hwid.upper() in lines or formatted_hwid.upper() in lines:
                    return True
        except Exception:
            # If offline or connection fails, do not block
            pass
            
        return False

    def _setup_global_keyboard_shortcuts(self):
        # Bind Control KeyPress globally to handle English & Arabic layouts on Windows
        self.bind_all("<Control-KeyPress>", self._on_global_control_press)

    def _on_global_control_press(self, event):
        widget = event.widget
        if not widget:
            return

        try:
            widget_class = widget.winfo_class()
        except Exception:
            return

        if widget_class not in ("Entry", "Text"):
            return

        # Keycodes on Windows:
        # A (sh) = 65
        # C (wa') = 67
        # V (ra') = 86
        # X (hamza) = 88
        if event.keycode == 67:  # Ctrl+C / Ctrl+Arabic_C
            widget.event_generate("<<Copy>>")
            return "break"
        elif event.keycode == 86:  # Ctrl+V / Ctrl+Arabic_V
            widget.event_generate("<<Paste>>")
            return "break"
        elif event.keycode == 88:  # Ctrl+X / Ctrl+Arabic_X
            widget.event_generate("<<Cut>>")
            return "break"
        elif event.keycode == 65:  # Ctrl+A / Ctrl+Arabic_A
            if widget_class == "Text":
                widget.tag_add("sel", "1.0", "end")
            elif widget_class == "Entry":
                widget.select_range(0, "end")
                widget.icursor("end")
            return "break"



    # ─── Sidebar ──────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, fg_color=C["sidebar"], corner_radius=0, width=240)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(8, weight=1)

        # App Brand Title
        brand_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand_frame.grid(row=0, column=0, padx=20, pady=(24, 20), sticky="ew")

        # Load Logo
        logo_png = find_asset("logo.png")
        if logo_png:
            try:
                from PIL import Image
                img = ctk.CTkImage(
                    light_image=Image.open(logo_png),
                    dark_image=Image.open(logo_png),
                    size=(36, 36),
                )
                ctk.CTkLabel(brand_frame, image=img, text="").pack(side="left", padx=(0, 10))
                self._sidebar_logo = img
            except Exception:
                pass

        ctk.CTkLabel(
            brand_frame,
            text="Qaff Professional",
            font=ctk.CTkFont("Segoe UI", 16, "bold"),
            text_color=C["fg"],
        ).pack(side="left")

        # Separator line
        ctk.CTkFrame(sidebar, fg_color=C["border"], height=1).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 15))

        # Navigation Buttons
        def make_nav_btn(row, text, command):
            btn = ctk.CTkButton(
                sidebar,
                text=text,
                anchor="w",
                height=42,
                corner_radius=8,
                fg_color="transparent",
                hover_color="#27272a",
                text_color=C["fg2"],
                font=ctk.CTkFont("Segoe UI", 13, "bold"),
                command=command,
            )
            btn.grid(row=row, column=0, sticky="ew", padx=12, pady=3)
            return btn

        self.nav_home_btn = make_nav_btn(2, "Home Dashboard", lambda: self.select_panel("home"))
        self.nav_setup_btn = make_nav_btn(3, "Channel Setup", lambda: self.select_panel("set"))
        self.nav_delete_btn = make_nav_btn(4, "Auto Delete", lambda: self.select_panel("delete"))
        self.nav_hours_btn = make_nav_btn(5, "Track Monetization", lambda: self.select_panel("hours"))
        self.nav_endscreen_btn = make_nav_btn(6, "Set End Screen & Premiere", lambda: self.select_panel("endscreen"))
        self.nav_publish_btn = make_nav_btn(7, "Publish Posts", lambda: self.select_panel("publish"))

        # Footer Status Indicator
        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=9, column=0, sticky="ew", padx=16, pady=20)
        
        self.lbl_global_status = ctk.CTkLabel(
            footer,
            text="Status: Idle",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=C["success"],
            anchor="w",
        )
        self.lbl_global_status.pack(fill="x", pady=2)
        
        ctk.CTkLabel(
            footer,
            text="Version: v1.0 • Qaff Digital",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=C["fg3"],
            anchor="w",
        ).pack(fill="x")

    def select_panel(self, name):
        # Update button visual styling
        for btn, active_name in [
            (self.nav_home_btn, "home"),
            (self.nav_setup_btn, "set"),
            (self.nav_delete_btn, "delete"),
            (self.nav_hours_btn, "hours"),
            (self.nav_endscreen_btn, "endscreen"),
            (self.nav_publish_btn, "publish"),
        ]:
            if name == active_name:
                btn.configure(fg_color=C["primary"], text_color="#ffffff", hover_color=C["primary_hover"])
            else:
                btn.configure(fg_color="transparent", text_color=C["fg2"], hover_color="#27272a")

        # Hide all panels
        self.home_panel.grid_forget()
        self.setup_panel.grid_forget()
        self.delete_panel.grid_forget()
        self.hours_panel.grid_forget()
        self.endscreen_panel.grid_forget()
        self.publish_panel.grid_forget()

        # Show active panel
        if name == "home":
            self.home_panel.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)
        elif name == "set":
            self.setup_panel.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)
        elif name == "delete":
            self.delete_panel.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)
        elif name == "hours":
            self.hours_panel.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)
        elif name == "endscreen":
            self.endscreen_panel.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)
        elif name == "publish":
            self.publish_panel.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)

        if hasattr(self, "update_btn") and self.update_btn:
            self.update_btn.lift()

    # ─── Home Dashboard Panel ─────────────────────────────────────────────────
    def _build_home_panel(self):
        self.home_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.home_panel.grid_rowconfigure(1, weight=1)
        self.home_panel.grid_columnconfigure(0, weight=1)

        # Header card
        hdr = ctk.CTkFrame(self.home_panel, fg_color=C["card"], border_width=1, border_color=C["border"], corner_radius=12)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        
        lbl_welcome = ctk.CTkLabel(
            hdr,
            text="Qaff Digital Professional Dashboard",
            font=ctk.CTkFont("Segoe UI", 24, "bold"),
            text_color=C["fg"],
            anchor="w"
        )
        lbl_welcome.pack(fill="x", padx=24, pady=(20, 4))

        lbl_desc = ctk.CTkLabel(
            hdr,
            text="Welcome to the unified YouTube management suite. Manage multiple channels and profiles concurrently.",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C["fg2"],
            anchor="w"
        )
        lbl_desc.pack(fill="x", padx=24, pady=(0, 20))

        # Main Info area
        content = ctk.CTkScrollableFrame(self.home_panel, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew")

        # 1. Profile Manager Card
        profile_card = ctk.CTkFrame(content, fg_color=C["card"], border_width=1, border_color=C["border"], corner_radius=12)
        profile_card.pack(fill="x", pady=(0, 16), padx=2)

        ctk.CTkLabel(
            profile_card,
            text="Chrome Profile Manager",
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=C["fg"],
            anchor="w"
        ).pack(fill="x", padx=20, pady=(16, 6))

        ctrl_row = ctk.CTkFrame(profile_card, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=20, pady=(0, 6))

        self.profile_dropdown_var = ctk.StringVar(value=self.active_profile_name)
        self.profile_dropdown = ctk.CTkOptionMenu(
            ctrl_row,
            values=self.profiles_list,
            variable=self.profile_dropdown_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["border"],
            button_color=C["border"],
            button_hover_color="#3f3f46",
            dropdown_fg_color=C["card"],
            dropdown_hover_color="#27272a",
            dropdown_text_color=C["fg"],
            width=180,
            command=self._on_profile_selected,
        )
        self.profile_dropdown.pack(side="right")

        ctk.CTkLabel(
            ctrl_row,
            text="Active Profile:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=C["fg2"],
            anchor="e"
        ).pack(side="right", padx=(0, 10))

        self.btn_add_profile = ctk.CTkButton(
            ctrl_row,
            text="Add Profile +",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["success"],
            hover_color=C["success_hover"],
            text_color="#ffffff",
            width=110,
            command=self._add_new_profile,
        )
        self.btn_add_profile.pack(side="left", padx=(0, 10))

        self.btn_rename_profile = ctk.CTkButton(
            ctrl_row,
            text="Rename Profile",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["warning"],
            hover_color=C["warning_hover"],
            text_color="#ffffff",
            width=110,
            command=self._rename_current_profile,
        )
        self.btn_rename_profile.pack(side="left", padx=(0, 10))

        self.btn_delete_profile = ctk.CTkButton(
            ctrl_row,
            text="Delete Profile ×",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["error"],
            hover_color=C["error_hover"],
            text_color="#ffffff",
            width=110,
            command=self._delete_current_profile,
        )
        self.btn_delete_profile.pack(side="left")

        path_row = ctk.CTkFrame(profile_card, fg_color="transparent")
        path_row.pack(fill="x", padx=20, pady=(0, 16))

        self.lbl_profile_path_display = ctk.CTkLabel(
            path_row,
            text=f"Profile Path: {self.global_chrome_profile.get()}",
            font=ctk.CTkFont("Consolas", 11),
            text_color=C["fg3"],
            anchor="w"
        )
        self.lbl_profile_path_display.pack(side="right", fill="x", expand=True)

        self.btn_open_browser = ctk.CTkButton(
            path_row,
            text="Open Browser (Preview)",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            fg_color=C["primary"],
            hover_color=C["primary_hover"],
            text_color="#ffffff",
            width=150,
            height=24,
            command=self._open_profile_browser,
        )
        self.btn_open_browser.pack(side="left", padx=(0, 10))

        self.btn_close_browser = ctk.CTkButton(
            path_row,
            text="Close Browser",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            fg_color=C["error"],
            hover_color=C["error_hover"],
            text_color="#ffffff",
            width=120,
            height=24,
            command=self._close_profile_browser,
        )
        self.btn_close_browser.pack(side="left", padx=(0, 10))

        self.btn_hide_browser = ctk.CTkButton(
            path_row,
            text="Hide Browser",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            fg_color=C["warning"],
            hover_color=C["warning_hover"],
            text_color="#ffffff",
            width=120,
            height=24,
            command=self._toggle_browser_visibility,
        )
        self.btn_hide_browser.pack(side="left", padx=(0, 10))



    def _get_config_path(self):
        old_config = BASE_DIR / "config.json"
        appdata_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "QaffDigitalProfessional"
        appdata_dir.mkdir(parents=True, exist_ok=True)
        new_config = appdata_dir / "config.json"
        if old_config.exists() and not new_config.exists():
            try:
                import shutil
                shutil.copy(old_config, new_config)
                old_config.unlink()
            except Exception:
                pass
        return new_config

    def _load_profiles_config(self):
        config_path = self._get_config_path()
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.profiles_list = data.get("profiles", ["Default"])
                    self.active_profile_name = data.get("active_profile", "Default")
                    self.base_profiles_dir = data.get("base_profiles_dir", r"C:\QaffChromeProfile")
                    self.license_key = data.get("license_key", "")
                    if self.active_profile_name not in self.profiles_list:
                        self.profiles_list.append(self.active_profile_name)
            except Exception:
                pass
        self._sync_profile_paths()

    def _save_profiles_config(self):
        config_path = self._get_config_path()
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "profiles": self.profiles_list,
                    "active_profile": self.active_profile_name,
                    "base_profiles_dir": self.base_profiles_dir,
                    "license_key": getattr(self, "license_key", "")
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _sync_profile_paths(self):
        full_path = os.path.join(self.base_profiles_dir, self.active_profile_name)
        self.global_chrome_profile.set(full_path)
        
        # Sync paths to script modules
        auto_delete_script.CHROME_PROFILE_PATH = full_path
        see_hours_script.CHROME_PROFILE_PATH = full_path
        auto_set_script.CHROME_PROFILE_PATH = full_path

    def _on_profile_selected(self, choice):
        self.active_profile_name = choice
        self._sync_profile_paths()
        self._save_profiles_config()
        if hasattr(self, "lbl_profile_path_display"):
            self.lbl_profile_path_display.configure(text=f"Profile Path: {self.global_chrome_profile.get()}")
        if hasattr(self, "btn_hide_browser"):
            self.btn_hide_browser.configure(text="Hide Browser")

    def _get_active_profile_port(self) -> int:
        try:
            idx = self.profiles_list.index(self.active_profile_name)
        except ValueError:
            idx = 0
        return 9222 + idx

    def _open_profile_browser_for_login(self):
        chrome_path = auto_delete_script.find_chrome_executable()
        if not chrome_path:
            messagebox.showerror("Error", "Google Chrome was not found on your system.")
            return

        profile_path = self.global_chrome_profile.get()
        Path(profile_path).mkdir(parents=True, exist_ok=True)

        cmd = [
            chrome_path,
            f"--user-data-dir={profile_path}",
            "--profile-directory=Default",
            "--new-window",
            "https://www.youtube.com/"
        ]

        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._queue_log("setup", f"Launched Chrome in secure Login Mode for profile '{self.active_profile_name}'", "success")
            messagebox.showinfo(
                "Login Browser Opened",
                f"Chrome has been opened in secure Login Mode for profile '{self.active_profile_name}'.\n\n"
                f"1. Login to your Google/YouTube account in the opened window.\n"
                f"2. Close the Chrome window completely once you are logged in.\n"
                f"3. After closing, you can run the automation tools from this application."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch Chrome: {e}")

    def _open_profile_browser(self):
        chrome_path = auto_delete_script.find_chrome_executable()
        if not chrome_path:
            messagebox.showerror("Error", "Google Chrome was not found on your system.")
            return
        
        profile_path = self.global_chrome_profile.get()
        port = self._get_active_profile_port()
        
        Path(profile_path).mkdir(parents=True, exist_ok=True)
        
        cmd = [
            chrome_path,
            f"--user-data-dir={profile_path}",
            f"--remote-debugging-port={port}",
            "--new-window",
            "https://www.youtube.com/"
        ]
        
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._queue_log("setup", f"Launched Chrome on port {port} for profile '{self.active_profile_name}'", "success")
            self._queue_log("delete", f"Launched Chrome on port {port} for profile '{self.active_profile_name}'", "success")
            self._queue_log("hours", f"Launched Chrome on port {port} for profile '{self.active_profile_name}'", "success")
            self._queue_log("endscreen", f"Launched Chrome on port {port} for profile '{self.active_profile_name}'", "success")
            messagebox.showinfo("Browser Opened", f"Chrome has been opened for profile '{self.active_profile_name}' on port {port}.\nYou can now run any automation without closing this browser.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch Chrome: {e}")

    def _close_profile_browser(self):
        port = self._get_active_profile_port()
        profile_path = self.global_chrome_profile.get().replace("\\", "\\\\").replace("'", "''")
        
        # PowerShell command to find and stop processes associated with this port or profile path
        ps_cmd = (
            f"Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe' AND "
            f"(CommandLine LIKE '%--remote-debugging-port={port}%' OR "
            f"CommandLine LIKE '%--user-data-dir={profile_path}%')\" | "
            f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
        )
        
        try:
            subprocess.Popen(["powershell", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._queue_log("setup", f"Closed Chrome browser for profile '{self.active_profile_name}'", "warning")
            self._queue_log("delete", f"Closed Chrome browser for profile '{self.active_profile_name}'", "warning")
            self._queue_log("hours", f"Closed Chrome browser for profile '{self.active_profile_name}'", "warning")
            self._queue_log("endscreen", f"Closed Chrome browser for profile '{self.active_profile_name}'", "warning")
        except Exception as e:
            pass

        if hasattr(self, "btn_hide_browser"):
            self.btn_hide_browser.configure(text="Hide Browser")

    def _toggle_browser_visibility(self):
        port = self._get_active_profile_port()
        profile_path = self.global_chrome_profile.get().replace("\\", "\\\\").replace("'", "''")
        
        # PowerShell command to find process IDs associated with this port or profile path
        ps_cmd = (
            f"Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe' AND "
            f"(CommandLine LIKE '%--remote-debugging-port={port}%' OR "
            f"CommandLine LIKE '%--user-data-dir={profile_path}%')\" | "
            f"Select-Object -ExpandProperty ProcessId"
        )
        
        try:
            output = subprocess.check_output(
                ["powershell", "-Command", ps_cmd],
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            pids = [int(x.strip()) for x in output.splitlines() if x.strip().isdigit()]
        except Exception:
            pids = []
            
        if not pids:
            messagebox.showinfo("Browser Info", "No active browser window found for this profile.\nMake sure the browser is open.")
            return

        import ctypes
        
        hwnds = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        
        def callback(hwnd, lParam):
            if ctypes.windll.user32.IsWindow(hwnd):
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in pids:
                    buf = ctypes.create_unicode_buffer(256)
                    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
                    class_name = buf.value
                    
                    title_buf = ctypes.create_unicode_buffer(1024)
                    ctypes.windll.user32.GetWindowTextW(hwnd, title_buf, 1024)
                    title = title_buf.value
                    
                    if class_name == "Chrome_WidgetWin_1" or title:
                        hwnds.append(hwnd)
            return True
            
        cb = EnumWindowsProc(callback)
        ctypes.windll.user32.EnumWindows(cb, 0)
        
        if not hwnds:
            messagebox.showinfo("Browser Info", "No active browser window found for this profile.")
            return
            
        # Determine visibility
        any_visible = False
        for hwnd in hwnds:
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                any_visible = True
                break
                
        if any_visible:
            for hwnd in hwnds:
                ctypes.windll.user32.ShowWindow(hwnd, 0) # SW_HIDE
            self.btn_hide_browser.configure(text="Show Browser")
            self._queue_log("setup", f"Browser hidden for profile '{self.active_profile_name}'", "info")
        else:
            for hwnd in hwnds:
                ctypes.windll.user32.ShowWindow(hwnd, 5) # SW_SHOW
                try:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            self.btn_hide_browser.configure(text="Hide Browser")
            self._queue_log("setup", f"Browser shown for profile '{self.active_profile_name}'", "info")

    def _rename_current_profile(self):
        old_name = self.active_profile_name
        dialog = ctk.CTkInputDialog(
            text=f"Enter new name for profile '{old_name}' (Arabic/English letters/Numbers):",
            title="Rename Profile"
        )
        new_name = dialog.get_input()
        if not new_name:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        if not re.match(r"^[\w _-]+$", new_name):
            messagebox.showerror("Error", "Profile name contains invalid characters. Please use alphanumeric characters and spaces/underscores/hyphens only.")
            return
        if new_name == old_name:
            return
        if new_name in self.profiles_list:
            messagebox.showerror("Error", "A profile with this name already exists.")
            return
        
        old_path = os.path.join(self.base_profiles_dir, old_name)
        new_path = os.path.join(self.base_profiles_dir, new_name)
        
        renamed_on_disk = False
        if os.path.exists(old_path):
            try:
                os.rename(old_path, new_path)
                renamed_on_disk = True
            except PermissionError:
                messagebox.showerror(
                    "Error",
                    f"Could not rename profile folder on disk.\n"
                    f"Please make sure Chrome is closed and not using the profile '{old_name}' before renaming."
                )
                return
            except Exception as e:
                messagebox.showerror("Error", f"An error occurred while renaming the folder: {e}")
                return
        
        idx = self.profiles_list.index(old_name)
        self.profiles_list[idx] = new_name
        self.active_profile_name = new_name
        
        self.profile_dropdown.configure(values=self.profiles_list)
        self.profile_dropdown_var.set(new_name)
        self._sync_profile_paths()
        self._save_profiles_config()
        
        msg = f"Profile renamed from '{old_name}' to '{new_name}'."
        if renamed_on_disk:
            msg += "\nProfile folder on disk was successfully renamed."
        messagebox.showinfo("Success", msg)

    def _auto_check_and_install_playwright(self):
        def run_check():
            import time
            time.sleep(0.5)
            
            need_install = False
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    try:
                        browser = playwright.chromium.launch(headless=True)
                        browser.close()
                    except Exception:
                        need_install = True
            except Exception:
                need_install = True
                
            if need_install:
                self.after(0, lambda: self._set_global_status("Installing Playwright browser...", C["warning"]))
                try:
                    import playwright.__main__ as pw_main
                    import sys
                    old_argv = sys.argv
                    sys.argv = ["playwright", "install", "chromium"]
                    try:
                        pw_main.main()
                    except SystemExit:
                        pass
                    except Exception:
                        pass
                    finally:
                        sys.argv = old_argv
                    self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))
                except Exception as e:
                    self.after(0, lambda: self._set_global_status("Browser Install Failed", C["error"]))
            else:
                self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))

        threading.Thread(target=run_check, daemon=True).start()

    def _pulse_global_status(self, step=0):
        if not self.active_tool:
            self.lbl_global_status.configure(text_color=C["success"])
            return

        # Gentle pulsing color ramp for active status (amber-orange-yellow)
        colors = ["#d97706", "#f59e0b", "#fbbf24", "#f59e0b"]
        next_step = (step + 1) % len(colors)
        
        try:
            self.lbl_global_status.configure(text_color=colors[step])
        except Exception:
            pass
            
        self.after(250, lambda: self._pulse_global_status(next_step))

    def _add_new_profile(self):
        dialog = ctk.CTkInputDialog(text="Enter new profile name (Arabic/English letters/Numbers):", title="New Profile")
        name = dialog.get_input()
        if name:
            name = name.strip()
            if not re.match(r"^[\w _-]+$", name):
                messagebox.showerror("Error", "Profile name contains invalid characters. Please use alphanumeric characters and spaces/underscores/hyphens only.")
                return
            if name in self.profiles_list:
                messagebox.showerror("Error", "Profile already exists.")
                return
            self.profiles_list.append(name)
            self.active_profile_name = name
            self.profile_dropdown.configure(values=self.profiles_list)
            self.profile_dropdown_var.set(name)
            self._sync_profile_paths()
            self._save_profiles_config()
            if hasattr(self, "lbl_profile_path_display"):
                self.lbl_profile_path_display.configure(text=f"Profile Path: {self.global_chrome_profile.get()}")
            messagebox.showinfo("Success", f"Profile '{name}' created successfully.")

    def _delete_current_profile(self):
        if len(self.profiles_list) <= 1:
            messagebox.showwarning("Warning", "You cannot delete the only remaining profile.")
            return
        
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete profile '{self.active_profile_name}'?\nIt will only be removed from the list, its actual files will not be deleted to protect your data."
        )
        if confirm:
            old_name = self.active_profile_name
            self.profiles_list.remove(old_name)
            self.active_profile_name = self.profiles_list[0]
            self.profile_dropdown.configure(values=self.profiles_list)
            self.profile_dropdown_var.set(self.active_profile_name)
            self._sync_profile_paths()
            self._save_profiles_config()
            if hasattr(self, "lbl_profile_path_display"):
                self.lbl_profile_path_display.configure(text=f"Profile Path: {self.global_chrome_profile.get()}")
            messagebox.showinfo("Success", f"Profile '{old_name}' removed from the list.")



    def _install_playwright_browsers(self):
        if self.active_tool:
            messagebox.showwarning("Warning", "Please wait until the current tool finishes running.")
            return

        self._set_global_status("Installing Playwright browser...", C["warning"])
        
        def run_install():
            try:
                import playwright.__main__ as pw_main
                old_argv = sys.argv
                sys.argv = ["playwright", "install", "chromium"]
                try:
                    pw_main.main()
                except Exception:
                    pass
                finally:
                    sys.argv = old_argv
                self.after(0, lambda: messagebox.showinfo("Success", "Playwright browser installed successfully!"))
                self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))
            except SystemExit:
                self.after(0, lambda: messagebox.showinfo("Success", "Playwright browser installed successfully!"))
                self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", f"Failed to install browser: {e}"))
                self.after(0, lambda: self._set_global_status("Error installing browser", C["error"]))

        threading.Thread(target=run_install, daemon=True).start()

    # ─── Panel 1: Channel Setup ────────────────────────────────────────────────
    def _build_setup_panel(self):
        self.setup_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.setup_panel.grid_rowconfigure(5, weight=1)
        self.setup_panel.grid_columnconfigure(0, weight=1)

        # Title card
        title_card = ctk.CTkFrame(self.setup_panel, fg_color="transparent", height=40)
        title_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(
            title_card, text="Channel Setup",
            font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=C["fg"]
        ).pack(side="left")

        # Input Card
        card = ctk.CTkFrame(self.setup_panel, fg_color=C["card"], border_width=1, border_color=C["border"], corner_radius=12)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(12, 4))
        
        ctk.CTkLabel(
            top_row, text="Enter channel handles (@handles) or names (one per line):",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left")

        ctk.CTkButton(
            top_row, text="Clear List", width=80, height=28,
            fg_color=C["border"], hover_color="#3f3f46",
            text_color=C["fg2"], font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._clear_setup_handles,
        ).pack(side="right")

        self.txt_setup_handles = ctk.CTkTextbox(
            card, height=100, font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"], text_color=C["fg"],
            border_color=C["border"], border_width=1, corner_radius=8,
        )
        self.txt_setup_handles.pack(fill="x", padx=16, pady=(0, 12))
        
        # Mode selector row
        mode_row = ctk.CTkFrame(card, fg_color="transparent")
        mode_row.pack(fill="x", padx=16, pady=(0, 14))

        ctk.CTkLabel(
            mode_row, text="Operation Mode:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left", padx=(0, 10))

        self.setup_run_mode_var = ctk.StringVar(value="Add Data")
        self.setup_mode_selector = ctk.CTkSegmentedButton(
            mode_row,
            values=["Add Data", "Add Logo"],
            variable=self.setup_run_mode_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            selected_color=C["primary"],
            selected_hover_color=C["primary_hover"],
            unselected_color=C["bg_dark"],
            unselected_hover_color=C["border"],
            text_color=C["fg"],
            corner_radius=8,
            command=self._on_setup_mode_change,
        )
        self.setup_mode_selector.pack(side="left")

        # Controls row
        btn_bar = ctk.CTkFrame(self.setup_panel, fg_color="transparent")
        btn_bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.btn_setup_start = self._make_control_btn(btn_bar, "Start Setup ▶", C["success"], C["success_hover"], self._start_setup)
        self.btn_setup_pause = self._make_control_btn(btn_bar, "Pause ⏸", C["warning"], C["warning_hover"], self._pause_setup, state="disabled")
        self.btn_setup_resume = self._make_control_btn(btn_bar, "Resume ▶", C["success"], C["success_hover"], self._resume_setup, state="disabled")
        self.btn_setup_stop = self._make_control_btn(btn_bar, "Stop ⏹", C["error"], C["error_hover"], self._stop_setup, state="disabled")

        # Stdin Continue button
        self.btn_setup_continue = ctk.CTkButton(
            btn_bar, text="Continue ✔", width=110, height=36,
            corner_radius=8, fg_color=C["primary"], hover_color=C["primary_hover"],
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#ffffff",
            command=lambda: self._release_stdin("setup")
        )
        self.lbl_setup_continue = ctk.CTkLabel(
            btn_bar, text="Waiting for confirmation...", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["warning"]
        )

        # Log Panel
        log_hdr = ctk.CTkFrame(self.setup_panel, fg_color="transparent")
        log_hdr.grid(row=3, column=0, sticky="ew", pady=(4, 2))
        ctk.CTkLabel(log_hdr, text="Channel Setup Logs", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg3"]).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear Log", width=80, height=22,
            fg_color="transparent", text_color=C["fg3"], hover_color=C["border"],
            font=ctk.CTkFont("Segoe UI", 10, "bold"), command=self._clear_setup_log
        ).pack(side="right")

        self.txt_setup_log = ctk.CTkTextbox(
            self.setup_panel, font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"], text_color="#d4d4d8",
            border_color=C["border"], border_width=1, corner_radius=8,
            wrap="word", state="disabled"
        )
        self.txt_setup_log.grid(row=4, column=0, sticky="nsew", pady=(0, 10))

        # Status Bar
        self.setup_bar = ctk.CTkFrame(self.setup_panel, fg_color=C["card"], height=32, corner_radius=6, border_width=1, border_color=C["border"])
        self.setup_bar.grid(row=6, column=0, sticky="ew")
        self.setup_bar.pack_propagate(False)

        self.lbl_setup_status = ctk.CTkLabel(
            self.setup_bar, text="Ready", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["fg2"]
        )
        self.lbl_setup_status.pack(side="left", padx=15)

        self.setup_progress = ctk.CTkProgressBar(
            self.setup_bar, width=180, height=6, fg_color=C["border"], progress_color=C["primary"]
        )
        self.setup_progress.pack(side="right", padx=15, pady=13)
        self.setup_progress.set(0)

    def _clear_setup_handles(self):
        self.txt_setup_handles.delete("1.0", "end")

    def _on_setup_mode_change(self, value):
        if "Logo" in value:
            self.setup_mode_selector.configure(selected_color=C["purple"], selected_hover_color=C["purple_hover"])
            self.btn_setup_start.configure(text="Start Add Logo ▶", fg_color=C["purple"], hover_color=C["purple_hover"])
        else:
            self.setup_mode_selector.configure(selected_color=C["primary"], selected_hover_color=C["primary_hover"])
            self.btn_setup_start.configure(text="Start Setup ▶", fg_color=C["success"], hover_color=C["success_hover"])

    def _clear_setup_log(self):
        self._clear_text_box(self.txt_setup_log)

    # ─── Panel 2: Auto Delete ─────────────────────────────────────────────────
    def _build_delete_panel(self):
        self.delete_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.delete_panel.grid_rowconfigure(5, weight=1)
        self.delete_panel.grid_columnconfigure(0, weight=1)

        # Title card
        title_card = ctk.CTkFrame(self.delete_panel, fg_color="transparent", height=40)
        title_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(
            title_card, text="Auto Delete",
            font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=C["fg"]
        ).pack(side="left")

        # Input Card
        card = ctk.CTkFrame(self.delete_panel, fg_color=C["card"], border_width=1, border_color=C["border"], corner_radius=12)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        # Checkboxes for deletion options
        options_row = ctk.CTkFrame(card, fg_color="transparent")
        options_row.pack(fill="x", padx=16, pady=10)

        ctk.CTkLabel(
            options_row, text="Deletion Options:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left", padx=(0, 15))

        self.delete_live_var = ctk.BooleanVar(value=True)
        self.chk_delete_live = ctk.CTkCheckBox(
            options_row, text="Delete Live Streams (Live)", variable=self.delete_live_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg"],
            fg_color=C["primary"], border_color=C["border"]
        )
        self.chk_delete_live.pack(side="left", padx=(0, 24))

        self.delete_shorts_var = ctk.BooleanVar(value=True)
        self.chk_delete_shorts = ctk.CTkCheckBox(
            options_row, text="Delete Shorts", variable=self.delete_shorts_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg"],
            fg_color=C["primary"], border_color=C["border"]
        )
        self.chk_delete_shorts.pack(side="left", padx=(0, 24))

        self.delete_uploads_var = ctk.BooleanVar(value=False)
        self.chk_delete_uploads = ctk.CTkCheckBox(
            options_row, text="Delete Main Videos (Uploads)", variable=self.delete_uploads_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg"],
            fg_color=C["primary"], border_color=C["border"]
        )
        self.chk_delete_uploads.pack(side="left")

        # Filter section
        filter_row = ctk.CTkFrame(card, fg_color="transparent")
        filter_row.pack(fill="x", padx=16, pady=(0, 10))

        self.delete_filter_subs_var = ctk.BooleanVar(value=True)
        self.chk_delete_filter_subs = ctk.CTkCheckBox(
            filter_row, text="Filter by channels with subscribers >", variable=self.delete_filter_subs_var,
            command=self._toggle_delete_filter_subs,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg"],
            fg_color=C["primary"], border_color=C["border"]
        )
        self.chk_delete_filter_subs.pack(side="left", padx=(0, 10))

        self.delete_subs_threshold_var = ctk.StringVar(value="1000")
        self.entry_delete_subs_threshold = ctk.CTkEntry(
            filter_row, textvariable=self.delete_subs_threshold_var, width=80, height=28,
            font=ctk.CTkFont("Consolas", 12), fg_color=C["bg_dark"], border_color=C["border"], text_color=C["fg"]
        )
        self.entry_delete_subs_threshold.pack(side="left", padx=(0, 30))

        self.delete_manual_input_var = ctk.BooleanVar(value=False)
        self.chk_delete_manual = ctk.CTkCheckBox(
            filter_row, text="Enter channel handles manually", variable=self.delete_manual_input_var,
            command=self._toggle_delete_manual_input,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg"],
            fg_color=C["primary"], border_color=C["border"]
        )
        self.chk_delete_manual.pack(side="left")

        # Hidden text area for manual handles
        self.delete_manual_frame = ctk.CTkFrame(card, fg_color="transparent")
        
        ctk.CTkLabel(
            self.delete_manual_frame, text="Channel handles (one handle per line):",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["fg3"], anchor="w"
        ).pack(fill="x", padx=16, pady=(4, 2))

        self.txt_delete_manual = ctk.CTkTextbox(
            self.delete_manual_frame, height=60, font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"], text_color=C["fg"],
            border_color=C["border"], border_width=1, corner_radius=8
        )
        self.txt_delete_manual.pack(fill="x", padx=16, pady=(0, 10))

        # Placeholder setup
        self.delete_manual_placeholder = "Enter one handle per line, e.g.:\n@Lights-of-FaithQuran\n@PulseVersesQuran"
        self.txt_delete_manual.insert("1.0", self.delete_manual_placeholder)
        self.txt_delete_manual.configure(text_color="#6b7280")
        self.txt_delete_manual.is_placeholder_active = True

        def on_focus_in_delete(event):
            if getattr(self.txt_delete_manual, "is_placeholder_active", False):
                self.txt_delete_manual.delete("1.0", "end")
                self.txt_delete_manual.configure(text_color=C["fg"])
                self.txt_delete_manual.is_placeholder_active = False

        def on_focus_out_delete(event):
            content = self.txt_delete_manual.get("1.0", "end-1c").strip()
            if not content:
                self.txt_delete_manual.delete("1.0", "end")
                self.txt_delete_manual.insert("1.0", self.delete_manual_placeholder)
                self.txt_delete_manual.configure(text_color="#6b7280")
                self.txt_delete_manual.is_placeholder_active = True

        self.txt_delete_manual.bind("<FocusIn>", on_focus_in_delete)
        self.txt_delete_manual.bind("<FocusOut>", on_focus_out_delete)

        # Controls row
        btn_bar = ctk.CTkFrame(self.delete_panel, fg_color="transparent")
        btn_bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.btn_delete_start = self._make_control_btn(btn_bar, "Start Delete ▶", C["error"], C["error_hover"], self._start_delete)
        self.btn_delete_pause = self._make_control_btn(btn_bar, "Pause ⏸", C["warning"], C["warning_hover"], self._pause_delete, state="disabled")
        self.btn_delete_resume = self._make_control_btn(btn_bar, "Resume ▶", C["success"], C["success_hover"], self._resume_delete, state="disabled")
        self.btn_delete_skip = self._make_control_btn(btn_bar, "Skip Channel ⏭", C["warning"], C["warning_hover"], self._skip_delete_channel, state="disabled")
        self.btn_delete_stop = self._make_control_btn(btn_bar, "Stop ⏹", C["error"], C["error_hover"], self._stop_delete, state="disabled")

        # Stdin Continue button
        self.btn_delete_continue = ctk.CTkButton(
            btn_bar, text="Continue ✔", width=110, height=36,
            corner_radius=8, fg_color=C["primary"], hover_color=C["primary_hover"],
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#ffffff",
            command=lambda: self._release_stdin("delete")
        )
        self.lbl_delete_continue = ctk.CTkLabel(
            btn_bar, text="Waiting for confirmation...", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["warning"]
        )

        # Log Panel
        log_hdr = ctk.CTkFrame(self.delete_panel, fg_color="transparent")
        log_hdr.grid(row=3, column=0, sticky="ew", pady=(4, 2))
        ctk.CTkLabel(log_hdr, text="Auto Delete Logs", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg3"]).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear Log", width=80, height=22,
            fg_color="transparent", text_color=C["fg3"], hover_color=C["border"],
            font=ctk.CTkFont("Segoe UI", 10, "bold"), command=self._clear_delete_log
        ).pack(side="left")

        self.txt_delete_log = ctk.CTkTextbox(
            self.delete_panel, font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"], text_color="#d4d4d8",
            border_color=C["border"], border_width=1, corner_radius=8,
            wrap="word", state="disabled"
        )
        self.txt_delete_log.grid(row=4, column=0, sticky="nsew", pady=(0, 10))

        # Status Bar
        self.delete_bar = ctk.CTkFrame(self.delete_panel, fg_color=C["card"], height=32, corner_radius=6, border_width=1, border_color=C["border"])
        self.delete_bar.grid(row=6, column=0, sticky="ew")
        self.delete_bar.pack_propagate(False)

        self.lbl_delete_status = ctk.CTkLabel(
            self.delete_bar, text="Ready", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["fg2"]
        )
        self.lbl_delete_status.pack(side="left", padx=15)

        self.delete_progress = ctk.CTkProgressBar(
            self.delete_bar, width=180, height=6, fg_color=C["border"], progress_color=C["primary"]
        )
        self.delete_progress.pack(side="right", padx=15, pady=13)
        self.delete_progress.set(0)

    def _toggle_delete_filter_subs(self):
        if self.delete_filter_subs_var.get():
            self.entry_delete_subs_threshold.configure(state="normal")
        else:
            self.entry_delete_subs_threshold.configure(state="disabled")

    def _toggle_delete_manual_input(self):
        if self.delete_manual_input_var.get():
            self.delete_manual_frame.pack(fill="x", pady=6)
            self.chk_delete_filter_subs.configure(state="disabled")
            self.entry_delete_subs_threshold.configure(state="disabled")
        else:
            self.delete_manual_frame.pack_forget()
            self.chk_delete_filter_subs.configure(state="normal")
            self._toggle_delete_filter_subs()

    def _clear_delete_log(self):
        self._clear_text_box(self.txt_delete_log)

    # ─── Panel 3: See Hours ───────────────────────────────────────────────────
    def _build_hours_panel(self):
        self.hours_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.hours_panel.grid_rowconfigure(5, weight=1)
        self.hours_panel.grid_columnconfigure(0, weight=1)

        # Title card
        title_card = ctk.CTkFrame(self.hours_panel, fg_color="transparent", height=40)
        title_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(
            title_card, text="Track Monetization",
            font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=C["fg"]
        ).pack(side="left")

        # Instruction Card
        card = ctk.CTkFrame(self.hours_panel, fg_color=C["card"], border_width=1, border_color=C["border"], corner_radius=12)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        ctk.CTkLabel(
            card,
            text="This tool automatically opens the channel switcher, iterates through all channels of the logged-in account to extract the actual watch hours and subscriber counts from YouTube Creator Studio, and exports the reports to Excel and JSON files.",
            font=ctk.CTkFont("Segoe UI", 12), text_color=C["fg2"], justify="left", wraplength=700
        ).pack(fill="x", padx=20, pady=20)

        # Controls row
        btn_bar = ctk.CTkFrame(self.hours_panel, fg_color="transparent")
        btn_bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.btn_hours_start = self._make_control_btn(btn_bar, "Start Fetching Hours ▶", C["success"], C["success_hover"], self._start_hours)
        self.btn_hours_pause = self._make_control_btn(btn_bar, "Pause ⏸", C["warning"], C["warning_hover"], self._pause_hours, state="disabled")
        self.btn_hours_resume = self._make_control_btn(btn_bar, "Resume ▶", C["success"], C["success_hover"], self._resume_hours, state="disabled")
        self.btn_hours_stop = self._make_control_btn(btn_bar, "Stop ⏹", C["error"], C["error_hover"], self._stop_hours, state="disabled")

        # Stdin Continue button
        self.btn_hours_continue = ctk.CTkButton(
            btn_bar, text="Continue ✔", width=110, height=36,
            corner_radius=8, fg_color=C["primary"], hover_color=C["primary_hover"],
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#ffffff",
            command=lambda: self._release_stdin("hours")
        )
        self.lbl_hours_continue = ctk.CTkLabel(
            btn_bar, text="Waiting for confirmation...", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["warning"]
        )

        # Log Panel
        log_hdr = ctk.CTkFrame(self.hours_panel, fg_color="transparent")
        log_hdr.grid(row=3, column=0, sticky="ew", pady=(4, 2))
        ctk.CTkLabel(log_hdr, text="Monetization Hours Logs", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg3"]).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear Log", width=80, height=22,
            fg_color="transparent", text_color=C["fg3"], hover_color=C["border"],
            font=ctk.CTkFont("Segoe UI", 10, "bold"), command=self._clear_hours_log
        ).pack(side="left")

        self.txt_hours_log = ctk.CTkTextbox(
            self.hours_panel, font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"], text_color="#d4d4d8",
            border_color=C["border"], border_width=1, corner_radius=8,
            wrap="word", state="disabled"
        )
        self.txt_hours_log.grid(row=4, column=0, sticky="nsew", pady=(0, 10))

        # Status Bar
        self.hours_bar = ctk.CTkFrame(self.hours_panel, fg_color=C["card"], height=32, corner_radius=6, border_width=1, border_color=C["border"])
        self.hours_bar.grid(row=6, column=0, sticky="ew")
        self.hours_bar.pack_propagate(False)

        self.lbl_hours_status = ctk.CTkLabel(
            self.hours_bar, text="Ready", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["fg2"]
        )
        self.lbl_hours_status.pack(side="left", padx=15)

        self.hours_progress = ctk.CTkProgressBar(
            self.hours_bar, width=180, height=6, fg_color=C["border"], progress_color=C["primary"]
        )
        self.hours_progress.pack(side="right", padx=15, pady=13)
        self.hours_progress.set(0)

    def _clear_hours_log(self):
        self._clear_text_box(self.txt_hours_log)


    # ─── Shared GUI Helpers ───────────────────────────────────────────────────
    def _make_control_btn(self, parent, text, color, hover_color, cmd, state="normal", width=120):
        btn = ctk.CTkButton(
            parent, text=text, width=width, height=36, corner_radius=8,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#ffffff",
            text_color_disabled=C["disabled_text"], fg_color=color, hover_color=hover_color,
            command=cmd, state=state
        )
        btn.pack(side="left", padx=(0, 8))
        return btn

    def _set_btn_state(self, btn, state, active_color=None, active_hover=None):
        if state == "normal" or state == "active":
            btn.configure(state="normal")
            if active_color:
                btn.configure(fg_color=active_color)
            if active_hover:
                btn.configure(hover_color=active_hover)
        else:
            btn.configure(state="disabled", fg_color=C["disabled_fg"])

    def _set_global_status(self, text, text_color):
        self.lbl_global_status.configure(text=text, text_color=text_color)

    def _clear_text_box(self, textbox):
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        textbox.configure(state="disabled")

    def _append_log(self, textbox, message, style=None):
        ts = datetime.now().strftime("%H:%M:%S")
        
        # Select color tag mapping
        tag = None
        if style:
            style_low = style.lower()
            if "green" in style_low or "success" in style_low: tag = "success"
            elif "red" in style_low or "error" in style_low: tag = "error"
            elif "yellow" in style_low or "warning" in style_low: tag = "warning"
            elif "accent" in style_low or "cyan" in style_low: tag = "accent"
            elif "dim" in style_low: tag = "dim"
        else:
            low = message.lower()
            if any(w in low for w in ("✓", "success", "done", "added", "enabled", "found")):
                tag = "success"
            elif any(w in low for w in ("✗", "error", "fail", "exception", "traceback")):
                tag = "error"
            elif any(w in low for w in ("warning", "warn", "skip", "timeout")):
                tag = "warning"
                
        textbox.configure(state="normal")
        
        # Configure tags dynamically if needed (Tkinter base Text object tags)
        tw = textbox._textbox
        tw.tag_configure("success", foreground=C["success"])
        tw.tag_configure("error",   foreground=C["error"])
        tw.tag_configure("warning", foreground=C["warning"])
        tw.tag_configure("accent",  foreground=C["primary"])
        tw.tag_configure("dim",     foreground=C["fg3"])

        tw.insert("end", f"[{ts}]  ", "dim")
        tw.insert("end", message + "\n", tag or "")
        textbox.see("end")
        textbox.configure(state="disabled")

    # ─── Script Execution Helpers ─────────────────────────────────────────────
    def _poll_log_queue(self):
        try:
            while True:
                tool, message, style = self.log_queue.get_nowait()
                if tool == "setup":
                    self._append_log(self.txt_setup_log, message, style)
                elif tool == "delete":
                    self._append_log(self.txt_delete_log, message, style)
                elif tool == "hours":
                    self._append_log(self.txt_hours_log, message, style)
                elif tool == "endscreen":
                    self._append_log(self.txt_endscreen_log, message, style)
                elif tool == "publish":
                    self._append_log(self.publish_log, message, style)
        except queue.Empty:
            pass
        self.after(200, self._poll_log_queue)

    def _queue_log(self, tool, message, style=None):
        self.log_queue.put((tool, message, style))

    # ─── Stdin Handlers ───────────────────────────────────────────────────────
    def _release_stdin(self, tool):
        if tool == "setup":
            self.setup_input_value[0] = ""
            self.setup_input_gate.set()
        elif tool == "delete":
            self.delete_input_value[0] = ""
            self.delete_input_gate.set()
        elif tool == "hours":
            self.hours_input_value[0] = ""
            self.hours_input_gate.set()
        elif tool == "endscreen":
            self.endscreen_input_value[0] = ""
            self.endscreen_input_gate.set()

    def _show_continue_prompt(self, tool, prompt):
        self._queue_log(tool, f"⏸ {prompt}", "warning")
        if tool == "setup":
            self.lbl_setup_continue.pack(side="left", padx=10)
            self.btn_setup_continue.pack(side="left")
        elif tool == "delete":
            self.lbl_delete_continue.pack(side="left", padx=10)
            self.btn_delete_continue.pack(side="left")
        elif tool == "hours":
            self.lbl_hours_continue.pack(side="left", padx=10)
            self.btn_hours_continue.pack(side="left")
        elif tool == "endscreen":
            self.lbl_endscreen_continue.pack(side="left", padx=10)
            self.btn_endscreen_continue.pack(side="left")

    def _hide_continue_prompt(self, tool):
        if tool == "setup":
            self.lbl_setup_continue.pack_forget()
            self.btn_setup_continue.pack_forget()
        elif tool == "delete":
            self.lbl_delete_continue.pack_forget()
            self.btn_delete_continue.pack_forget()
        elif tool == "hours":
            self.lbl_hours_continue.pack_forget()
            self.btn_hours_continue.pack_forget()
        elif tool == "endscreen":
            self.lbl_endscreen_continue.pack_forget()
            self.btn_endscreen_continue.pack_forget()


    # ──────────────────────────────────────────────────────────────────────────
    # TOOL 1: Channel Setup Methods
    # ──────────────────────────────────────────────────────────────────────────
    def _start_setup(self):
        if self.active_tool:
            messagebox.showwarning("Warning", f"Please wait for the current tool ({self.active_tool}) to finish.")
            return

        # Read input handles
        raw = self.txt_setup_handles.get("1.0", "end").strip()
        handles = []
        for line in raw.splitlines():
            h = line.strip()
            if h:
                if h.startswith("@") or not h.isalnum(): # Handle title or @handle
                    handles.append(h)
                else:
                    handles.append("@" + h)

        if not handles:
            messagebox.showwarning("Warning", "Please enter at least one channel handle.")
            return

        self.active_tool = "setup"
        self._set_global_status("Channel Setup is running...", C["warning"])
        self.setup_progress.start()
        self._pulse_global_status()
        
        self.setup_stop_event.clear()
        self.setup_pause_event.set()
        
        # Update buttons
        self._set_btn_state(self.btn_setup_start, "disabled")
        self._set_btn_state(self.btn_setup_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_setup_resume, "disabled")
        self._set_btn_state(self.btn_setup_stop, "normal", C["error"], C["error_hover"])
        self.setup_progress.set(0)
        self.lbl_setup_status.configure(text="Initializing and launching...")

        # Sync profile path
        auto_set_script.CHROME_PROFILE_PATH = self.global_chrome_profile.get()
        auto_set_script.REMOTE_DEBUGGING_PORT = self._get_active_profile_port()

        # Run in thread
        t = threading.Thread(target=self._run_setup_worker, args=(handles,), daemon=True)
        t.start()

    def _run_setup_worker(self, handles):
        # Override printing
        def custom_print(message="", style=None):
            self._queue_log("setup", message, style)
        
        auto_set_script.console.print = custom_print
        auto_set_script.STOP_EVENT = self.setup_stop_event
        auto_set_script.PAUSE_EVENT = self.setup_pause_event
        
        # Override run mode
        mode_val = self.setup_run_mode_var.get()
        auto_set_script.RUN_MODE = "logo" if "Logo" in mode_val else "data"
        
        total = len(handles)
        results = []

        # Intercept stdio
        with ThreadIOContext(
            lambda msg, style=None: self._queue_log("setup", msg, style),
            lambda: self.after(0, self._show_continue_prompt, "setup", "Waiting for continue..."),
            self.setup_input_gate,
            self.setup_input_value,
            "accent"
        ):
            try:
                with auto_set_script.sync_playwright() as playwright:
                    context, page = auto_set_script.launch_browser(playwright)

                    # Login step if needed
                    if getattr(auto_set_script, "PAUSE_FOR_LOGIN", True):
                        self._queue_log("setup", "Checking login status...", "accent")
                        auto_set_script.wait_for_manual_login(page)

                    # Pre-load youtube homepage
                    try:
                        page.goto("https://www.youtube.com/", wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        pass

                    for idx, handle in enumerate(handles, 1):
                        if self.setup_stop_event.is_set():
                            break
                        self.setup_pause_event.wait()

                        # Update GUI Status
                        self.after(0, lambda i=idx, t=total, h=handle: (
                            self.setup_progress.stop(),
                            self.setup_progress.set(i / t),
                            self.lbl_setup_status.configure(text=f"Processing {i}/{t}: {h}")
                        ))

                        self._queue_log("setup", f"── [{idx}/{total}] Processing channel: {handle}", "accent")
                        result = auto_set_script.process_channel(context, handle, idx, total)
                        results.append(result)

                        # Write individual channel CSV
                        try:
                            safe_handle = "".join(c if c.isalnum() else "_" for c in handle).strip("_") or f"channel_{idx}"
                            ch_csv_path = BASE_DIR / f"{safe_handle}.csv"
                            with open(ch_csv_path, mode="w", encoding="utf-8-sig", newline="") as f:
                                writer = csv.writer(f)
                                writer.writerow([
                                    "Handle", "Channel ID", "Website Link Added", 
                                    "Email Added", "Home Tab Enabled", "Livestreaming Data", "Status", "Error"
                                ])
                                writer.writerow([
                                    result.get("handle", ""), result.get("channel_id", ""),
                                    "TRUE" if result.get("link_status") in ("added", "updated", "already_exists") else "FALSE",
                                    "TRUE" if result.get("email_status") in ("added", "updated", "already_exists") else "FALSE",
                                    "TRUE" if result.get("home_tab_status") in ("enabled", "enabled_now") else "FALSE",
                                    "TRUE" if result.get("livestreaming_status") in ("opened", "completed") else "FALSE",
                                    result.get("status", ""), result.get("error", "")
                                ])
                        except Exception as ch_exc:
                            self._queue_log("setup", f"Warning: Could not save individual CSV: {ch_exc}", "yellow")

                    context.close()

                # Generate global report
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_filename = f"report_{ts}.csv"
                csv_path = BASE_DIR / csv_filename
                try:
                    with open(csv_path, mode="w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow([
                            "Handle", "Channel ID", "Website Link Added", 
                            "Email Added", "Home Tab Enabled", "Livestreaming Data", "Status", "Error"
                        ])
                        for r in results:
                            writer.writerow([
                                r.get("handle", ""), r.get("channel_id", ""),
                                "TRUE" if r.get("link_status") in ("added", "updated", "already_exists") else "FALSE",
                                "TRUE" if r.get("email_status") in ("added", "updated", "already_exists") else "FALSE",
                                "TRUE" if r.get("home_tab_status") in ("enabled", "enabled_now") else "FALSE",
                                "TRUE" if r.get("livestreaming_status") in ("opened", "completed") else "FALSE",
                                r.get("status", ""), r.get("error", "")
                            ])
                    self._queue_log("setup", f"Global report successfully saved to: {csv_filename}", "success")
                except Exception as csv_exc:
                    self._queue_log("setup", f"Warning: Could not save global report: {csv_exc}", "yellow")

                self.after(0, lambda: self.setup_progress.set(1.0))
                self.after(0, lambda: self.lbl_setup_status.configure(text="Completed successfully"))
                self._queue_log("setup", "✓ Channel setup completed successfully!", "success")
            except Exception as e:
                self._queue_log("setup", f"✗ Fatal error in Setup tool: {e}", "error")
                self.after(0, lambda: self.lbl_setup_status.configure(text="Failed"))
            finally:
                self.after(0, self.setup_progress.stop)
                self.after(0, self._hide_continue_prompt, "setup")
                self.active_tool = None
                self.after(0, self._reset_setup_buttons)
                self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))

    def _reset_setup_buttons(self):
        # Update run mode start button styling
        self._on_setup_mode_change(self.setup_run_mode_var.get())
        self.btn_setup_start.configure(state="normal")
        self._set_btn_state(self.btn_setup_pause, "disabled")
        self._set_btn_state(self.btn_setup_resume, "disabled")
        self._set_btn_state(self.btn_setup_stop, "disabled")

    def _pause_setup(self):
        self.setup_pause_event.clear()
        self._set_btn_state(self.btn_setup_pause, "disabled")
        self._set_btn_state(self.btn_setup_resume, "normal", C["success"], C["success_hover"])
        self._queue_log("setup", "⏸ Process paused (will pause after current channel step).", "warning")
        self.lbl_setup_status.configure(text="Paused...")

    def _resume_setup(self):
        self.setup_pause_event.set()
        self._set_btn_state(self.btn_setup_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_setup_resume, "disabled")
        self._queue_log("setup", "▶ Process resumed.", "success")
        self.lbl_setup_status.configure(text="Processing...")

    def _stop_setup(self):
        self.setup_stop_event.set()
        self.setup_pause_event.set() # Unblock if paused
        self.setup_input_gate.set() # Unblock stdin
        self._queue_log("setup", "⏹ Stop command sent. Please wait...", "error")
        self._set_btn_state(self.btn_setup_stop, "disabled")
        self._set_btn_state(self.btn_setup_pause, "disabled")
        self._set_btn_state(self.btn_setup_resume, "disabled")
        self.lbl_setup_status.configure(text="Stopping...")


    # ──────────────────────────────────────────────────────────────────────────
    # TOOL 2: Auto Delete Videos Methods
    # ──────────────────────────────────────────────────────────────────────────
    def _start_delete(self):
        if self.active_tool:
            messagebox.showwarning("Warning", f"Please wait for the current tool ({self.active_tool}) to finish.")
            return

        if not self.delete_live_var.get() and not self.delete_shorts_var.get() and not self.delete_uploads_var.get():
            messagebox.showwarning("Warning", "Please select at least one delete option (Live, Shorts, or Main Videos).")
            return

        # Check manual channels if enabled
        handles = []
        if self.delete_manual_input_var.get():
            if getattr(self.txt_delete_manual, "is_placeholder_active", False):
                raw_text = ""
            else:
                raw_text = self.txt_delete_manual.get("1.0", "end").strip()
            
            if not raw_text:
                messagebox.showwarning("Warning", "Please enter at least one channel handle.")
                return
            
            for line in raw_text.splitlines():
                part = line.strip()
                if part:
                    part = part.rstrip(",")
                    if not part.startswith('@'):
                        part = '@' + part
                    handles.append(part)
            
            if not handles:
                messagebox.showwarning("Warning", "Please enter valid channel handles.")
                return

        # Verify subscriber limit
        threshold = 1000
        if self.delete_filter_subs_var.get():
            try:
                threshold = int(self.delete_subs_threshold_var.get())
            except ValueError:
                messagebox.showwarning("Warning", "Please enter a valid subscriber threshold (integer).")
                return

        self.active_tool = "delete"
        self._set_global_status("Auto Delete tool is running...", C["warning"])
        self.delete_progress.start()
        self._pulse_global_status()
        
        self.delete_stop_event.clear()
        self.delete_pause_event.set()
        auto_delete_script.skip_channel_event = threading.Event()
        auto_delete_script.skip_channel_event.clear()

        # Update button states
        self._set_btn_state(self.btn_delete_start, "disabled")
        self._set_btn_state(self.btn_delete_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_delete_resume, "disabled")
        self._set_btn_state(self.btn_delete_skip, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_delete_stop, "normal", C["error"], C["error_hover"])
        self.delete_progress.set(0)
        self.lbl_delete_status.configure(text="Initializing and launching...")

        # Sync profile settings
        auto_delete_script.CHROME_PROFILE_PATH = self.global_chrome_profile.get()
        auto_delete_script.REMOTE_DEBUGGING_PORT = self._get_active_profile_port()
        auto_delete_script.DELETE_LIVE = self.delete_live_var.get()
        auto_delete_script.DELETE_SHORTS = self.delete_shorts_var.get()
        auto_delete_script.DELETE_UPLOADS = self.delete_uploads_var.get()
        auto_delete_script.USE_MANUAL_CHANNELS = self.delete_manual_input_var.get()
        auto_delete_script.MANUAL_CHANNELS = handles
        auto_delete_script.FILTER_BY_SUB_COUNT = self.delete_filter_subs_var.get()
        auto_delete_script.SUB_COUNT_THRESHOLD = threshold

        # Run in thread
        t = threading.Thread(target=self._run_delete_worker, daemon=True)
        t.start()

    def _run_delete_worker(self):
        # Override printing
        def custom_print(message="", style=None):
            self._queue_log("delete", message, style)
        
        auto_delete_script.console.print = custom_print
        auto_delete_script.stop_event = self.delete_stop_event
        auto_delete_script.pause_event = self.delete_pause_event
        
        # Override status hook inside the loop of the script
        # In script.py process_channel outputs to console, but we also want progress tracking
        # Let's wrap standard print or rely on script output
        with ThreadIOContext(
            lambda msg, style=None: self._queue_log("delete", msg, style),
            lambda: self.after(0, self._show_continue_prompt, "delete", "Waiting for continue..."),
            self.delete_input_gate,
            self.delete_input_value,
            "accent"
        ):
            try:
                # Run main logic
                auto_delete_script.main()
                self.after(0, lambda: self.delete_progress.set(1.0))
                self.after(0, lambda: self.lbl_delete_status.configure(text="Completed successfully"))
            except Exception as e:
                self._queue_log("delete", f"✗ Fatal error in Auto Delete: {e}", "error")
                self.after(0, lambda: self.lbl_delete_status.configure(text="Failed"))
            finally:
                self.after(0, self.delete_progress.stop)
                self.after(0, self._hide_continue_prompt, "delete")
                self.active_tool = None
                self.after(0, self._reset_delete_buttons)
                self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))

    def _reset_delete_buttons(self):
        self.btn_delete_start.configure(state="normal")
        self._set_btn_state(self.btn_delete_pause, "disabled")
        self._set_btn_state(self.btn_delete_resume, "disabled")
        self._set_btn_state(self.btn_delete_skip, "disabled")
        self._set_btn_state(self.btn_delete_stop, "disabled")

    def _pause_delete(self):
        self.delete_pause_event.clear()
        self._set_btn_state(self.btn_delete_pause, "disabled")
        self._set_btn_state(self.btn_delete_resume, "normal", C["success"], C["success_hover"])
        self._queue_log("delete", "⏸ Process paused (will pause after current channel).", "warning")
        self.lbl_delete_status.configure(text="Paused...")

    def _resume_delete(self):
        self.delete_pause_event.set()
        self._set_btn_state(self.btn_delete_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_delete_resume, "disabled")
        self._queue_log("delete", "▶ Process resumed.", "success")
        self.lbl_delete_status.configure(text="Deleting...")

    def _skip_delete_channel(self):
        if hasattr(auto_delete_script, "skip_channel_event"):
            auto_delete_script.skip_channel_event.set()
            self._queue_log("delete", "⏭ Skip channel command sent. Moving to next channel...", "warning")

    def _stop_delete(self):
        self.delete_stop_event.set()
        self.delete_pause_event.set() # Unblock if paused
        self.delete_input_gate.set() # Unblock stdin
        self._queue_log("delete", "⏹ Stop command sent. Please wait...", "error")
        self._set_btn_state(self.btn_delete_stop, "disabled")
        self._set_btn_state(self.btn_delete_pause, "disabled")
        self._set_btn_state(self.btn_delete_resume, "disabled")
        self._set_btn_state(self.btn_delete_skip, "disabled")
        self.lbl_delete_status.configure(text="Stopping...")


    # ──────────────────────────────────────────────────────────────────────────
    # TOOL 3: See Hours Methods
    # ──────────────────────────────────────────────────────────────────────────
    def _start_hours(self):
        if self.active_tool:
            messagebox.showwarning("Warning", f"Please wait for the current tool ({self.active_tool}) to finish.")
            return

        self.active_tool = "hours"
        self._set_global_status("See Hours tool is running...", C["warning"])
        self.hours_progress.start()
        self._pulse_global_status()
        
        self.hours_stop_event.clear()
        self.hours_pause_event.set()

        # Update button states
        self._set_btn_state(self.btn_hours_start, "disabled")
        self._set_btn_state(self.btn_hours_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_hours_resume, "disabled")
        self._set_btn_state(self.btn_hours_stop, "normal", C["error"], C["error_hover"])
        self.hours_progress.set(0)
        self.lbl_hours_status.configure(text="Initializing and launching...")

        # Sync profile settings
        see_hours_script.CHROME_PROFILE_PATH = self.global_chrome_profile.get()
        see_hours_script.REMOTE_DEBUGGING_PORT = self._get_active_profile_port()

        # Run in thread
        t = threading.Thread(target=self._run_hours_worker, daemon=True)
        t.start()

    def _run_hours_worker(self):
        # Override printing
        def custom_print(message="", style=None):
            self._queue_log("hours", message, style)
        
        see_hours_script.console.print = custom_print
        see_hours_script.stop_event = self.hours_stop_event
        see_hours_script.pause_event = self.hours_pause_event

        with ThreadIOContext(
            lambda msg, style=None: self._queue_log("hours", msg, style),
            lambda: self.after(0, self._show_continue_prompt, "hours", "Waiting for continue..."),
            self.hours_input_gate,
            self.hours_input_value,
            "accent"
        ):
            try:
                # Run main logic
                see_hours_script.main()
                self.after(0, lambda: self.hours_progress.set(1.0))
                self.after(0, lambda: self.lbl_hours_status.configure(text="Completed successfully"))
            except Exception as e:
                self._queue_log("hours", f"✗ Fatal error in See Hours: {e}", "error")
                self.after(0, lambda: self.lbl_hours_status.configure(text="Failed"))
            finally:
                self.after(0, self.hours_progress.stop)
                self.after(0, self._hide_continue_prompt, "hours")
                self.active_tool = None
                self.after(0, self._reset_hours_buttons)
                self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))

    def _reset_hours_buttons(self):
        self.btn_hours_start.configure(state="normal")
        self._set_btn_state(self.btn_hours_pause, "disabled")
        self._set_btn_state(self.btn_hours_resume, "disabled")
        self._set_btn_state(self.btn_hours_stop, "disabled")

    def _pause_hours(self):
        self.hours_pause_event.clear()
        self._set_btn_state(self.btn_hours_pause, "disabled")
        self._set_btn_state(self.btn_hours_resume, "normal", C["success"], C["success_hover"])
        self._queue_log("hours", "⏸ Process paused (will pause after current channel).", "warning")
        self.lbl_hours_status.configure(text="Paused...")

    def _resume_hours(self):
        self.hours_pause_event.set()
        self._set_btn_state(self.btn_hours_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_hours_resume, "disabled")
        self._queue_log("hours", "▶ Process resumed.", "success")
        self.lbl_hours_status.configure(text="Fetching watch hours...")

    def _stop_hours(self):
        self.hours_stop_event.set()
        self.hours_pause_event.set() # Unblock if paused
        self.hours_input_gate.set() # Unblock stdin
        self._queue_log("hours", "⏹ Stop command sent. Please wait...", "error")
        self._set_btn_state(self.btn_hours_stop, "disabled")
        self._set_btn_state(self.btn_hours_pause, "disabled")
        self._set_btn_state(self.btn_hours_resume, "disabled")
        self.lbl_hours_status.configure(text="Stopping...")


    # ──────────────────────────────────────────────────────────────────────────
    # TOOL 4: EndScreen & Premiere Methods
    # ──────────────────────────────────────────────────────────────────────────
    def _build_endscreen_panel(self):
        self.endscreen_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.endscreen_panel.grid_rowconfigure(5, weight=1)
        self.endscreen_panel.grid_columnconfigure(0, weight=1)

        # Title card
        title_card = ctk.CTkFrame(self.endscreen_panel, fg_color="transparent", height=40)
        title_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(
            title_card, text="Set End Screen & Premiere",
            font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=C["fg"]
        ).pack(side="left")

        # Input Options Card
        card = ctk.CTkFrame(self.endscreen_panel, fg_color=C["card"], border_width=1, border_color=C["border"], corner_radius=12)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        # Row 1: max_to_collect & template_card_index
        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=10)

        # Template Card Index
        ctk.CTkLabel(
            row1, text="Template Card Index (1-4):",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left", padx=(0, 10))

        self.endscreen_card_index_var = ctk.StringVar(value="3")
        self.entry_endscreen_card_index = ctk.CTkEntry(
            row1, textvariable=self.endscreen_card_index_var, width=60, height=28,
            font=ctk.CTkFont("Consolas", 12), fg_color=C["bg_dark"], border_color=C["border"], text_color=C["fg"]
        )
        self.entry_endscreen_card_index.pack(side="left")

        # Max to Collect
        self.endscreen_max_videos_var = ctk.StringVar(value="0")
        self.entry_endscreen_max_videos = ctk.CTkEntry(
            row1, textvariable=self.endscreen_max_videos_var, width=60, height=28,
            font=ctk.CTkFont("Consolas", 12), fg_color=C["bg_dark"], border_color=C["border"], text_color=C["fg"]
        )
        self.entry_endscreen_max_videos.pack(side="right")

        ctk.CTkLabel(
            row1, text="Max videos to process (0 for all):",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="right", padx=(0, 10))

        # Row 2: Checkboxes
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(0, 10))

        self.endscreen_teaser_var = ctk.BooleanVar(value=True)
        self.chk_endscreen_teaser = ctk.CTkCheckBox(
            row2, text="Enable Card Teaser Outline", variable=self.endscreen_teaser_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg"],
            fg_color=C["primary"], border_color=C["border"]
        )
        self.chk_endscreen_teaser.pack(side="left", padx=(0, 10))

        self.endscreen_enable_premiere_var = ctk.BooleanVar(value=True)
        self.chk_endscreen_enable_premiere = ctk.CTkCheckBox(
            row2, text="Enable Premiere for Scheduled", variable=self.endscreen_enable_premiere_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg"],
            fg_color=C["primary"], border_color=C["border"]
        )
        self.chk_endscreen_enable_premiere.pack(side="right", padx=(10, 0))

        # Controls row
        btn_bar = ctk.CTkFrame(self.endscreen_panel, fg_color="transparent")
        btn_bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.btn_endscreen_start = self._make_control_btn(btn_bar, "Start Processing ▶", C["success"], C["success_hover"], self._start_endscreen)
        self.btn_endscreen_pause = self._make_control_btn(btn_bar, "Pause ⏸", C["warning"], C["warning_hover"], self._pause_endscreen, state="disabled")
        self.btn_endscreen_resume = self._make_control_btn(btn_bar, "Resume ▶", C["success"], C["success_hover"], self._resume_endscreen, state="disabled")
        self.btn_endscreen_stop = self._make_control_btn(btn_bar, "Stop ⏹", C["error"], C["error_hover"], self._stop_endscreen, state="disabled")

        # Stdin Continue button (unused but nice for safety)
        self.btn_endscreen_continue = ctk.CTkButton(
            btn_bar, text="Continue ✔", width=110, height=36,
            corner_radius=8, fg_color=C["primary"], hover_color=C["primary_hover"],
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#ffffff",
            command=lambda: self._release_stdin("endscreen")
        )
        self.btn_endscreen_continue.pack_forget()
        self.lbl_endscreen_continue = ctk.CTkLabel(
            btn_bar, text="Waiting for confirmation...", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["warning"]
        )
        self.lbl_endscreen_continue.pack_forget()

        # Log Panel
        log_hdr = ctk.CTkFrame(self.endscreen_panel, fg_color="transparent")
        log_hdr.grid(row=3, column=0, sticky="ew", pady=(4, 2))
        ctk.CTkLabel(log_hdr, text="EndScreen & Premiere Logs", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg3"]).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear Log", width=80, height=22,
            fg_color="transparent", text_color=C["fg3"], hover_color=C["border"],
            font=ctk.CTkFont("Segoe UI", 10, "bold"), command=self._clear_endscreen_log
        ).pack(side="left")

        self.txt_endscreen_log = ctk.CTkTextbox(
            self.endscreen_panel, font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"], text_color="#d4d4d8",
            border_color=C["border"], border_width=1, corner_radius=8,
            wrap="word", state="disabled"
        )
        self.txt_endscreen_log.grid(row=4, column=0, sticky="nsew", pady=(0, 10))

        # Status Bar
        self.endscreen_bar = ctk.CTkFrame(self.endscreen_panel, fg_color=C["card"], height=32, corner_radius=6, border_width=1, border_color=C["border"])
        self.endscreen_bar.grid(row=6, column=0, sticky="ew")
        self.endscreen_bar.pack_propagate(False)

        self.lbl_endscreen_status = ctk.CTkLabel(
            self.endscreen_bar, text="Ready", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["fg2"]
        )
        self.lbl_endscreen_status.pack(side="left", padx=15)

        self.endscreen_progress = ctk.CTkProgressBar(
            self.endscreen_bar, width=180, height=6, fg_color=C["border"], progress_color=C["primary"]
        )
        self.endscreen_progress.pack(side="right", padx=15, pady=13)
        self.endscreen_progress.set(0)

    def _clear_endscreen_log(self):
        self._clear_text_box(self.txt_endscreen_log)

    def _start_endscreen(self):
        if self.active_tool:
            messagebox.showwarning("Warning", f"Please wait for the current tool ({self.active_tool}) to finish.")
            return

        try:
            max_val = int(self.endscreen_max_videos_var.get() or 0)
        except ValueError:
            messagebox.showwarning("Warning", "Please enter a valid number of videos.")
            return

        try:
            card_idx = int(self.endscreen_card_index_var.get() or 3)
        except ValueError:
            messagebox.showwarning("Warning", "Please enter a valid Template Card Index.")
            return

        self.active_tool = "endscreen"
        self._set_global_status("EndScreen tool is running...", C["warning"])
        self.endscreen_progress.start()
        self._pulse_global_status()
        
        self.endscreen_stop_event.clear()
        self.endscreen_pause_event.set()

        # Update button states
        self._set_btn_state(self.btn_endscreen_start, "disabled")
        self._set_btn_state(self.btn_endscreen_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_endscreen_resume, "disabled")
        self._set_btn_state(self.btn_endscreen_stop, "normal", C["error"], C["error_hover"])
        self.endscreen_progress.set(0)
        self.lbl_endscreen_status.configure(text="Initializing and launching...")

        # Run in thread
        t = threading.Thread(target=self._run_endscreen_worker, daemon=True)
        t.start()

    def _run_endscreen_worker(self):
        # Override printing
        def custom_print(message="", style=None):
            self._queue_log("endscreen", message, style)
        
        # Override print in end_screen_script
        end_screen_script.print = lambda *args, **kwargs: (
            self._queue_log("endscreen", " ".join(str(x) for x in args))
        )
        end_screen_script.stop_event = self.endscreen_stop_event
        end_screen_script.pause_event = self.endscreen_pause_event

        # Build config dict
        config = {
            "chrome_user_data_root": self.base_profiles_dir,
            "chrome_profile_directory": self.active_profile_name,
            "chrome_executable": auto_set_script.find_chrome_executable() or "",
            "headless": False,
            "slow_mo": 250,
            "max_to_collect": int(self.endscreen_max_videos_var.get() or 0),
            "enable_premiere_for_scheduled": self.endscreen_enable_premiere_var.get(),
            "template_card_index": int(self.endscreen_card_index_var.get() or 3),
            "enable_card_teaser_outline": self.endscreen_teaser_var.get(),
            "navigation_timeout_ms": 45000,
            "action_timeout_ms": 12000,
            "max_concurrent_tabs": 15,
            "ignore_automation_flag": True,
            "connect_over_cdp_url": f"http://localhost:{self._get_active_profile_port()}",
            "studio_videos_url": "",
            "auto_confirm": True,
            "process_all_channels": True,
            "max_channels": 5,
            "max_videos_per_channel": 5,
            "video_visibility_target": "scheduled",
            "template_keywords": [
                "1 video, 1 subscribe",
                "1 video 1 subscribe",
                "one video one subscribe",
                "فيديو واحد",
                "اشتراك واحد",
                "1 video, 1 subscribe",
                "1 video 1 subscribe",
                "one video one subscribe",
            ],
            "screenshots_dir": str(BASE_DIR / "screenshots"),
            "debug_dir": str(BASE_DIR / "debug"),
            "results_json": str(BASE_DIR / "results.json"),
            "results_csv": str(BASE_DIR / "results.csv"),
        }

        with ThreadIOContext(
            lambda msg, style=None: self._queue_log("endscreen", msg, style),
            lambda: self.after(0, self._show_continue_prompt, "endscreen", "Waiting for continue..."),
            self.endscreen_input_gate,
            self.endscreen_input_value,
            "accent"
        ):
            try:
                # Instantiate bot and run
                bot = end_screen_script.YouTubeStudioTemplateBot(config)
                bot.run()
                self.after(0, lambda: self.endscreen_progress.set(1.0))
                self.after(0, lambda: self.lbl_endscreen_status.configure(text="Completed successfully"))
            except Exception as e:
                self._queue_log("endscreen", f"✗ Error in EndScreen: {e}", "error")
                self.after(0, lambda: self.lbl_endscreen_status.configure(text="Failed"))
            finally:
                self.after(0, self.endscreen_progress.stop)
                self.after(0, self._hide_continue_prompt, "endscreen")
                self.active_tool = None
                self.after(0, self._reset_endscreen_buttons)
                self.after(0, lambda: self._set_global_status("Status: Ready", C["success"]))

    def _reset_endscreen_buttons(self):
        self.btn_endscreen_start.configure(state="normal")
        self._set_btn_state(self.btn_endscreen_pause, "disabled")
        self._set_btn_state(self.btn_endscreen_resume, "disabled")
        self._set_btn_state(self.btn_endscreen_stop, "disabled")

    def _pause_endscreen(self):
        self.endscreen_pause_event.clear()
        self._set_btn_state(self.btn_endscreen_pause, "disabled")
        self._set_btn_state(self.btn_endscreen_resume, "normal", C["success"], C["success_hover"])
        self._queue_log("endscreen", "⏸ Process paused.", "warning")
        self.lbl_endscreen_status.configure(text="Paused...")

    def _resume_endscreen(self):
        self.endscreen_pause_event.set()
        self._set_btn_state(self.btn_endscreen_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_endscreen_resume, "disabled")
        self._queue_log("endscreen", "▶ Process resumed.", "success")
        self.lbl_endscreen_status.configure(text="Processing...")

    def _stop_endscreen(self):
        self.endscreen_stop_event.set()
        self.endscreen_pause_event.set() # Unblock if paused
        self.endscreen_input_gate.set() # Unblock stdin
        self._queue_log("endscreen", "⏹ Stop command sent. Please wait...", "error")
        self._set_btn_state(self.btn_endscreen_stop, "disabled")
        self._set_btn_state(self.btn_endscreen_pause, "disabled")
        self._set_btn_state(self.btn_endscreen_resume, "disabled")
        self.lbl_endscreen_status.configure(text="Stopping...")


    # ─── Publish Posts Panel ──────────────────────────────────────────────────
    def _build_publish_panel(self):
        self.publish_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.publish_panel.grid_rowconfigure(5, weight=1)
        self.publish_panel.grid_columnconfigure(0, weight=1)

        # Title card
        title_card = ctk.CTkFrame(self.publish_panel, fg_color="transparent", height=40)
        title_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(
            title_card, text="Publish Posts",
            font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=C["fg"]
        ).pack(side="left")

        # ── Post Content Card ─────────────────────────────────────────────────
        content_card = ctk.CTkFrame(
            self.publish_panel, fg_color=C["card"],
            border_width=1, border_color=C["border"], corner_radius=12
        )
        content_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        # Post Mode (Text only / Text + Image)
        mode_row = ctk.CTkFrame(content_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=16, pady=(12, 6))

        ctk.CTkLabel(
            mode_row, text="Post Mode:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left", padx=(0, 14))

        self.publish_mode_var = ctk.StringVar(value="text_only")

        ctk.CTkRadioButton(
            mode_row,
            text="Text Only",
            variable=self.publish_mode_var,
            value="text_only",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C["fg"],
            fg_color=C["primary"],
            command=self._on_publish_mode_change,
        ).pack(side="left", padx=(0, 18))

        ctk.CTkRadioButton(
            mode_row,
            text="Text + Image",
            variable=self.publish_mode_var,
            value="text_image",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C["fg"],
            fg_color=C["primary"],
            command=self._on_publish_mode_change,
        ).pack(side="left", padx=(0, 18))

        ctk.CTkRadioButton(
            mode_row,
            text="YouTube Video",
            variable=self.publish_mode_var,
            value="text_video",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C["fg"],
            fg_color=C["primary"],
            command=self._on_publish_mode_change,
        ).pack(side="left")

        # Post Text Input
        text_label_row = ctk.CTkFrame(content_card, fg_color="transparent")
        text_label_row.pack(fill="x", padx=16, pady=(6, 2))
        
        self.publish_text_enable_var = ctk.BooleanVar(value=True)
        self.chk_publish_text = ctk.CTkCheckBox(
            text_label_row,
            text="Add text to post",
            variable=self.publish_text_enable_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=C["fg2"],
            fg_color=C["primary"],
            command=self._on_text_toggle,
        )
        self.chk_publish_text.pack(side="left")

        self.publish_text_box = ctk.CTkTextbox(
            content_card,
            height=100,
            font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"],
            border_color=C["border"],
            border_width=1,
            text_color=C["fg"],
        )
        self.publish_text_box.pack(fill="x", padx=16, pady=(0, 10))

        # Image Picker Subframe
        self.publish_image_row = ctk.CTkFrame(content_card, fg_color="transparent")
        self.publish_image_row.pack(fill="x", padx=16, pady=(0, 14))

        # 1. Image Source Selector Row (File vs Folder Rotation)
        img_src_row = ctk.CTkFrame(self.publish_image_row, fg_color="transparent")
        img_src_row.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            img_src_row, text="Image Source:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left", padx=(0, 14))

        self.publish_img_src_var = ctk.StringVar(value="file")

        ctk.CTkRadioButton(
            img_src_row,
            text="Single File",
            variable=self.publish_img_src_var,
            value="file",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C["fg"],
            fg_color=C["primary"],
            command=self._on_img_src_change,
        ).pack(side="left", padx=(0, 18))

        ctk.CTkRadioButton(
            img_src_row,
            text="Folder Rotation",
            variable=self.publish_img_src_var,
            value="folder",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C["fg"],
            fg_color=C["primary"],
            command=self._on_img_src_change,
        ).pack(side="left")

        # 2. File Selection Row
        self.publish_img_file_row = ctk.CTkFrame(self.publish_image_row, fg_color="transparent")
        self.publish_img_file_row.pack(fill="x", pady=(2, 2))

        ctk.CTkLabel(
            self.publish_img_file_row, text="Image File:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left", padx=(0, 10))

        self.publish_image_path_var = ctk.StringVar(value="")
        self.lbl_publish_image_path = ctk.CTkLabel(
            self.publish_img_file_row,
            textvariable=self.publish_image_path_var,
            font=ctk.CTkFont("Consolas", 11),
            text_color=C["fg3"],
            anchor="w",
        )
        self.lbl_publish_image_path.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            self.publish_img_file_row,
            text="Browse...",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            fg_color=C["primary"],
            hover_color=C["primary_hover"],
            text_color="#ffffff",
            width=90,
            height=26,
            command=self._browse_publish_image,
        ).pack(side="right")

        # 3. Folder Selection Row
        self.publish_img_folder_row = ctk.CTkFrame(self.publish_image_row, fg_color="transparent")
        # Managed (shown/hidden) dynamically, not packed initially since default is "file"

        ctk.CTkLabel(
            self.publish_img_folder_row, text="Image Folder:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left", padx=(0, 10))

        self.publish_image_folder_var = ctk.StringVar(value="")
        self.lbl_publish_image_folder = ctk.CTkLabel(
            self.publish_img_folder_row,
            textvariable=self.publish_image_folder_var,
            font=ctk.CTkFont("Consolas", 11),
            text_color=C["fg3"],
            anchor="w",
        )
        self.lbl_publish_image_folder.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            self.publish_img_folder_row,
            text="Browse...",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            fg_color=C["primary"],
            hover_color=C["primary_hover"],
            text_color="#ffffff",
            width=90,
            height=26,
            command=self._browse_publish_image_folder,
        ).pack(side="right")

        # Initially hide the entire image container (default mode is text_only)
        self.publish_image_row.pack_forget()

        # ── Subscriber Limit Row ──────────────────────────────────────────────
        sub_limit_row = ctk.CTkFrame(content_card, fg_color="transparent")
        sub_limit_row.pack(fill="x", padx=16, pady=(0, 12))

        self.publish_min_subs_enable_var = ctk.BooleanVar(value=False)
        self.chk_publish_min_subs = ctk.CTkCheckBox(
            sub_limit_row,
            text="Skip channels with subscribers less than:",
            variable=self.publish_min_subs_enable_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=C["fg2"],
            fg_color=C["primary"],
            command=self._on_min_subs_toggle,
        )
        self.chk_publish_min_subs.pack(side="left")

        self.publish_min_subs_var = ctk.StringVar(value="0")
        self.entry_publish_min_subs = ctk.CTkEntry(
            sub_limit_row,
            textvariable=self.publish_min_subs_var,
            width=70,
            height=26,
            font=ctk.CTkFont("Consolas", 12),
            fg_color=C["bg_dark"],
            border_color=C["border"],
            border_width=1,
            text_color=C["fg"],
            state="disabled",
        )
        self.entry_publish_min_subs.pack(side="left", padx=(10, 0))



        # ── Controls Card ─────────────────────────────────────────────────────
        ctrl_card = ctk.CTkFrame(
            self.publish_panel, fg_color=C["card"],
            border_width=1, border_color=C["border"], corner_radius=12
        )
        ctrl_card.grid(row=2, column=0, sticky="ew", pady=(0, 14))

        ctrl_inner = ctk.CTkFrame(ctrl_card, fg_color="transparent")
        ctrl_inner.pack(fill="x", padx=16, pady=12)

        self.btn_publish_start = ctk.CTkButton(
            ctrl_inner,
            text="▶  Start Publishing",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["success"],
            hover_color=C["success_hover"],
            text_color="#ffffff",
            width=160, height=32,
            command=self._start_publish,
        )
        self.btn_publish_start.pack(side="left", padx=(0, 10))

        self.btn_publish_pause = ctk.CTkButton(
            ctrl_inner,
            text="⏸  Pause",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["warning"],
            hover_color=C["warning_hover"],
            text_color="#ffffff",
            width=100, height=32,
            state="disabled",
            command=self._pause_publish,
        )
        self.btn_publish_pause.pack(side="left", padx=(0, 10))

        self.btn_publish_resume = ctk.CTkButton(
            ctrl_inner,
            text="▶  Resume",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["primary"],
            hover_color=C["primary_hover"],
            text_color="#ffffff",
            width=110, height=32,
            state="disabled",
            command=self._resume_publish,
        )
        self.btn_publish_resume.pack(side="left", padx=(0, 10))

        self.btn_publish_stop = ctk.CTkButton(
            ctrl_inner,
            text="⏹  Stop",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C["error"],
            hover_color=C["error_hover"],
            text_color="#ffffff",
            width=100, height=32,
            state="disabled",
            command=self._stop_publish,
        )
        self.btn_publish_stop.pack(side="left", padx=(0, 10))

        # Status
        self.lbl_publish_status = ctk.CTkLabel(
            ctrl_inner,
            text="Idle",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=C["fg3"],
        )
        self.lbl_publish_status.pack(side="right")

        self.publish_progress = ctk.CTkProgressBar(
            ctrl_card,
            fg_color=C["border"],
            progress_color=C["success"],
        )
        self.publish_progress.set(0)
        self.publish_progress.pack(fill="x", padx=16, pady=(0, 12))

        # ── Log Console ───────────────────────────────────────────────────────
        log_card = ctk.CTkFrame(
            self.publish_panel, fg_color=C["card"],
            border_width=1, border_color=C["border"], corner_radius=12
        )
        log_card.grid(row=3, column=0, sticky="nsew", pady=(0, 0))
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        ctk.CTkLabel(
            log_header, text="Activity Log",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["fg2"]
        ).pack(side="left")

        ctk.CTkButton(
            log_header,
            text="Clear Log",
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color="transparent",
            text_color=C["fg3"],
            hover_color=C["border"],
            width=70, height=22,
            command=lambda: self.publish_log.configure(state="normal") or
                            self.publish_log.delete("1.0", "end") or
                            self.publish_log.configure(state="disabled"),
        ).pack(side="right")

        self.publish_log = ctk.CTkTextbox(
            log_card,
            font=ctk.CTkFont("Consolas", 11),
            fg_color=C["bg_dark"],
            text_color=C["fg2"],
            state="disabled",
        )
        self.publish_log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        # Tag colours
        self.publish_log._textbox.tag_config("success", foreground=C["success"])
        self.publish_log._textbox.tag_config("error", foreground=C["error"])
        self.publish_log._textbox.tag_config("warning", foreground=C["warning"])
        self.publish_log._textbox.tag_config("accent", foreground=C["primary"])
        self.publish_log._textbox.tag_config("info", foreground=C["fg2"])

        # Initialize initial modes state
        self._on_publish_mode_change()

    # ── Publish Panel helpers ──────────────────────────────────────────────────

    def _on_img_src_change(self):
        src = self.publish_img_src_var.get()
        if src == "file":
            self.publish_img_folder_row.pack_forget()
            self.publish_img_file_row.pack(fill="x", pady=(2, 2))
        else:
            self.publish_img_file_row.pack_forget()
            self.publish_img_folder_row.pack(fill="x", pady=(2, 2))

    def _on_text_toggle(self):
        if self.publish_text_enable_var.get():
            self.publish_text_box.configure(
                state="normal",
                fg_color=C["bg_dark"],
                text_color=C["fg"],
                border_color=C["border"]
            )
        else:
            self.publish_text_box.configure(
                state="disabled",
                fg_color=C["card"],
                text_color=C["disabled_text"],
                border_color=C["card"]
            )
            self.chk_publish_text.focus_set()

    def _on_publish_mode_change(self):
        mode = self.publish_mode_var.get()
        # Hide all optional rows first
        self.publish_image_row.pack_forget()
        
        # Adjust "Add text to post" checkbox state based on mode
        if mode == "text_only":
            self.publish_text_enable_var.set(True)
            self.chk_publish_text.configure(state="disabled")
            self._on_text_toggle()
        else:
            self.chk_publish_text.configure(state="normal")
            self._on_text_toggle()

        # Show relevant rows based on mode
        if mode == "text_image":
            self.publish_image_row.pack(fill="x", padx=16, pady=(0, 14))
            self._on_img_src_change()

    def _on_min_subs_toggle(self):
        if self.publish_min_subs_enable_var.get():
            self.entry_publish_min_subs.configure(state="normal")
        else:
            self.entry_publish_min_subs.configure(state="disabled")

    def _browse_publish_image(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select Image for Post",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.publish_image_path_var.set(path)

    def _browse_publish_image_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="Select Folder of Images")
        if path:
            self.publish_image_folder_var.set(path)

    def _start_publish(self):
        if self.active_tool:
            messagebox.showwarning("Busy", "Another tool is already running. Please wait.")
            return

        mode = self.publish_mode_var.get()
        img_src = self.publish_img_src_var.get()
        
        if self.publish_text_enable_var.get():
            post_text = self.publish_text_box.get("1.0", "end").strip()
        else:
            post_text = ""
            
        image_path = self.publish_image_path_var.get().strip() or None
        image_folder = self.publish_image_folder_var.get().strip() or None

        if mode == "text_only" and not post_text:
            messagebox.showwarning("Missing Content", "Please enter post text before starting.")
            return

        # Image/folder validations
        image_paths = None
        if mode == "text_image":
            if img_src == "file":
                if not image_path:
                    messagebox.showwarning("Missing Image", "Please select an image file before starting.")
                    return
                if not Path(image_path).exists():
                    messagebox.showerror("File Not Found", f"Image file not found:\n{image_path}")
                    return
            else:
                if not image_folder:
                    messagebox.showwarning("Missing Folder", "Please select an image folder before starting.")
                    return
                if not Path(image_folder).exists():
                    messagebox.showerror("Folder Not Found", f"Image folder not found:\n{image_folder}")
                    return
                try:
                    image_paths = publish_posts_script.scan_image_folder(image_folder)
                    if not image_paths:
                        messagebox.showerror("No Images", f"No supported image files found in folder:\n{image_folder}")
                        return
                except Exception as e:
                    messagebox.showerror("Error Scanning Folder", f"Could not scan image folder:\n{e}")
                    return

        if mode == "text_image" and self.publish_text_enable_var.get() and not post_text:
            messagebox.showwarning("Missing Text", "Please enter post text or uncheck 'Add text to post'.")
            return
            
        if mode == "text_video" and self.publish_text_enable_var.get() and not post_text:
            messagebox.showwarning("Missing Text", "Please enter post text or uncheck 'Add text to post'.")
            return

        # Reset state
        self.publish_stop_event.clear()
        self.publish_pause_event.set()
        self.active_tool = "publish"

        # Update script settings
        publish_posts_script.CHROME_PROFILE_PATH = self.global_chrome_profile.get()
        publish_posts_script.REMOTE_DEBUGGING_PORT = self._get_active_profile_port()
        publish_posts_script.STOP_EVENT = self.publish_stop_event

        # UI state
        self._set_btn_state(self.btn_publish_start, "disabled")
        self._set_btn_state(self.btn_publish_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_publish_stop, "normal", C["error"], C["error_hover"])
        self.lbl_publish_status.configure(text="Running...")
        self.publish_progress.set(0)
        self.publish_progress.start()
        self._set_global_status("Running: Publish Posts", C["warning"])
        self._pulse_global_status()

        def _thread():
            with ThreadIOContext(
                lambda msg, style=None: self._queue_log("publish", msg, style),
                lambda: None,
                self.publish_input_gate,
                self.publish_input_value,
                "accent",
            ):
                try:
                    min_subs = None
                    if self.publish_min_subs_enable_var.get():
                        try:
                            min_subs = int(self.publish_min_subs_var.get().strip())
                        except ValueError:
                            min_subs = 0

                    results = publish_posts_script.run(
                        post_text=post_text,
                        image_path=image_path if mode == "text_image" and img_src == "file" else None,
                        image_paths=image_paths if mode == "text_image" and img_src == "folder" else None,
                        video_url=None,
                        attach_video=True if mode == "text_video" else False,
                        min_subscribers=min_subs,
                    )
                    success = sum(1 for r in results if r.get("status") == "success")
                    skipped = sum(1 for r in results if r.get("status") == "skipped")
                    failed = sum(1 for r in results if r.get("status") == "failed")
                    self._queue_log(
                        "publish",
                        f"✓ Finished — {success} succeeded, {skipped} skipped, {failed} failed.",
                        "success",
                    )
                    self.after(0, lambda: self.publish_progress.set(1.0))
                    self.after(0, lambda: self.lbl_publish_status.configure(text="Completed"))
                except Exception as e:
                    self._queue_log("publish", f"✗ Fatal error: {e}", "error")
                    self.after(0, lambda: self.lbl_publish_status.configure(text="Failed"))
                finally:
                    self.after(0, self.publish_progress.stop)
                    self.after(0, self._on_publish_done)

        threading.Thread(target=_thread, daemon=True).start()

    def _on_publish_done(self):
        self.active_tool = None
        publish_posts_script.STOP_EVENT = None
        self._set_btn_state(self.btn_publish_start, "normal", C["success"], C["success_hover"])
        self._set_btn_state(self.btn_publish_pause, "disabled")
        self._set_btn_state(self.btn_publish_resume, "disabled")
        self._set_btn_state(self.btn_publish_stop, "disabled")
        self._set_global_status("Status: Idle", C["success"])

    def _pause_publish(self):
        self.publish_pause_event.clear()
        self._set_btn_state(self.btn_publish_pause, "disabled")
        self._set_btn_state(self.btn_publish_resume, "normal", C["success"], C["success_hover"])
        self._queue_log("publish", "⏸ Process paused.", "warning")
        self.lbl_publish_status.configure(text="Paused...")

    def _resume_publish(self):
        self.publish_pause_event.set()
        self._set_btn_state(self.btn_publish_pause, "normal", C["warning"], C["warning_hover"])
        self._set_btn_state(self.btn_publish_resume, "disabled")
        self._queue_log("publish", "▶ Process resumed.", "success")
        self.lbl_publish_status.configure(text="Running...")

    def _stop_publish(self):
        self.publish_stop_event.set()
        self.publish_pause_event.set()  # Unblock if paused
        self.publish_input_gate.set()   # Unblock stdin
        self._queue_log("publish", "⏹ Stop command sent. Please wait...", "error")
        self._set_btn_state(self.btn_publish_stop, "disabled")
        self._set_btn_state(self.btn_publish_pause, "disabled")
        self._set_btn_state(self.btn_publish_resume, "disabled")
        self.lbl_publish_status.configure(text="Stopping...")


# ── Application Entry ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QaffDigitalProfessional()
    app.mainloop()

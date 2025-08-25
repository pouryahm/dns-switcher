#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DNS Switcher (Portable) – Windows

ویژگی‌ها:
- انتخاب از بین چند پروفایل DNS (IPv4/IPv6) و اعمال روی همه آداپتورهای فعال یا آداپتورهای انتخابی
- دکمه بازگردانی به حالت پیش‌فرض (DHCP)
- بدون وابستگی خارجی (فقط کتابخانه‌های استاندارد پایتون + PowerShell/Netsh ویندوز)
- قابلیت دریافت لیست پروفایل‌ها از فایل dns_profiles.json کنار برنامه (اختیاری)
- تشخیص عدم دسترسی ادمین و اجرای مجدد با UAC (در صورت نیاز)

نکات:
- برای تغییر DNS نیاز به دسترسی Administrator دارید.
- روی سیستم‌های سازمانی یا دارای Group Policy ممکن است تغییرات محدود شده باشد.

ساخت فایل exe پرتابل با PyInstaller:
    py -m pip install --upgrade pip
    py -m pip install pyinstaller
    py -m PyInstaller --noconsole --onefile dns_switcher.py

(اختیاری) اگر می‌خواهید همیشه با دسترسی ادمین اجرا شود می‌توانید از گزینه زیر استفاده کنید،
اما در این اسکریپت ارتقا دسترسی در زمان اعمال تغییرات انجام می‌شود:
    py -m PyInstaller --noconsole --onefile --uac-admin dns_switcher.py

"""

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

APP_NAME = "DNS Switcher"
HERE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))  # پشتیبانی از PyInstaller
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else HERE
PROFILE_FILE = APP_DIR / "dns_profiles.json"

# پروفایل‌های پیش‌فرض
DEFAULT_PROFILES = {
    "Cloudflare": {
        "ipv4": ["1.1.1.1", "1.0.0.1"],
        "ipv6": ["2606:4700:4700::1111", "2606:4700:4700::1001"],
    },
    "Google": {
        "ipv4": ["8.8.8.8", "8.8.4.4"],
        "ipv6": ["2001:4860:4860::8888", "2001:4860:4860::8844"],
    },
}


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def relaunch_as_admin():
    """Re-run current script with UAC elevation."""
    try:
        params = ' '.join([f'"{arg}"' for arg in sys.argv])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)
    except Exception as e:
        messagebox.showerror(APP_NAME, f"عدم موفقیت در ارتقا دسترسی به ادمین:\n{e}")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, shell=False)


def run_powershell(ps_code: str) -> subprocess.CompletedProcess:
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code])


def get_active_adapters() -> list[str]:
    """برگشت نام آداپتورهای فیزیکی فعال (Up)"""
    ps = (
        "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and -not $_.Virtual -and $_.HardwareInterface } "
        "| Select-Object -ExpandProperty Name"
    )
    p = run_powershell(ps)
    if p.returncode == 0:
        names = [line.strip() for line in p.stdout.splitlines() if line.strip()]
        if names:
            return names
    # تلاش جایگزین با netsh (در صورت عدم دسترسی به PowerShell cmdlets)
    p = run(["netsh", "interface", "show", "interface"])
    names = []
    if p.returncode == 0:
        for line in p.stdout.splitlines():
            # خروجی netsh معمولاً شامل: Admin State  State   Type     Interface Name
            if line.strip() and not line.startswith("Admin State") and not line.startswith("---"):
                parts = [x for x in line.split(" ") if x]
                # نام اینترفیس انتهای خط است
                if len(parts) >= 4:
                    state = parts[-3]
                    name = " ".join(parts[3:])  # امن‌تر برای نام‌های با فاصله
                    if state.lower() == "connected":
                        names.append(name)
    return names


def set_dns_servers(adapters: list[str], servers_v4: list[str] | None, servers_v6: list[str] | None) -> tuple[bool, str]:
    """اعمال DNS برای آداپتورها. ابتدا PowerShell، در صورت خطا netsh برای IPv4."""
    logs: list[str] = []
    all_ok = True

    if servers_v4 is None:
        servers_v4 = []
    if servers_v6 is None:
        servers_v6 = []

    # تلاش با PowerShell (بهترین روش – یکجا هر دو نسخه را ست می‌کند)
    try:
        for adapter in adapters:
            if servers_v4 or servers_v6:
                # Set-DnsClientServerAddress با هر دو خانواده کار می‌کند
                all_servers = servers_v4 + servers_v6
                ps = (
                    f"$ErrorActionPreference='Stop'; "
                    f"Set-DnsClientServerAddress -InterfaceAlias \"{adapter}\" -ServerAddresses @('{"','".join(all_servers)}')"
                )
            else:
                # اگر لیست خالی بود کاری نکنیم
                continue
            p = run_powershell(ps)
            if p.returncode != 0:
                all_ok = False
                logs.append(f"[PowerShell] {adapter}: خطا در تنظیم DNS\n{p.stderr.strip()}")
            else:
                logs.append(f"[PowerShell] {adapter}: DNS با موفقیت اعمال شد → {', '.join(all_servers)}")
    except Exception as e:
        all_ok = False
        logs.append(f"[PowerShell] خطای غیرمنتظره: {e}")

    # اگر PowerShell ناموفق بود، برای IPv4 از netsh استفاده کنیم
    if not all_ok and servers_v4:
        logs.append("تلاش با netsh برای IPv4...")
        for adapter in adapters:
            # ابتدا منبع را DHCP می‌کنیم تا لیست پاک شود
            p1 = run(["netsh", "interface", "ipv4", "set", "dnsservers", f"name={adapter}", "source=dhcp"])
            if p1.returncode != 0:
                logs.append(f"[netsh] {adapter}: خطا در تنظیم DHCP IPv4 → {p1.stderr.strip()}")
            # سپس Primary
            p2 = run(["netsh", "interface", "ipv4", "set", "dnsservers", f"name={adapter}", "static", servers_v4[0], "primary"])
            if p2.returncode != 0:
                logs.append(f"[netsh] {adapter}: خطا در تنظیم DNS اولیه → {p2.stderr.strip()}")
                continue
            # و بقیه
            for idx, ip in enumerate(servers_v4[1:], start=2):
                p3 = run(["netsh", "interface", "ipv4", "add", "dnsservers", f"name={adapter}", ip, f"index={idx}"])
                if p3.returncode != 0:
                    logs.append(f"[netsh] {adapter}: خطا در افزودن DNS شماره {idx} → {p3.stderr.strip()}")
            logs.append(f"[netsh] {adapter}: DNSهای IPv4 اعمال شد → {', '.join(servers_v4)}")

    return all_ok, "\n".join(logs)


def reset_dns(adapters: list[str]) -> tuple[bool, str]:
    """بازگردانی به حالت پیش‌فرض (DHCP) برای هر دو خانواده."""
    logs: list[str] = []
    all_ok = True

    # PowerShell – بهترین روش
    for adapter in adapters:
        ps = (
            f"$ErrorActionPreference='Stop'; "
            f"Set-DnsClientServerAddress -InterfaceAlias \"{adapter}\" -ResetServerAddresses"
        )
        p = run_powershell(ps)
        if p.returncode != 0:
            all_ok = False
            logs.append(f"[PowerShell] {adapter}: خطا در ریست DNS → {p.stderr.strip()}")
        else:
            logs.append(f"[PowerShell] {adapter}: DNS به حالت DHCP بازگردانی شد")

    # اگر PowerShell موفق نبود، لااقل IPv4 را با netsh به DHCP برگردانیم
    if not all_ok:
        logs.append("تلاش با netsh برای IPv4 (Reset)...")
        for adapter in adapters:
            p1 = run(["netsh", "interface", "ipv4", "set", "dnsservers", f"name={adapter}", "source=dhcp"])
            if p1.returncode != 0:
                logs.append(f"[netsh] {adapter}: خطا در DHCP IPv4 → {p1.stderr.strip()}")
            else:
                logs.append(f"[netsh] {adapter}: IPv4 به DHCP بازگردانی شد")
            # IPv6 ریست fallback استاندارد مطمئن netsh ندارد، برای جلوگیری از رفتار ناخواسته صرف‌نظر می‌کنیم.

    return all_ok, "\n".join(logs)


def load_profiles() -> dict:
    profiles = DEFAULT_PROFILES.copy()
    if PROFILE_FILE.exists():
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                user_profiles = json.load(f)
                # merge (کاربر اولویت دارد)
                for k, v in user_profiles.items():
                    ipv4 = v.get("ipv4", []) or []
                    ipv6 = v.get("ipv6", []) or []
                    profiles[k] = {"ipv4": ipv4, "ipv6": ipv6}
        except Exception as e:
            print(f"خطا در خواندن dns_profiles.json: {e}")
    return profiles


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("720x540")
        self.minsize(680, 520)

        self.profiles = load_profiles()
        self.adapters: list[str] = []

        self._build_ui()
        self.refresh_adapters()

        if not is_admin():
            self.log("برنامه بدون دسترسی ادمین اجرا شده است. برای اعمال DNS نیاز به ارتقا دسترسی دارید.")

    # ---------- UI ----------
    def _build_ui(self):
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        # Banner Admin
        self.admin_frame = ttk.Frame(container)
        self.admin_frame.pack(fill=tk.X)
        self.admin_label = ttk.Label(self.admin_frame, text="⚠️ دسترسی ادمین فعال نیست.")
        self.admin_btn = ttk.Button(self.admin_frame, text="اجرای مجدد با دسترسی ادمین", command=relaunch_as_admin)
        if is_admin():
            self.admin_frame.pack_forget()
        else:
            self.admin_label.pack(side=tk.LEFT)
            self.admin_btn.pack(side=tk.RIGHT)

        # Profiles
        prof_box = ttk.LabelFrame(container, text="پروفایل‌های DNS")
        prof_box.pack(fill=tk.X, pady=(8, 8))

        self.profile_var = tk.StringVar()
        profile_names = list(self.profiles.keys())
        if profile_names:
            self.profile_var.set(profile_names[0])
        self.profile_combo = ttk.Combobox(prof_box, values=profile_names, textvariable=self.profile_var, state="readonly")
        self.profile_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8), pady=8)

        ttk.Button(prof_box, text="باز کردن فایل پروفایل‌ها", command=self.open_profiles_file).pack(side=tk.RIGHT, padx=8, pady=8)

        # Adapters
        adp_box = ttk.LabelFrame(container, text="انتخاب آداپتور شبکه")
        adp_box.pack(fill=tk.BOTH, expand=False, pady=(0, 8))

        self.all_adapters_var = tk.BooleanVar(value=True)
        chk = ttk.Checkbutton(adp_box, text="اعمال روی همه آداپتورهای فعال", variable=self.all_adapters_var, command=self._toggle_adapter_list)
        chk.pack(anchor=tk.W, padx=8, pady=6)

        self.adapter_list = tk.Listbox(adp_box, selectmode=tk.EXTENDED, height=6)
        self.adapter_list.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.adapter_list.configure(state=tk.DISABLED)

        act_frame = ttk.Frame(adp_box)
        act_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Button(act_frame, text="به‌روزرسانی لیست آداپتورها", command=self.refresh_adapters).pack(side=tk.LEFT)

        # Actions
        buttons = ttk.Frame(container)
        buttons.pack(fill=tk.X, pady=(4, 8))
        ttk.Button(buttons, text="اعمال DNS انتخابی", command=self.apply_selected_profile).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="بازگردانی به حالت پیش‌فرض (DHCP)", command=self.reset_selected).pack(side=tk.LEFT)

        # Log
        log_box = ttk.LabelFrame(container, text="گزارش عملیات")
        log_box.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_box, height=12, wrap=tk.NONE)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.log_text.configure(state=tk.DISABLED)

    def _toggle_adapter_list(self):
        if self.all_adapters_var.get():
            self.adapter_list.configure(state=tk.DISABLED)
        else:
            self.adapter_list.configure(state=tk.NORMAL)

    def open_profiles_file(self):
        """ایجاد/بازکردن فایل dns_profiles.json برای ویرایش سریع."""
        if not PROFILE_FILE.exists():
            # نمونه اولیه بسازیم
            sample = {
                "MyOffice": {"ipv4": ["10.0.0.53", "10.0.0.54"], "ipv6": []},
                # مثال: DNS محلی/خصوصی
            }
            try:
                with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                    json.dump(sample, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror(APP_NAME, f"خطا در ساخت {PROFILE_FILE.name}:\n{e}")
                return
        try:
            os.startfile(str(PROFILE_FILE))
        except Exception as e:
            messagebox.showerror(APP_NAME, f"خطا در باز کردن فایل:\n{e}")

    def refresh_adapters(self):
        self.adapters = get_active_adapters()
        self.adapter_list.delete(0, tk.END)
        for name in self.adapters:
            self.adapter_list.insert(tk.END, name)
        self.log(f"آداپتورهای فعال: {', '.join(self.adapters) if self.adapters else '— هیچ —'}")

    def _selected_adapters(self) -> list[str]:
        if self.all_adapters_var.get() or not self.adapters:
            return self.adapters
        idxs = self.adapter_list.curselection()
        if not idxs:
            messagebox.showwarning(APP_NAME, "هیچ آداپتوری انتخاب نشده است.")
            return []
        return [self.adapter_list.get(i) for i in idxs]

    def apply_selected_profile(self):
        if not is_admin():
            if messagebox.askyesno(APP_NAME, "برای اعمال DNS نیاز به دسترسی ادمین است. الان ارتقا دسترسی انجام شود؟"):
                relaunch_as_admin()
            return
        name = self.profile_var.get()
        prof = self.profiles.get(name)
        if not prof:
            messagebox.showerror(APP_NAME, "پروفایل انتخابی یافت نشد.")
            return
        adapters = self._selected_adapters()
        if not adapters:
            return
        v4 = [ip for ip in (prof.get("ipv4") or []) if ip]
        v6 = [ip for ip in (prof.get("ipv6") or []) if ip]
        ok, log = set_dns_servers(adapters, v4, v6)
        self.log(f"اعمال پروفایل '{name}' روی: {', '.join(adapters)}\n{log}")
        if ok:
            messagebox.showinfo(APP_NAME, "DNS با موفقیت اعمال شد.")
        else:
            messagebox.showwarning(APP_NAME, "برخی عملیات با خطا مواجه شد. گزارش را بررسی کنید.")

    def reset_selected(self):
        if not is_admin():
            if messagebox.askyesno(APP_NAME, "برای ریست DNS به دسترسی ادمین نیاز است. ارتقا انجام شود؟"):
                relaunch_as_admin()
            return
        adapters = self._selected_adapters()
        if not adapters:
            return
        ok, log = reset_dns(adapters)
        self.log(f"بازگردانی DNS روی: {', '.join(adapters)}\n{log}")
        if ok:
            messagebox.showinfo(APP_NAME, "DNS به حالت پیش‌فرض بازگردانی شد.")
        else:
            messagebox.showwarning(APP_NAME, "برخی عملیات با خطا مواجه شد. گزارش را بررسی کنید.")

    def log(self, text: str):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {text}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)


if __name__ == '__main__':
    app = App()
    app.mainloop()

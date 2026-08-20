#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Sequence


APP_TITLE = "百工 Demo 安装器"
ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
REQUIREMENTS = ROOT / "requirements" / "requirements-data-action-demo.txt"
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run_capture(command: Sequence[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=CREATE_NO_WINDOW, check=False,
    )


def environment_report() -> dict:
    _, _, free = shutil.disk_usage(ROOT)
    report = {
        "python": sys.version.split()[0],
        "venv_exists": VENV_PYTHON.is_file(),
        "disk_free_gb": round(free / 1024**3, 1),
        "nvidia": shutil.which("nvidia-smi") is not None,
        "requirements_exists": REQUIREMENTS.is_file(),
        "config_template_count": len(list((ROOT / "config").rglob("*.example.yml"))),
    }
    if VENV_PYTHON.is_file():
        probe_code = """import json
try:
    import torch
    result = {
        'torch': torch.__version__,
        'cuda': torch.version.cuda,
        'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else '',
    }
except Exception as exc:
    result = {'error': str(exc)}
print(json.dumps(result, ensure_ascii=False))
"""
        probe = run_capture([
            str(VENV_PYTHON), "-c",
            probe_code,
        ])
        if probe.returncode == 0:
            report.update(json.loads(probe.stdout))
    return report


def nvidia_report() -> dict:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"available": False}
    summary = run_capture([
        executable,
        "--query-gpu=name,driver_version",
        "--format=csv,noheader",
    ])
    details = run_capture([executable])
    match = re.search(r"CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)", details.stdout)
    cuda_max = float(match.group(1)) if match else None
    return {
        "available": summary.returncode == 0 and details.returncode == 0,
        "gpu_driver": summary.stdout.strip(),
        "cuda_max": cuda_max,
        "cuda_126_compatible": cuda_max is not None and cuda_max >= 12.6,
    }


def install_exception_hook() -> None:
    def handler(exc_type, exc_value, exc_traceback) -> None:
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "installer_crash.log"
        log_path.write_text(
            "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
            encoding="utf-8",
        )
        try:
            from tkinter import messagebox
            messagebox.showerror(APP_TITLE, f"安装器启动失败。诊断日志：\n{log_path}")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = handler


def copy_configs(log) -> None:
    copied = 0
    for source in (ROOT / "config").rglob("*.example.yml"):
        destination = source.with_name(source.name.replace(".example.yml", ".yml"))
        if not destination.exists():
            shutil.copy2(source, destination)
            copied += 1
    log(f"活动配置：新复制 {copied} 个，已有配置未覆盖。\n")


class Installer:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("760x500")
        self.root.minsize(640, 420)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.log_dir = ROOT / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / f"installer_{datetime.now():%Y%m%d_%H%M%S}.log"

        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=APP_TITLE, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="自动创建虚拟环境、选择 GPU/CPU 路线、安装依赖并完成验证。\n安装 CUDA 版 PyTorch 时约需下载 2.6 GB。", justify="left").pack(anchor="w", pady=(4, 12))

        self.stage = tk.StringVar(value="等待开始")
        ttk.Label(frame, textvariable=self.stage).pack(anchor="w")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(4, 10))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(buttons, text="开始安装", command=self.start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="取消", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="复制日志", command=self.copy_log).pack(side="right")

        log_frame = ttk.Frame(frame)
        log_frame.pack(fill="both", expand=True)
        self.log_widget = tk.Text(log_frame, wrap="word", font=("Consolas", 10), state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log_widget.yview)
        self.log_widget.configure(yscrollcommand=scroll.set)
        self.log_widget.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.root.after(100, self.poll)

    def emit(self, kind: str, value: str) -> None:
        self.events.put((kind, value))

    def append_log(self, value: str) -> None:
        value = value.replace("\r", "\n")
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", value)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(value)

    def poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.append_log(value)
                elif kind == "stage":
                    self.stage.set(value)
                elif kind == "done":
                    self.progress.stop()
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.process = None
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def command(self, args: Sequence[str]) -> None:
        self.emit("log", "\n命令：" + subprocess.list2cmdline(list(args)) + "\n")
        self.process = subprocess.Popen(
            list(args), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=0,
            creationflags=CREATE_NO_WINDOW,
        )
        assert self.process.stdout is not None
        while True:
            chunk = self.process.stdout.read(256)
            if chunk:
                self.emit("log", chunk)
            if not chunk and self.process.poll() is not None:
                break
        code = self.process.wait()
        if code:
            raise RuntimeError(f"命令失败，退出码 {code}")

    def cuda_ready(self) -> bool:
        if not VENV_PYTHON.is_file():
            return False
        result = run_capture([
            str(VENV_PYTHON), "-c",
            "import torch,sys; sys.exit(0 if torch.__version__.startswith('2.9.0+cu126') and torch.cuda.is_available() else 1)",
        ])
        return result.returncode == 0

    def cpu_torch_ready(self) -> bool:
        if not VENV_PYTHON.is_file():
            return False
        result = run_capture([
            str(VENV_PYTHON), "-c",
            "import torch,sys; sys.exit(0 if torch.__version__.startswith('2.9.0') and torch.version.cuda is None else 1)",
        ])
        return result.returncode == 0

    def install(self) -> None:
        try:
            if not VENV_PYTHON.is_file():
                self.emit("stage", "1/5 创建虚拟环境")
                self.command([sys.executable, "-m", "venv", str(ROOT / ".venv")])
            else:
                self.emit("log", ".venv 已存在，继续使用。\n")

            gpu = nvidia_report()
            has_nvidia = bool(gpu.get("available"))
            if has_nvidia:
                self.emit("log", f"NVIDIA 检测：{json.dumps(gpu, ensure_ascii=False)}\n")
                if not gpu.get("cuda_126_compatible"):
                    raise RuntimeError(
                        "显卡驱动报告的最高 CUDA 兼容版本低于 12.6，或无法可靠读取；"
                        "为避免下载错误构建，安装器已停止。请先更新驱动或使用命令行 CPU 路线。"
                    )
            if has_nvidia and not self.cuda_ready():
                self.emit("stage", "2/5 下载并安装 CUDA 12.6 PyTorch")
                self.command([
                    str(VENV_PYTHON), "-m", "pip", "install", "--force-reinstall", "--no-deps",
                    "--timeout", "600", "--retries", "10", "--progress-bar", "on",
                    "torch==2.9.0", "--index-url", TORCH_INDEX,
                ])
            elif has_nvidia:
                self.emit("log", "CUDA 12.6 PyTorch 已就绪，跳过重复下载。\n")
            elif not self.cpu_torch_ready():
                self.emit("stage", "2/5 下载并安装 CPU PyTorch")
                self.command([
                    str(VENV_PYTHON), "-m", "pip", "install", "--force-reinstall", "--no-deps",
                    "--timeout", "600", "--retries", "10", "--progress-bar", "on",
                    "torch==2.9.0", "--index-url", TORCH_CPU_INDEX,
                ])
            else:
                self.emit("log", "CPU PyTorch 2.9.0 已就绪，跳过重复下载。\n")

            self.emit("stage", "3/5 安装其余依赖")
            self.command([
                str(VENV_PYTHON), "-m", "pip", "install", "--timeout", "300", "--retries", "5",
                "--progress-bar", "on", "-r", str(REQUIREMENTS),
            ])

            self.emit("stage", "4/5 准备活动配置")
            copy_configs(lambda value: self.emit("log", value))

            self.emit("stage", "5/5 验证安装")
            self.command([
                str(VENV_PYTHON), "-c",
                "import torch,yaml,duckdb,chromadb,sentence_transformers; print('torch:',torch.__version__); print('CUDA:',torch.version.cuda); print('GPU可用:',torch.cuda.is_available()); print('GPU:',torch.cuda.get_device_name(0) if torch.cuda.is_available() else '不可用'); print('依赖导入：通过')",
            ])
            self.emit("stage", "安装完成")
            self.emit("log", f"\n安装完成。完整日志：{self.log_path}\n")
        except Exception as exc:
            self.emit("stage", "安装失败，请复制日志")
            self.emit("log", f"\n错误：{exc}\n{traceback.format_exc()}\n")
        finally:
            self.emit("done", "")

    def start(self) -> None:
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.start(10)
        self.append_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 开始安装\n")
        threading.Thread(target=self.install, daemon=True).start()

    def cancel(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.append_log("\n正在取消当前安装步骤…\n")
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()

    def copy_log(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_widget.get("1.0", "end-1c"))

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        report = environment_report()
        report["nvidia_details"] = nvidia_report()
        report["self_test_ok"] = bool(
            report["requirements_exists"] and report["config_template_count"] > 0
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["self_test_ok"] else 1
    install_exception_hook()
    Installer().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

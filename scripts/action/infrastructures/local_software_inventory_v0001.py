# ============================================================
# 文件名: local_software_inventory_v0001.py
# 中文名: 本机软件与 Agent 只读发现
# 版本号: v0001
# 主层级: action
# 层级: workbench / integration
# 脚本定位: 本机软件与 Agent 只读发现
# 职责说明: 通过已有索引和显式 JSON 契约提供真实信息
# 本脚本做什么: 本机软件与 Agent 只读发现
# 本脚本不做什么: 不自动授权模型、不安装软件、不卸载系统应用
# 制度边界声明: 只在明确操作后执行；用户数据保留；错误可追溯
# 可更新: True
# ============================================================
# ALIAS_META
# alias: local_software_inventory_v0001
# family: local_software_inventory
# role: managed_integration
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/local_software_inventory_v0001.py
# input: authenticated explicit operation or JSON request
# output: factual inventory or versioned result
# depends_on: standard library and existing managed APIs
# used_by: workbench_server_v0011
# ============================================================

from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_NAME = "local_software_inventory_v0001"
SCRIPT_FAMILY = "local_software_inventory"
SCRIPT_VERSION = "v0001"
DEFAULT_ENCODING = "utf-8"

AGENTS = {"codex": "Codex", "claude": "Claude", "gemini": "Gemini CLI", "opencode": "OpenCode",
          "aider": "Aider", "goose": "Goose", "cursor": "Cursor", "windsurf": "Windsurf"}
BLOCKED = {"cmd.exe", "powershell.exe", "pwsh.exe", "python.exe", "pythonw.exe", "node.exe",
           "wscript.exe", "cscript.exe", "mshta.exe", "rundll32.exe", "regsvr32.exe",
           "explorer.exe", "msiexec.exe"}

def entry(raw):
    raw = os.path.expandvars(str(raw or "")).strip()
    match = re.match(r'^"([^"]+\.exe)"', raw, re.I) or re.match(r"^(.+?\.exe)(?:,\s*-?\d+)?$", raw, re.I)
    candidate = match.group(1) if match else raw
    file = Path(candidate)
    if file.is_absolute() and file.suffix.lower() == ".exe" and file.is_file():
        if file.name.lower() == "blender-launcher.exe" and file.with_name("blender.exe").is_file():
            file = file.with_name("blender.exe")
        return str(file.resolve())
    return None

def scan():
    records = []
    errors = []
    def add(name, executable=None, source="", version="", location="", evidence=""):
        if not name:
            return
        exe = entry(executable)
        key = (exe or (str(name) + "|" + str(location))).casefold()
        identifier = "software." + hashlib.sha256(key.encode()).hexdigest()[:24]
        agent = next((label for word, label in AGENTS.items() if re.search(r"(?<![a-z])" + word + r"(?![a-z])", (str(name) + (" " + str(evidence) if source == "Windows 应用登记" else "")).lower())), None)
        blocked = exe and (Path(exe).name.lower() in BLOCKED or re.search(r"unins|uninstall|setup|update", Path(exe).name, re.I))
        records.append({"id": identifier, "name": str(name), "path": exe, "version": str(version or ""),
                        "location": str(location or ""), "source": source, "evidence": str(evidence),
                        "launchable": bool(exe and not blocked), "adapter": "blender" if exe and Path(exe).name.lower() == "blender.exe" else "native",
                        "agent_name": agent, "status": "installed_entry" if exe else "registered_unresolved"})
    if os.name == "nt":
        import winreg
        roots = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY, "HKLM64"),
                 (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY, "HKLM32"),
                 (winreg.HKEY_CURRENT_USER, 0, "HKCU")]
        for hive, flag, label in roots:
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | flag) as parent:
                    for i in range(winreg.QueryInfoKey(parent)[0]):
                        sub = winreg.EnumKey(parent, i)
                        try:
                            with winreg.OpenKey(parent, sub) as key:
                                def val(name):
                                    try:
                                        return winreg.QueryValueEx(key, name)[0]
                                    except OSError:
                                        return ""
                                if val("SystemComponent") == 1:
                                    continue
                                add(val("DisplayName"), val("DisplayIcon"), label, val("DisplayVersion"), val("InstallLocation"), "Windows 安装登记")
                        except OSError as exc:
                            errors.append({"source": label, "error": type(exc).__name__})
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append({"source": label, "error": type(exc).__name__})
        # Fixed PowerShell program: no user text interpolation, no launch, no console.
        script = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$ErrorActionPreference = 'Stop'
$roots = @([Environment]::GetFolderPath('CommonPrograms'), [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('CommonDesktopDirectory'))
$ws = New-Object -ComObject WScript.Shell
$rows = @(foreach ($dir in $roots) {
 if (Test-Path -LiteralPath $dir) {
  foreach ($file in (Get-ChildItem -LiteralPath $dir -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 4000)) {
   try { $lnk=$ws.CreateShortcut($file.FullName); [PSCustomObject]@{name=$file.BaseName;target=$lnk.TargetPath;evidence=$file.FullName;hasArguments=([bool]$lnk.Arguments)} } catch {}
  }
 }
})
[PSCustomObject]@{shortcuts=$rows;apps=@(Get-StartApps -ErrorAction SilentlyContinue | Select-Object Name,AppID)} | ConvertTo-Json -Compress -Depth 4
"""
        try:
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            result = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                                    capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                errors.append({"source": "shortcuts", "error": "shortcut_scan_failed"})
            else:
                payload = json.loads(result.stdout.decode("utf-8-sig") or "{}")
                for app in payload.get("apps", []):
                    if "!" in app.get("AppID", ""):
                        add(app["Name"], source="Windows 应用登记", evidence=app["AppID"])
                for row in payload.get("shortcuts", []):
                    add(row["name"], row["target"], "Windows 快捷方式", evidence=row["evidence"])
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append({"source": "shortcuts", "error": type(exc).__name__})
    agents = []
    for command, label in AGENTS.items():
        executable = shutil.which(command)
        if executable:
            agents.append({"id": "local-agent." + hashlib.sha256(executable.casefold().encode()).hexdigest()[:24],
                           "name": label, "kind": "CLI", "path": executable, "source": "PATH",
                           "status": "detected_unverified", "evidence": "可执行入口存在；未运行、未验证登录或模型权限"})
    for folder in (Path.home()/".vscode/extensions", Path.home()/".cursor/extensions", Path.home()/".windsurf/extensions"):
        if not folder.is_dir():
            continue
        obsolete = {}
        try:
            obsolete = json.loads((folder/".obsolete").read_text(encoding="utf-8")) if (folder/".obsolete").is_file() else {}
        except (ValueError, OSError):
            errors.append({"source": str(folder), "error": "obsolete_metadata_unavailable"})
        for package in folder.glob("*/package.json"):
            if package.parent.name.startswith(".") or obsolete.get(package.parent.name):
                continue
            try:
                if package.stat().st_size > 2000000:
                    continue
                meta = json.loads(package.read_text(encoding="utf-8"))
                identity = str(meta.get("publisher", "")) + "." + str(meta.get("name", ""))
                if not re.search(r"(^saoudrizwan\.claude-dev$|roo-cline|^continue\.continue$|claude-code|^openai\.chatgpt$|copilot-chat)", identity, re.I):
                    continue
                agents.append({"id": "local-agent."+hashlib.sha256(str(package).casefold().encode()).hexdigest()[:24],
                               "name": meta.get("displayName", identity), "kind": "编辑器扩展", "path": str(package.parent),
                               "version": meta.get("version", ""), "source": folder.parent.name,
                               "status": "detected_unverified", "evidence": identity+"；已安装不代表已启用或可调用"})
            except (OSError, ValueError):
                errors.append({"source": str(package), "error": "extension_metadata_unavailable"})
    for row in records:
        if row["path"] or not row["location"]:
            continue
        directory = Path(os.path.expandvars(row["location"]))
        matches = [other for other in records if other["path"] and Path(other["path"]).parent == directory
                   and (other["name"].casefold().startswith(row["name"].casefold()) or row["name"].casefold().startswith(other["name"].casefold()))]
        if len({other["path"].casefold() for other in matches}) == 1:
            other = matches[0]
            for key in ("id", "path", "launchable", "adapter", "status"):
                row[key] = other[key]
    merged = {}
    for row in records:
        existing = merged.get(row["id"])
        if existing:
            existing["sources"] = sorted(set(existing["sources"] + [row["source"]]))
            if not existing["version"]:
                existing["version"] = row["version"]
        else:
            merged[row["id"]] = {**row, "sources": [row["source"]]}
    records = sorted(merged.values(), key=lambda row: row["name"].casefold())
    for row in records:
        if row["agent_name"]:
            agents.append({"id": "local-agent."+row["id"], "name": row["name"], "kind": "桌面应用",
                           "path": row["path"] or row["location"], "version": row["version"], "source": " / ".join(row["sources"]),
                           "status": "detected_unverified", "evidence": row["evidence"]+"；应用登记，不代表 Agent 运行"})
    return {"items": records, "agents": agents, "errors": errors, "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "安装登记、开始菜单和桌面快捷方式、PATH Agent CLI、VS Code/Cursor/Windsurf Agent 扩展；未扫描整个磁盘"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    raw = args.request.read_bytes()
    output = {"schema_version": "workbench.software-inventory.v1", "source_sha256": hashlib.sha256(raw).hexdigest(),
              "producer": {"script": SCRIPT_NAME, "version": SCRIPT_VERSION}}
    code = 0
    try:
        req = json.loads(raw)
        output["run_id"] = req.get("run_id")
        if len(raw) > 16384 or req.get("schema_version") != output["schema_version"] or req.get("operation") != "scan" or req.get("producer") != {"script": "workbench_software_v0001", "version": "v0001"}:
            raise ValueError("scan_contract_invalid")
        output.update(status="completed", result=scan())
    except Exception as exc:
        code = 2
        output.update(status="failed", error=type(exc).__name__)
    args.response.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.response.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False), encoding=DEFAULT_ENCODING)
    tmp.replace(args.response)
    return code

if __name__ == "__main__":
    raise SystemExit(main())

# ============================================================
# 文件名: owned_process_v0001.py
# 中文名: 受拥有者生命周期约束的子进程
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / process
# 脚本定位: 防止编排中断后遗留写库孙进程
# 职责说明: Windows Job Object 在拥有者退出时终止整个子树
# 本脚本做什么: 暂停启动、加入作业、恢复执行；超时先终止子树
# 本脚本不做什么: 不访问业务数据，不终止无关进程
# 制度边界声明: 无法建立进程所有权时拒绝执行
# 可更新: True
# ALIAS_META
# alias: owned_process_v0001
# family: owned_process
# role: process_tree_lifetime
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/owned_process_v0001.py
# input: explicit child command
# output: subprocess CompletedProcess
# depends_on: Python stdlib, Windows Job API
# used_by: ingestion_jobs_v0001, data_action_chain_pipeline_v0020
# ============================================================
import ctypes
import os
import subprocess
from ctypes import wintypes

DEFAULT_ENCODING='utf-8'
SCRIPT_FAMILY='owned_process'
SCRIPT_NAME='owned_process_v0001.py'
SCRIPT_VERSION='v0001'


def run(command, *, capture_output=False, timeout=None, check=False, **kwargs):
    if os.name!='nt':
        raise RuntimeError('Owned ingestion process tree currently requires Windows')
    class Basic(ctypes.Structure):
        _fields_=[('per_process',ctypes.c_longlong),('per_job',ctypes.c_longlong),('flags',wintypes.DWORD),
                  ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',wintypes.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                  ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
    job=kernel.CreateJobObjectW(None,None)
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    limits=Extended();limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process=None
    try:
        if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if capture_output:kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        kwargs['creationflags']=kwargs.get('creationflags',0)|subprocess.CREATE_NO_WINDOW|0x4
        process=subprocess.Popen(command,**kwargs)
        if not kernel.AssignProcessToJobObject(job,wintypes.HANDLE(int(process._handle))):
            process.kill();process.wait()
            raise ctypes.WinError(ctypes.get_last_error())
        ntdll=ctypes.WinDLL('ntdll')
        ntdll.NtResumeProcess.argtypes=[wintypes.HANDLE]
        if ntdll.NtResumeProcess(wintypes.HANDLE(int(process._handle)))!=0:
            raise RuntimeError('Unable to resume owned process')
        try:
            stdout,stderr=process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kernel.TerminateJobObject(job,124)
            stdout,stderr=process.communicate()
            raise subprocess.TimeoutExpired(command,timeout,output=stdout,stderr=stderr)
        result=subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
        measured=Extended()
        if kernel.QueryInformationJobObject(job,9,ctypes.byref(measured),ctypes.sizeof(measured),None):
            result.resource_usage={'peak_job_committed_bytes':measured.peak_job,
                                   'peak_process_committed_bytes':measured.peak_process,
                                   'read_bytes':measured.io.read_bytes,'write_bytes':measured.io.write_bytes}
        if check:result.check_returncode()
        return result
    finally:
        kernel.CloseHandle(job)
        if process is not None and process.poll() is None:process.wait()

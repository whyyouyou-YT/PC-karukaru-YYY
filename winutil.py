"""Windows API まわりの薄いラッパー。

ごみ箱の照会・削除、管理者権限の判定と昇格再起動、ドライブ空き容量の取得を扱う。
外部ライブラリには依存せず ctypes だけで完結させる（send2trash 等を入れずに済ませる）。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

# --- ごみ箱 ---------------------------------------------------------------

SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


@dataclass
class RecycleBinInfo:
    size: int
    items: int


def query_recycle_bin() -> RecycleBinInfo:
    """全ドライブのごみ箱の合計サイズと項目数を返す。"""
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    # 第1引数 None = 全ドライブ合計
    res = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    if res != 0:
        return RecycleBinInfo(0, 0)
    return RecycleBinInfo(int(info.i64Size), int(info.i64NumItems))


def empty_recycle_bin() -> bool:
    """ごみ箱を空にする（確認ダイアログ・音・進捗UIなし）。"""
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    # S_OK(0) 以外に「既に空」で 0x8000FFFF(E_UNEXPECTED) が返ることがある
    return res in (0, -2147418113)


# --- ごみ箱へ送る（SHFileOperationW） -------------------------------------

FO_DELETE = 0x0003
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_NOERRORUI = 0x0400
FOF_SILENT = 0x0004
FOF_NOCONFIRMMKDIR = 0x0200


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def send_to_recycle_bin(paths: list[str]) -> bool:
    """指定パス群をごみ箱へ送る（復元可能）。ユーザーの実ファイルを消すとき専用。"""
    if not paths:
        return True
    # pFrom はダブルNUL終端のリスト
    buf = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = buf
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT | FOF_NOCONFIRMMKDIR
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return res == 0 and not op.fAnyOperationsAborted


# --- 管理者権限 -----------------------------------------------------------


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """自身を管理者権限で起動し直す。成功したら True（呼び出し元は終了すること）。"""
    if is_admin():
        return False
    params = " ".join(f'"{a}"' for a in sys.argv)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
    except Exception:
        return False
    return int(rc) > 32


# --- 実行中プロセス -------------------------------------------------------

TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


def running_processes() -> set[str]:
    """実行中プロセスの実行ファイル名（小文字）の集合を返す。"""
    names: set[str] = set()
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return names
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return names
        while True:
            names.add(entry.szExeFile.lower())
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


# --- 使用中ファイルの判定（Restart Manager） -------------------------------

CCH_RM_SESSION_KEY = 32
ERROR_MORE_DATA = 234


def files_in_use(paths: list[str]) -> bool:
    """指定パス群のいずれかを、他プロセスが開いているか（Restart Manager経由）。

    Windows が「このファイルは使用中です」ダイアログを出すのと同じ仕組み。
    ファイルを実際に開き直す方式と違い、共有モードにかかわらずハンドルを
    持つプロセスを検出できる（読み取り専用共有で掴まれているだけのケースも拾える）。
    セッション開始・登録に失敗した場合は判定不能として True（使用中扱い）を返す。
    """
    if not paths:
        return False
    rstrtmgr = ctypes.windll.rstrtmgr
    session = wintypes.DWORD()
    session_key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    if rstrtmgr.RmStartSession(ctypes.byref(session), 0, session_key) != 0:
        return True
    try:
        names = (ctypes.c_wchar_p * len(paths))(*paths)
        if rstrtmgr.RmRegisterResources(session, len(paths), names, 0, None, 0, None) != 0:
            return True

        needed = wintypes.UINT(0)
        have = wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        res = rstrtmgr.RmGetList(
            session, ctypes.byref(needed), ctypes.byref(have), None, ctypes.byref(reasons)
        )
        if res == 0:  # ERROR_SUCCESS: 使用中プロセスなし
            return False
        if res == ERROR_MORE_DATA:  # 使用中プロセスあり（件数はneededに入る）
            return needed.value > 0
        return True  # 想定外のエラーは安全側に倒す
    finally:
        rstrtmgr.RmEndSession(session)


# --- ドライブ空き容量 -----------------------------------------------------


def disk_usage(path: str = "C:\\") -> tuple[int, int]:
    """(空き, 総容量) をバイトで返す。取得できなければ (0, 0)。"""
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(path), ctypes.byref(free), ctypes.byref(total), None
    )
    if not ok:
        return (0, 0)
    return (free.value, total.value)

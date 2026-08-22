"""Temp Cleaner — Windows の一時ファイル・キャッシュ掃除ツール。

pywebview のネイティブウィンドウ + ローカル HTML の構成。HTTP サーバーは立てず、
JS からは `pywebview.api.*` で Python を直接呼ぶ。常駐せず、ウィンドウを閉じれば
プロセスも終わる。

起動: python app.py  （管理者権限で消したいときは --admin か UI のボタンから）

注意: js_api に渡すオブジェクトの属性は pywebview が再帰的に走査する。
Window オブジェクト等を Api の属性に持たせると無限再帰でクラッシュするため、
アプリの状態は Api の外（モジュールレベルの _S）に置くこと。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import webview

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cleaner import clean_target  # noqa: E402
from scanner import CategoryResult, scan_all  # noqa: E402
from targets import Target, build_targets  # noqa: E402
from winutil import (  # noqa: E402
    disk_usage,
    is_admin,
    relaunch_as_admin,
    running_processes,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE_DIR, "static", "index.html")
MAX_DETAIL_ITEMS = 30
SYSTEM_DRIVE = os.environ.get("SYSTEMDRIVE", "C:")


class _State:
    """アプリの状態。Api の外に置く（上の注意書き参照）。"""

    def __init__(self) -> None:
        self.window = None
        self.targets: list[Target] = []
        self.scans: dict[str, CategoryResult] = {}
        self.processes: set[str] = set()
        self.lock = threading.Lock()
        self.busy = False


_S = _State()


def _target_payload(target: Target) -> dict:
    return {
        "key": target.key,
        "label": target.label,
        "detail": target.detail,
        "risk": target.risk,
        "defaultOn": target.default_on,
        "needsAdmin": target.needs_admin,
        "toTrash": target.to_trash,
        "roots": [r.path for r in target.roots],
        "checksLocks": target.check_locks,
        # 起動中だと消し損ねる・壊しうるアプリ（実行中のものだけを返す）
        "conflicts": _running_conflicts(target),
    }


def _running_conflicts(target: Target) -> list[str]:
    if not target.conflict_processes:
        return []
    running = _S.processes
    return [p for p in target.conflict_processes if p.lower() in running]


def _result_payload(res: CategoryResult) -> dict:
    return {
        "key": res.key,
        "size": res.size,
        "files": res.files,
        "denied": res.denied,
        "skippedRecent": res.skipped_recent,
        "skippedRecentSize": res.skipped_recent_size,
        "error": res.error,
        "items": [
            {"path": i.path, "size": i.size, "mtime": i.mtime, "isDir": i.is_dir}
            for i in res.items[:MAX_DETAIL_ITEMS]
        ],
        "itemsTotal": len(res.items),
    }


def _emit(event: str, data: dict) -> None:
    """UI にイベントを送る。"""
    if _S.window is None:
        return
    try:
        _S.window.evaluate_js(
            f"window.onPyEvent({json.dumps(event)}, {json.dumps(data, ensure_ascii=False)})"
        )
    except Exception:
        pass


def _disk() -> dict:
    free, total = disk_usage(SYSTEM_DRIVE + "\\")
    return {"free": free, "total": total, "drive": SYSTEM_DRIVE}


def _acquire() -> bool:
    with _S.lock:
        if _S.busy:
            return False
        _S.busy = True
    return True


def _release() -> None:
    with _S.lock:
        _S.busy = False


def _scan_worker() -> None:
    started = time.time()
    _S.processes = running_processes()

    def on_result(res: CategoryResult) -> None:
        # 終わったカテゴリから即 UI に出す（全体の完了を待たせない）
        _S.scans[res.key] = res
        _emit("scanned", _result_payload(res))

    try:
        scan_all(_S.targets, on_result=on_result)
        _emit(
            "scanDone",
            {
                "elapsed": round(time.time() - started, 2),
                "disk": _disk(),
                "conflicts": {t.key: _running_conflicts(t) for t in _S.targets},
            },
        )
    finally:
        _release()


def _clean_worker(keys: list[str]) -> None:
    by_key = {t.key: t for t in _S.targets}
    try:
        for key in keys:
            target = by_key.get(key)
            scan = _S.scans.get(key)
            if not target or not scan:
                continue
            _emit("cleanStart", {"key": key, "label": target.label})
            res = clean_target(
                target,
                scan,
                on_progress=lambda k, done, total: _emit(
                    "cleanProgress", {"key": k, "done": done, "total": total}
                ),
            )
            _S.scans[key] = CategoryResult(key=key)
            _emit(
                "cleaned",
                {
                    "key": key,
                    "freed": res.freed,
                    "deleted": res.deleted,
                    "skipped": res.skipped,
                    "locked": res.locked,
                    "errors": res.errors,
                },
            )
        _emit("cleanDone", {"disk": _disk()})
    finally:
        _release()


class Api:
    """JS から呼ばれる窓口。状態は持たないこと。"""

    def bootstrap(self) -> dict:
        """UI 初期化用の情報を返し、そのままスキャンを開始する。"""
        _S.targets = build_targets()
        _S.processes = running_processes()
        payload = {
            "isAdmin": is_admin(),
            "disk": _disk(),
            "targets": [_target_payload(t) for t in _S.targets],
        }
        self.start_scan()
        return payload

    def start_scan(self) -> bool:
        """バックグラウンドでスキャンを開始する。"""
        if not _acquire():
            return False
        threading.Thread(target=_scan_worker, daemon=True).start()
        return True

    def clean(self, keys: list) -> bool:
        """選択されたカテゴリの削除を開始する。"""
        if not _acquire():
            return False
        threading.Thread(target=_clean_worker, args=(list(keys),), daemon=True).start()
        return True

    def open_path(self, path: str) -> bool:
        """エクスプローラーで対象を開く（消す前に中身を確かめたいとき用）。"""
        if not os.path.exists(path):
            return False
        try:
            if os.path.isdir(path):
                subprocess.Popen(["explorer.exe", os.path.normpath(path)])
            else:
                subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
        except OSError:
            return False
        return True

    def request_admin(self) -> bool:
        """管理者権限で起動し直す。"""
        if is_admin():
            return False
        if not relaunch_as_admin():
            return False
        threading.Timer(0.4, lambda: os._exit(0)).start()
        return True


def main() -> None:
    if "--admin" in sys.argv and not is_admin():
        if relaunch_as_admin():
            return

    window = webview.create_window(
        "Temp Cleaner" + ("（管理者）" if is_admin() else ""),
        INDEX_HTML,
        js_api=Api(),
        width=980,
        height=760,
        min_size=(720, 560),
        background_color="#16171a",
    )
    _S.window = window
    webview.start()


if __name__ == "__main__":
    main()

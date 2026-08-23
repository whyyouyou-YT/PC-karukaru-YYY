"""ユーザー設定（前回の削除選択・表示テーマ）の永続化。

%LOCALAPPDATA%\\PC-karukaru-YYY\\settings.json に保存する。インストール先
（Program Files）は書き込み権限がない場合があるため使わない。

pywebview の js_api 呼び出しはスレッド化されうるため、保存はロックで直列化し
一時ファイル経由の置き換え（os.replace）で書き込み途中のファイルが読まれない
ようにする。
"""
from __future__ import annotations

import json
import os
import threading

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local")
SETTINGS_DIR = os.path.join(LOCALAPPDATA, "PC-karukaru-YYY")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

THEMES = ("dark", "light")

_lock = threading.Lock()


def _load_raw() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_patch(patch: dict) -> bool:
    """既存の設定を読み込んでpatchをマージしてから書き戻す。成功したかを返す。"""
    tmp_path = SETTINGS_PATH + ".tmp"
    try:
        with _lock:
            data = _load_raw()
            data.update(patch)
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, SETTINGS_PATH)
        return True
    except OSError:
        return False


def load_checked() -> dict[str, bool]:
    """前回保存されたチェック状態（key -> bool）を返す。無ければ空dict。"""
    checked = _load_raw().get("checked")
    if not isinstance(checked, dict):
        return {}
    return {str(k): bool(v) for k, v in checked.items()}


def save_checked(checked: dict) -> bool:
    """チェック状態を保存する。成功したかどうかを返す（呼び出し元は落とさない）。"""
    return _save_patch({"checked": checked})


def load_theme() -> str:
    """前回起動時のテーマ（"dark" / "light"）を返す。無ければ既定の "dark"。"""
    theme = _load_raw().get("theme")
    return theme if theme in THEMES else "dark"


def save_theme(theme: str) -> bool:
    """テーマを保存する。成功したかどうかを返す。"""
    if theme not in THEMES:
        return False
    return _save_patch({"theme": theme})

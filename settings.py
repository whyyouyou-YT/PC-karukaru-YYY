"""ユーザー設定（前回の削除選択）の永続化。

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

_lock = threading.Lock()


def load_checked() -> dict[str, bool]:
    """前回保存されたチェック状態（key -> bool）を返す。無ければ空dict。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    checked = data.get("checked")
    if not isinstance(checked, dict):
        return {}
    return {str(k): bool(v) for k, v in checked.items()}


def save_checked(checked: dict) -> bool:
    """チェック状態を保存する。成功したかどうかを返す（呼び出し元は落とさない）。"""
    tmp_path = SETTINGS_PATH + ".tmp"
    try:
        with _lock:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"checked": checked}, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, SETTINGS_PATH)
        return True
    except OSError:
        return False

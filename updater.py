"""PC-karukaru-YYY 用の軽量アップデーター。

既にインストール済みの PC-karukaru-YYY.exe を GitHub の最新 Release に置き換える。
フルインストーラー（約20MB）を落とし直さず、本体exe（単体exe版と同じもの）だけを
ダウンロードして差し替える。pywebview 等の重い依存は一切使わず標準ライブラリのみ
で完結させ、単体で数MB程度の小さなexeとしてビルドできるようにしている。

使い方: このexeをそのまま実行する。インストール先はレジストリの
アンインストール情報（Inno Setup が書き込む）から自動検出する。見つからない
場合は、このexeを PC-karukaru-YYY.exe と同じフォルダに置いて再実行すること。

前提: 更新対象の PC-karukaru-YYY.exe は起動していないこと（実行中だと
ファイルがロックされていて置き換えできない）。起動中を検出した場合は
アップデーターは何もせず終了する（強制終了はしない）。

リポジトリが Private の間のアクセスについて:
GitHub の Releases API は匿名アクセスでは Private リポジトリを見られない
（404になる）。開発者（yuuma）から読み取り専用スコープの Fine-grained
Personal Access Token を発行してもらい、このexeと同じフォルダに
token.txt という名前でトークンだけを1行貼り付けて保存すること
（環境変数 PC_KARUKARU_UPDATER_TOKEN でも可）。リポジトリが将来 Public に
なれば token.txt は不要になる（無くても動く）。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import winreg

REPO = "whyyouyou-YT/PC-karukaru-YYY"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
EXE_NAME = "PC-karukaru-YYY.exe"
UNINSTALL_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{6F2E8C1A-9D4B-4E7A-8F3C-2B6A1D9E5C4F}_is1"
USER_AGENT = "PC-karukaru-YYY-Updater"
TOKEN_ENV_VAR = "PC_KARUKARU_UPDATER_TOKEN"


def _pause(msg: str = "") -> None:
    if msg:
        print(msg)
    try:
        input("\n終了するには Enter キーを押してください...")
    except EOFError:
        pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """自身を管理者権限で起動し直す。成功したら True（呼び出し元は終了すること）。"""
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
    except Exception:
        return False
    return int(rc) > 32


def find_install_dir() -> str | None:
    """レジストリのアンインストール情報からインストール先を探す。"""
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for wow in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL_SUBKEY, 0, winreg.KEY_READ | wow) as key:
                    path, _ = winreg.QueryValueEx(key, "InstallLocation")
                    if path and os.path.isdir(path):
                        return path
            except OSError:
                continue
    # フォールバック: このexeと同じフォルダに本体があればそこを使う（単体exe版の隣に置いた場合）
    here = _here_dir()
    if os.path.isfile(os.path.join(here, EXE_NAME)):
        return here
    return None


def _here_dir() -> str:
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def is_running(exe_path: str) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {os.path.basename(exe_path)}"],
            capture_output=True, text=True, timeout=10,
        )
        return os.path.basename(exe_path).lower() in out.stdout.lower()
    except OSError:
        return False


# --- Private リポジトリ向けトークン認証 -----------------------------------


def _load_token() -> str | None:
    env = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if env:
        return env
    token_path = os.path.join(_here_dir(), "token.txt")
    try:
        with open(token_path, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        return None
    return token or None


def _headers(accept: str) -> dict:
    h = {"User-Agent": USER_AGENT, "Accept": accept}
    token = _load_token()
    if token:
        h["Authorization"] = f"token {token}"
    return h


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """リダイレクト先（GitHub の署名付きS3 URL等）にAuthorizationヘッダを持ち越さない。

    そのまま持ち越すとS3側が「認証方式が重複している」として拒否することがあるため、
    GitHub公式ドキュメントの推奨に合わせて元のホスト以外へのリダイレクトでは外す。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and "Authorization" in new_req.headers:
            del new_req.headers["Authorization"]
        return new_req


_opener = urllib.request.build_opener(_StripAuthOnRedirect)


def fetch_latest() -> dict:
    req = urllib.request.Request(API_LATEST, headers=_headers("application/vnd.github+json"))
    with _opener.open(req, timeout=15) as resp:
        return json.load(resp)


def download_asset(asset_api_url: str, dest: str) -> None:
    """release asset のAPI URL（`/releases/assets/{id}`）からダウンロードする。

    Privateリポジトリでは browser_download_url ではなくこちらのAPIエンドポイントに
    Accept: application/octet-stream を付けてアクセスする必要がある。
    """
    req = urllib.request.Request(asset_api_url, headers=_headers("application/octet-stream"))
    with _opener.open(req, timeout=120) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _no_access_message() -> str:
    if _load_token():
        return (
            "最新バージョンの確認に失敗しました（トークンで認証できませんでした）。\n"
            "token.txt の中身が正しいか、期限切れになっていないか確認してください。"
        )
    return (
        "最新バージョンの確認に失敗しました（アクセス権限がありません）。\n"
        "このリポジトリは現在Privateです。開発者（yuuma）からアクセス用トークンを\n"
        "もらい、このexeと同じフォルダに token.txt という名前でトークンだけを\n"
        "1行貼り付けて保存してから再実行してください。"
    )


def main() -> int:
    print("PC-karukaru-YYY アップデーター")
    print("=" * 40)

    install_dir = find_install_dir()
    if not install_dir:
        _pause(
            "インストール先が見つかりませんでした。\n"
            f"このファイルを {EXE_NAME} と同じフォルダに置いて再実行してください。"
        )
        return 1

    target = os.path.join(install_dir, EXE_NAME)
    print(f"インストール先: {install_dir}")

    if is_running(target):
        _pause(f"{EXE_NAME} が起動中です。アプリを終了してから再実行してください。")
        return 1

    print("最新バージョンを確認しています...")
    try:
        release = fetch_latest()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            _pause(_no_access_message())
        else:
            _pause(f"最新バージョンの確認に失敗しました（HTTP {e.code}）。")
        return 1
    except (urllib.error.URLError, OSError) as e:
        _pause(f"最新バージョンの確認に失敗しました: {e}")
        return 1

    latest_tag = release.get("tag_name", "")
    asset = next((a for a in release.get("assets", []) if a.get("name") == EXE_NAME), None)
    if not asset or not asset.get("url"):
        _pause("最新リリースに本体exeが見つかりませんでした。")
        return 1

    print(f"最新バージョン: {latest_tag}")
    print("ダウンロード中...")
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".exe")
    os.close(tmp_fd)
    try:
        download_asset(asset["url"], tmp_path)
    except urllib.error.HTTPError as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if e.code in (401, 403, 404):
            _pause(_no_access_message())
        else:
            _pause(f"ダウンロードに失敗しました（HTTP {e.code}）。")
        return 1
    except (urllib.error.URLError, OSError) as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        _pause(f"ダウンロードに失敗しました: {e}")
        return 1

    print("置き換え中...")
    try:
        shutil.move(tmp_path, target)
    except PermissionError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if not is_admin():
            print("書き込み権限がありません。管理者権限で起動し直します...")
            if relaunch_as_admin():
                return 0
        _pause("置き換えに失敗しました（管理者権限が必要な場合があります）。")
        return 1
    except OSError as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        _pause(f"置き換えに失敗しました: {e}")
        return 1

    print(f"\n更新が完了しました（{latest_tag}）。")
    try:
        launch = input("今すぐ起動しますか？ [Y/n]: ").strip().lower()
    except EOFError:
        launch = "n"
    if launch in ("", "y", "yes"):
        subprocess.Popen([target], cwd=install_dir)
    else:
        _pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())

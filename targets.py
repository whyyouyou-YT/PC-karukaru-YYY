"""掃除対象カテゴリの定義。

ここに書かれたパス配下だけが削除の対象になる。scanner / cleaner は
このモジュールが返した実在パスのみを扱い、それ以外には一切触れない。

パスの追加時の原則:
- 「アプリが再生成するキャッシュ」か「明確な一時ファイル」だけを safe にする
- ユーザーが作ったファイルが混ざりうるものは risk="caution" + default_on=False
- ルートフォルダ自体は消さず、常に中身だけを消す（kind="dir_contents"）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local")
APPDATA = os.environ.get("APPDATA", r"C:\Users\Default\AppData\Roaming")
PROGRAMDATA = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
WINDIR = os.environ.get("WINDIR", r"C:\Windows")

RISK_SAFE = "safe"
RISK_CAUTION = "caution"


@dataclass(frozen=True)
class Root:
    """1つの掃除対象ディレクトリ。"""

    path: str
    # None ならディレクトリの中身すべて。指定時はその glob にマッチするものだけ。
    pattern: str | None = None


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    detail: str
    risk: str
    default_on: bool
    roots: list[Root] = field(default_factory=list)
    # "dir_contents": roots 配下の中身を消す / "recycle_bin": ごみ箱API
    kind: str = "dir_contents"
    # 最終更新がこの時間以内のものは対象外（使用中ファイルを避ける）
    min_age_hours: int = 0
    # True なら削除ではなくごみ箱送り（復元可能）。ユーザーの実ファイル向け。
    to_trash: bool = False
    # 管理者権限がないと一部消せない見込み
    needs_admin: bool = False
    # これらのプロセスが動いていたら警告し、チェックを自動で外す
    conflict_processes: tuple[str, ...] = ()
    # 削除前にフォルダ内のロックを調べ、1つでも掴まれていたらフォルダごと見送る。
    # 実行中アプリの展開先（PyInstaller の _MEI* 等）を半分だけ消す事故を防ぐ。
    check_locks: bool = False


def _exists(path: str) -> bool:
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def _chrome_cache_roots() -> list[Root]:
    """Chrome の全プロファイルのキャッシュ系フォルダを列挙する。"""
    user_data = Path(LOCALAPPDATA) / "Google" / "Chrome" / "User Data"
    if not user_data.is_dir():
        return []

    # プロファイル直下のキャッシュ系（履歴・Cookie・ログイン情報には触れない）
    per_profile = [
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        os.path.join("Service Worker", "CacheStorage"),
        os.path.join("Service Worker", "ScriptCache"),
    ]
    # User Data 直下の共有キャッシュ
    shared = ["GrShaderCache", "GraphiteDawnCache", "GPUPersistentCache", "ShaderCache"]

    roots: list[Root] = []
    try:
        entries = list(user_data.iterdir())
    except OSError:
        return []

    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        is_profile = name == "Default" or name.startswith("Profile ") or name == "Guest Profile"
        if not is_profile:
            continue
        for sub in per_profile:
            p = entry / sub
            if _exists(str(p)):
                roots.append(Root(str(p)))

    for sub in shared:
        p = user_data / sub
        if _exists(str(p)):
            roots.append(Root(str(p)))
    return roots


def _electron_cache_roots(app_dirs: list[str]) -> list[Root]:
    """Discord 等 Electron 製アプリのキャッシュフォルダを列挙する。"""
    subs = ["Cache", "Code Cache", "GPUCache", "Cache_Data", "ShaderCache"]
    roots: list[Root] = []
    for base in app_dirs:
        if not _exists(base):
            continue
        for sub in subs:
            p = os.path.join(base, sub)
            if _exists(p):
                roots.append(Root(p))
        # Discord は Cache/Cache_Data の入れ子構造を取る
        nested = os.path.join(base, "Cache", "Cache_Data")
        if _exists(nested):
            roots.append(Root(nested))
    return roots


def _steam_libraries() -> list[str]:
    """libraryfolders.vdf から Steam ライブラリのパスを収集する。"""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Steam"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Steam"),
    ]
    main = next((c for c in candidates if _exists(c)), None)
    libs: list[str] = []
    if main:
        libs.append(main)
        vdf = os.path.join(main, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    # 形式: "path"		"E:\\SteamLibrary"
                    if line.startswith('"path"'):
                        parts = line.split('"')
                        if len(parts) >= 4:
                            p = parts[3].replace("\\\\", "\\")
                            if _exists(p) and p not in libs:
                                libs.append(p)
        except OSError:
            pass
    return libs


def _steam_temp_roots() -> list[Root]:
    roots: list[Root] = []
    for lib in _steam_libraries():
        for sub in ("downloading", "temp", "shadercache"):
            p = os.path.join(lib, "steamapps", sub)
            if _exists(p):
                roots.append(Root(p))
        p = os.path.join(lib, "steamapps", "depotcache")
        if _exists(p):
            roots.append(Root(p))
    return roots


def _nvidia_cache_roots() -> list[Root]:
    roots: list[Root] = []
    for sub in ("DXCache", "GLCache", "ComputeCache", "OptixCache"):
        p = os.path.join(LOCALAPPDATA, "NVIDIA", sub)
        if _exists(p):
            roots.append(Root(p))
    for p in (
        os.path.join(LOCALAPPDATA, "D3DSCache"),
        os.path.join(LOCALAPPDATA, "AMD", "DxCache"),
        os.path.join(LOCALAPPDATA, "Intel", "ShaderCache"),
    ):
        if _exists(p):
            roots.append(Root(p))
    return roots


def build_targets() -> list[Target]:
    """この PC に実在するパスだけを持つ Target のリストを返す。"""
    raw: list[Target] = [
        Target(
            key="user_temp",
            label="ユーザー一時ファイル",
            detail="アプリがインストール中・実行中に作った一時ファイル。24時間以内に作られたものと、実行中のアプリが掴んでいるフォルダは除外します。",
            risk=RISK_SAFE,
            default_on=True,
            min_age_hours=24,
            check_locks=True,
            roots=[Root(os.path.join(LOCALAPPDATA, "Temp"))],
        ),
        Target(
            key="windows_temp",
            label="Windows 一時ファイル",
            detail="システム側の一時ファイル置き場。使用中のものはスキップされます。",
            risk=RISK_SAFE,
            default_on=True,
            min_age_hours=24,
            needs_admin=True,
            check_locks=True,
            roots=[Root(os.path.join(WINDIR, "Temp"))],
        ),
        Target(
            key="chrome_cache",
            label="Chrome キャッシュ",
            detail="表示済みページの画像・スクリプトのキャッシュとGPUシェーダー。履歴・Cookie・保存したパスワードには触れません（次回のページ表示が少し遅くなるだけ）。",
            risk=RISK_SAFE,
            default_on=True,
            conflict_processes=("chrome.exe",),
            roots=_chrome_cache_roots(),
        ),
        Target(
            key="discord_cache",
            label="Discord キャッシュ",
            detail="Discord が保存した画像・添付ファイルのキャッシュ。ログイン状態やメッセージは消えません。",
            risk=RISK_SAFE,
            default_on=True,
            conflict_processes=("discord.exe", "discordptb.exe", "discordcanary.exe"),
            roots=_electron_cache_roots(
                [
                    os.path.join(APPDATA, "discord"),
                    os.path.join(APPDATA, "discordptb"),
                    os.path.join(APPDATA, "discordcanary"),
                ]
            ),
        ),
        Target(
            key="shader_cache",
            label="GPU シェーダーキャッシュ",
            detail="NVIDIA / DirectX が生成したシェーダーキャッシュ。削除後、各ゲームの初回起動だけ少し重くなりますが自動で作り直されます。",
            risk=RISK_SAFE,
            default_on=True,
            roots=_nvidia_cache_roots(),
        ),
        Target(
            key="crash_dumps",
            label="クラッシュダンプ",
            detail="アプリが落ちたときに出力されるメモリダンプ。1個で数百MBになることがあります。",
            risk=RISK_SAFE,
            default_on=True,
            needs_admin=True,
            roots=[
                Root(os.path.join(LOCALAPPDATA, "CrashDumps")),
                Root(os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "WER")),
                Root(os.path.join(PROGRAMDATA, "Microsoft", "Windows", "WER")),
            ],
        ),
        Target(
            key="thumbnail_cache",
            label="サムネイル・アイコンキャッシュ",
            detail="エクスプローラーが作るサムネイル画像のキャッシュ。再表示時に自動で作り直されます。",
            risk=RISK_SAFE,
            default_on=True,
            roots=[
                Root(
                    os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "Explorer"),
                    pattern="*cache_*.db",
                )
            ],
        ),
        Target(
            key="windows_update",
            label="Windows Update の残骸",
            detail="適用済みの更新プログラムのダウンロードファイルと配信最適化キャッシュ。数GB単位で溜まります。",
            risk=RISK_SAFE,
            default_on=True,
            needs_admin=True,
            roots=[
                Root(os.path.join(WINDIR, "SoftwareDistribution", "Download")),
                Root(os.path.join(WINDIR, "SoftwareDistribution", "DeliveryOptimization")),
                Root(
                    os.path.join(
                        WINDIR,
                        "ServiceProfiles",
                        "NetworkService",
                        "AppData",
                        "Local",
                        "Microsoft",
                        "Windows",
                        "DeliveryOptimization",
                    )
                ),
            ],
        ),
        Target(
            key="dev_cache",
            label="開発ツールのキャッシュ",
            detail="pip / npm / yarn などがダウンロードしたパッケージのキャッシュ。再インストール時に再ダウンロードが走るぶん遅くなります。",
            risk=RISK_CAUTION,
            default_on=False,
            roots=[
                Root(os.path.join(LOCALAPPDATA, "pip", "Cache")),
                Root(os.path.join(APPDATA, "npm-cache", "_cacache")),
                Root(os.path.join(LOCALAPPDATA, "npm-cache", "_cacache")),
                Root(os.path.join(LOCALAPPDATA, "Yarn", "Cache")),
                Root(os.path.join(LOCALAPPDATA, "uv", "cache")),
            ],
        ),
        Target(
            key="steam_temp",
            label="Steam ダウンロードの残骸",
            detail="中断したダウンロードの断片とデポキャッシュ。インストール済みゲーム本体には触れません。ダウンロード中のゲームがあるときは実行しないでください。",
            risk=RISK_CAUTION,
            default_on=False,
            conflict_processes=("steam.exe",),
            roots=_steam_temp_roots(),
        ),
        Target(
            key="recycle_bin",
            label="ごみ箱を空にする",
            detail="全ドライブのごみ箱。空にすると復元できなくなります。",
            risk=RISK_CAUTION,
            default_on=False,
            kind="recycle_bin",
        ),
    ]

    result: list[Target] = []
    for t in raw:
        if t.kind == "recycle_bin":
            result.append(t)
            continue
        alive = [r for r in t.roots if _exists(r.path)]
        if alive:
            result.append(replace(t, roots=alive))
    return result

"""削除の実行。

安全装置:
1. scanner が列挙した Item しか消さない
2. その Item が対象カテゴリのルート配下にあることを削除直前に再検証する
3. ルートフォルダ自体は絶対に消さない（中身だけ）
4. 使用中・権限不足で消せないものはスキップしてカウントするだけ（強制解除はしない）
"""
from __future__ import annotations

import os
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from scanner import CategoryResult, Item, _walk_dir
from targets import Target
from winutil import empty_recycle_bin, files_in_use, send_to_recycle_bin

# _has_locked_file の1回の Restart Manager 問い合わせに渡す最大ファイル数
_RM_BATCH_SIZE = 500


@dataclass
class CleanResult:
    key: str
    freed: int = 0
    deleted: int = 0
    skipped: int = 0
    # 使用中のため丸ごと見送ったフォルダ数
    locked: int = 0
    errors: list[str] = field(default_factory=list)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_inside(path: str, root: str) -> bool:
    """path が root の「配下」か。root 自身は False。"""
    p = _norm(path)
    r = _norm(root).rstrip("\\/")
    return p != r and p.startswith(r + os.sep)


def _force_remove(func, path, _exc_info):
    """読み取り専用属性で消せなかったファイルを、属性を落として消し直す。"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise


def _is_locked(path: str) -> bool:
    """他プロセスがこのファイルを開いているか（Restart Manager経由）。"""
    try:
        return files_in_use([path])
    except OSError:
        return True


def _has_locked_file(root: str, budget: int = 5000) -> bool:
    """フォルダ配下に使用中のファイルがあるか。

    実行中アプリの展開先フォルダ（PyInstaller の _MEI* など）を
    「消せたファイルだけ消して壊す」のを防ぐための事前チェック。
    パスの列挙だけ済ませ、実際の使用中判定は Restart Manager にまとめて
    問い合わせる（1件ずつ開き直すより高速で、読み取り共有だけのロックも拾える）。

    全件を調べ切れなかった場合（budget 超過・走査エラー・RM判定不能）は True を返す。
    「使用中なしと確認できた」ときだけ False を返す契約であることが重要で、
    未確認のまま False を返すと、まさにこの関数が防ごうとしている
    「途中まで消してフォルダを壊す」事故が起きる。budget は暴走防止の上限。
    """
    paths: list[str] = []
    checked = 0
    stack = [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                    except OSError:
                        return True
                    paths.append(entry.path)
                    checked += 1
                    if checked >= budget:
                        return True  # 調べ切れていない = 安全側に倒す
        except OSError:
            return True

    for i in range(0, len(paths), _RM_BATCH_SIZE):
        try:
            if files_in_use(paths[i : i + _RM_BATCH_SIZE]):
                return True
        except OSError:
            return True  # 判定できなければ安全側に倒す
    return False


def _delete_item(item: Item) -> tuple[int, bool, str | None]:
    """(解放できたバイト数, 成功したか, エラー文字列) を返す。"""
    try:
        if item.is_dir:
            shutil.rmtree(item.path, onexc=_force_remove)
        else:
            try:
                os.remove(item.path)
            except PermissionError:
                os.chmod(item.path, stat.S_IWRITE)
                os.remove(item.path)
        return item.size, True, None
    except OSError as exc:
        # 部分的に消えている可能性があるので、残っているぶんを測り直す
        remaining = item.size
        if item.is_dir and os.path.isdir(item.path):
            remaining, _, _, _ = _walk_dir(item.path)
        elif not os.path.exists(item.path):
            remaining = 0
        freed = max(item.size - remaining, 0)
        return freed, False, f"{os.path.basename(item.path)}: {exc.strerror or exc}"


def clean_target(
    target: Target,
    scan: CategoryResult,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> CleanResult:
    """1カテゴリぶんを削除する。scan は同じカテゴリのスキャン結果であること。"""
    result = CleanResult(key=target.key)

    if target.kind == "recycle_bin":
        if empty_recycle_bin():
            result.freed = scan.size
            result.deleted = scan.files
        else:
            result.skipped = scan.files
            result.errors.append("ごみ箱を空にできませんでした")
        return result

    roots = [r.path for r in target.roots]
    # ルート配下にあるものだけに絞り込む（二重チェック）
    valid = [i for i in scan.items if any(_is_inside(i.path, r) for r in roots)]
    result.skipped += len(scan.items) - len(valid)

    if target.to_trash:
        existing = [i for i in valid if os.path.exists(i.path)]
        if send_to_recycle_bin([i.path for i in existing]):
            result.freed = sum(i.size for i in existing)
            result.deleted = len(existing)
        else:
            # 一括で失敗したら1件ずつ試す
            for item in existing:
                if send_to_recycle_bin([item.path]):
                    result.freed += item.size
                    result.deleted += 1
                else:
                    result.skipped += 1
        return result

    # 使用中のものが混ざったフォルダは、半分だけ消して壊すより丸ごと見送る。
    # 1件ずつ調べると数秒〜十数秒かかるので、削除の前にまとめて並列で判定する。
    locked_paths: set[str] = set()
    if target.check_locks and valid:
        # 大きいフォルダが1スレッドを占有すると全体が待たされる（16並列で11.9秒、
        # 32並列で0.34秒）。ほぼ I/O 待ちなのでスレッドは多めに取る。
        with ThreadPoolExecutor(max_workers=32) as pool:
            checks = {
                pool.submit(_has_locked_file if i.is_dir else _is_locked, i.path): i
                for i in valid
            }
            for future in as_completed(checks):
                try:
                    if future.result():
                        locked_paths.add(checks[future].path)
                except Exception:
                    locked_paths.add(checks[future].path)  # 判定できなければ触らない

    total = len(valid)
    for index, item in enumerate(valid, 1):
        if item.path in locked_paths:
            result.locked += 1
            result.skipped += 1
            continue

        freed, ok, err = _delete_item(item)
        result.freed += freed
        if ok:
            result.deleted += 1
        else:
            result.skipped += 1
            if err and len(result.errors) < 5:
                result.errors.append(err)
        if on_progress and (index % 20 == 0 or index == total):
            on_progress(target.key, index, total)
    return result


def clean_all(
    targets: list[Target],
    scans: dict[str, CategoryResult],
    keys: list[str],
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, CleanResult]:
    """指定キーのカテゴリを順に削除する（I/O が競合するので直列）。"""
    by_key = {t.key: t for t in targets}
    results: dict[str, CleanResult] = {}
    for key in keys:
        target = by_key.get(key)
        scan = scans.get(key)
        if not target or not scan:
            continue
        results[key] = clean_target(target, scan, on_progress)
    return results

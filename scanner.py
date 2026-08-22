"""掃除対象のスキャン。

os.scandir で再帰し、カテゴリごとに並列でサイズを集計する。
削除の単位は「対象ルート直下の項目」（ファイル or フォルダ）で、
scanner が組み立てたこのリスト以外を cleaner が消すことはない。

ジャンクション・シンボリックリンク（リパースポイント）は辿らず、対象からも外す。
`%LOCALAPPDATA%\\Temp` 配下などにリンクが仕込まれていた場合に、
リンク先の実データを巻き込んで消してしまう事故を防ぐため。
"""
from __future__ import annotations

import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Callable

from targets import Target
from winutil import query_recycle_bin

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_point(entry: os.DirEntry) -> bool:
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True  # 判定できないものは触らない
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass
class Item:
    """削除の最小単位。"""

    path: str
    size: int
    files: int
    mtime: float
    is_dir: bool


@dataclass
class CategoryResult:
    key: str
    size: int = 0
    files: int = 0
    items: list[Item] = field(default_factory=list)
    # 新しすぎて対象から外したぶん
    skipped_recent: int = 0
    skipped_recent_size: int = 0
    # アクセスできなかったぶん（多くは管理者権限か使用中）
    denied: int = 0
    error: str | None = None


def _walk_dir(path: str) -> tuple[int, int, float, int]:
    """(合計サイズ, ファイル数, 最新mtime, アクセス拒否数) を返す。リンクは辿らない。"""
    total = 0
    files = 0
    newest = 0.0
    denied = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if _is_reparse_point(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        st = entry.stat(follow_symlinks=False)
                        total += st.st_size
                        files += 1
                        if st.st_mtime > newest:
                            newest = st.st_mtime
                    except OSError:
                        denied += 1
        except PermissionError:
            denied += 1
        except OSError:
            denied += 1
    return total, files, newest, denied


def _scan_root(root_path: str, pattern: str | None, min_age_hours: int) -> CategoryResult:
    res = CategoryResult(key="")
    cutoff = time.time() - min_age_hours * 3600 if min_age_hours else None
    try:
        entries = list(os.scandir(root_path))
    except PermissionError:
        res.denied += 1
        return res
    except OSError as exc:
        res.error = str(exc)
        return res

    for entry in entries:
        try:
            if _is_reparse_point(entry):
                continue
            if pattern and not fnmatch(entry.name, pattern):
                continue
            if entry.is_dir(follow_symlinks=False):
                size, files, newest, denied = _walk_dir(entry.path)
                res.denied += denied
                mtime = newest
                if mtime == 0.0:
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                item = Item(entry.path, size, max(files, 1), mtime, True)
            else:
                st = entry.stat(follow_symlinks=False)
                item = Item(entry.path, st.st_size, 1, st.st_mtime, False)
        except OSError:
            res.denied += 1
            continue

        if cutoff is not None and item.mtime > cutoff:
            res.skipped_recent += 1
            res.skipped_recent_size += item.size
            continue

        res.items.append(item)
        res.size += item.size
        res.files += item.files
    return res


def scan_target(target: Target) -> CategoryResult:
    """1カテゴリをスキャンする。"""
    if target.kind == "recycle_bin":
        info = query_recycle_bin()
        res = CategoryResult(key=target.key, size=info.size, files=int(info.items))
        return res

    merged = CategoryResult(key=target.key)
    for root in target.roots:
        part = _scan_root(root.path, root.pattern, target.min_age_hours)
        merged.size += part.size
        merged.files += part.files
        merged.items.extend(part.items)
        merged.skipped_recent += part.skipped_recent
        merged.skipped_recent_size += part.skipped_recent_size
        merged.denied += part.denied
        if part.error and not merged.error:
            merged.error = part.error
    merged.items.sort(key=lambda i: i.size, reverse=True)
    return merged


def _merge(dst: CategoryResult, src: CategoryResult) -> None:
    dst.size += src.size
    dst.files += src.files
    dst.items.extend(src.items)
    dst.skipped_recent += src.skipped_recent
    dst.skipped_recent_size += src.skipped_recent_size
    dst.denied += src.denied
    if src.error and not dst.error:
        dst.error = src.error


def scan_all(
    targets: list[Target],
    on_result: Callable[[CategoryResult], None] | None = None,
    max_workers: int = 16,
) -> dict[str, CategoryResult]:
    """全カテゴリをスキャンし、終わったカテゴリから on_result に流す。

    カテゴリ単位ではなくルートフォルダ単位で並列に投げる。Chrome のように
    ルートが数十個あるカテゴリが1スレッドで直列処理されるのを避けるため。
    """
    results = {t.key: CategoryResult(key=t.key) for t in targets}
    pending: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures: dict = {}
        for target in targets:
            if target.kind == "recycle_bin":
                pending[target.key] = 1
                futures[pool.submit(scan_target, target)] = (target, True)
                continue
            pending[target.key] = len(target.roots)
            for root in target.roots:
                futures[
                    pool.submit(_scan_root, root.path, root.pattern, target.min_age_hours)
                ] = (target, False)

        # ルートが1つも実在しないカテゴリは待たずに確定させる
        for target in targets:
            if pending.get(target.key) == 0 and on_result:
                on_result(results[target.key])

        for future in as_completed(futures):
            target, whole = futures[future]
            try:
                part = future.result()
            except Exception as exc:  # 1ルートの失敗で全体を止めない
                part = CategoryResult(key=target.key, error=str(exc))
            if whole:
                results[target.key] = part
                part.key = target.key
            else:
                _merge(results[target.key], part)
            pending[target.key] -= 1
            if pending[target.key] == 0:
                res = results[target.key]
                res.items.sort(key=lambda i: i.size, reverse=True)
                if on_result:
                    on_result(res)
    return results

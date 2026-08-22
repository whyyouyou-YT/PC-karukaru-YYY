"""削除まわりの安全装置のテスト。

実行: python test_safety.py

本物の掃除対象には一切触れず、テンポラリに作ったサンドボックスだけを使う。
特に「ジャンクション先の実データを巻き込まないこと」を必ず確認する。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
import unittest

from cleaner import _has_locked_file, _is_inside, clean_target
from scanner import scan_target
from targets import Root, Target


def _open_exclusive(path: str):
    """実行中の exe/dll と同じ共有モード（読み取りのみ許可）でファイルを掴む。"""
    GENERIC_READ_WRITE = 0xC0000000
    FILE_SHARE_READ = 0x00000001
    OPEN_EXISTING = 3
    handle = ctypes.windll.kernel32.CreateFileW(
        ctypes.c_wchar_p(path), GENERIC_READ_WRITE, FILE_SHARE_READ,
        None, OPEN_EXISTING, 0, None,
    )
    return None if handle in (-1, 0xFFFFFFFFFFFFFFFF, None) else handle


def make_target(root: str, **kwargs) -> Target:
    defaults = dict(
        key="test",
        label="テスト",
        detail="",
        risk="safe",
        default_on=True,
        roots=[Root(root)],
    )
    defaults.update(kwargs)
    return Target(**defaults)


def write(path: str, content: str = "x" * 1024) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class SafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = tempfile.mkdtemp(prefix="tc_test_")
        self.root = os.path.join(self.base, "root")
        self.outside = os.path.join(self.base, "outside")
        os.makedirs(self.root)
        os.makedirs(self.outside)

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    # --- パス判定 ---------------------------------------------------

    def test_is_inside(self):
        self.assertTrue(_is_inside(os.path.join(self.root, "a"), self.root))
        self.assertTrue(_is_inside(os.path.join(self.root, "a", "b"), self.root))
        # ルート自身は「配下」ではない = 消してはいけない
        self.assertFalse(_is_inside(self.root, self.root))
        self.assertFalse(_is_inside(self.outside, self.root))
        # 前方一致だけで判定していないこと（root と root2 の取り違え）
        self.assertFalse(_is_inside(self.root + "2\\a", self.root))

    # --- 基本の削除 -------------------------------------------------

    def test_deletes_contents_but_keeps_root(self):
        write(os.path.join(self.root, "a.tmp"))
        write(os.path.join(self.root, "sub", "b.tmp"))
        target = make_target(self.root)
        result = clean_target(target, scan_target(target))

        self.assertTrue(os.path.isdir(self.root), "ルートフォルダは残すこと")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(result.deleted, 2)
        self.assertGreater(result.freed, 0)

    def test_ignores_items_outside_root(self):
        """スキャン結果が改竄されてもルート外は消さない。"""
        victim = write(os.path.join(self.outside, "important.txt"))
        target = make_target(self.root)
        scan = scan_target(target)
        # ルート外のパスを scan 結果に紛れ込ませる
        from scanner import Item

        scan.items.append(Item(victim, 1024, 1, 0.0, False))

        result = clean_target(target, scan)
        self.assertTrue(os.path.exists(victim), "ルート外のファイルを消してはいけない")
        self.assertEqual(result.skipped, 1)

    # --- ジャンクション ---------------------------------------------

    def test_does_not_follow_junction(self):
        """ルート配下のジャンクションを辿ってリンク先を消さないこと。"""
        real_dir = os.path.join(self.outside, "real")
        victim = write(os.path.join(real_dir, "precious.txt"))
        link = os.path.join(self.root, "link")
        rc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", link, real_dir],
            capture_output=True, text=True,
        )
        if rc.returncode != 0:
            self.skipTest("ジャンクションを作成できない環境")

        target = make_target(self.root)
        scan = scan_target(target)
        # スキャン段階でリンクは対象外になっている
        self.assertEqual([i.path for i in scan.items], [])

        clean_target(target, scan)
        self.assertTrue(os.path.exists(victim), "リンク先の実ファイルを消してはいけない")

    # --- ロック検出 -------------------------------------------------

    def test_locked_folder_is_skipped_entirely(self):
        """使用中ファイルを含むフォルダは、部分的に消さず丸ごと見送る。"""
        folder = os.path.join(self.root, "app_running")
        write(os.path.join(folder, "free1.dll"))
        write(os.path.join(folder, "free2.dll"))
        locked_path = os.path.join(folder, "inuse.dll")
        write(locked_path)

        # 実行中の exe/dll と同じ開き方（読み取り共有のみ）で掴んだまま削除を試す
        handle = _open_exclusive(locked_path)
        if handle is None:
            self.skipTest("排他ハンドルを取得できない環境")
        try:
            self.assertTrue(_has_locked_file(folder))

            target = make_target(self.root, check_locks=True)
            result = clean_target(target, scan_target(target))

            self.assertEqual(result.locked, 1)
            self.assertTrue(os.path.exists(os.path.join(folder, "free1.dll")),
                            "同じフォルダの他のファイルも残すこと")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    def test_unverifiable_folder_is_treated_as_locked(self):
        """全件調べ切れなかったフォルダは「ロックあり」として扱う。

        budget で打ち切って False を返すと、未確認のファイルにロックがあった場合に
        rmtree が途中で失敗し、まさに防ぎたい部分削除が起きる。
        """
        folder = os.path.join(self.root, "many_files")
        for i in range(20):
            write(os.path.join(folder, f"f{i}.dat"), "x")

        self.assertFalse(_has_locked_file(folder), "全件調べ切れたならロックなし")
        self.assertTrue(_has_locked_file(folder, budget=5),
                        "調べ切れていないなら安全側（ロックあり扱い）にすること")

    def test_min_age_filter(self):
        """新しいファイルは対象に入らない。"""
        write(os.path.join(self.root, "fresh.tmp"))
        target = make_target(self.root, min_age_hours=24)
        scan = scan_target(target)
        self.assertEqual(scan.items, [])
        self.assertEqual(scan.skipped_recent, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

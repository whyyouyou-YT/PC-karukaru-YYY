# Temp Cleaner

Windows の一時ファイル・キャッシュ削除ツール。Microsoft PC Manager の代替として作成。

## 設計方針（PC Manager の不満点への対応）

| PC Manager の不満 | このツールでの対応 |
|---|---|
| 常駐する・広告や余計な機能がある | 常駐なし。ウィンドウを閉じればプロセスも終了。掃除機能のみ |
| スキャンが遅い | 起動と同時にスキャン開始。ルートフォルダ単位で並列処理し、実測 **0.7〜1.0 秒**（12カテゴリ・約37GB検出時） |
| 何を消すのか分からない | 全項目のフルパス・サイズ・最終更新日を表示。クリックでエクスプローラーを開いて中身を確認できる |
| 勝手に判断される | チェックは自分で操作。危険寄りの項目は既定でオフ |

## 起動

```
python app.py            # 通常起動
python app.py --admin    # 管理者権限で起動
```

`S:\MY-life\実行ファイル\start-temp-cleaner.vbs`（コンソール非表示）からも起動できる。
Windows Temp・Windows Update の残骸まで消すには管理者権限が必要（`start-temp-cleaner-admin.vbs`）。
※ `実行ファイル/` は `.gitignore` 対象（Python の絶対パスを直書きしているため）。環境を移したら作り直すこと。

依存は `pywebview` のみ（`pip install -r requirements.txt`）。

## exeのビルド方法

Python環境なしで配布・起動できる単体exe（`dist/temp_cleaner.exe`）を作る手順。

1. 依存パッケージをインストール

   ```
   pip install -r requirements.txt
   pip install pyinstaller
   ```

2. ビルド

   ```
   pyinstaller build.spec --noconfirm
   ```

3. `dist/temp_cleaner.exe` が生成される。このexe単体を配布すれば、Python環境の追加インストールなしに動作する（管理者権限が必要な項目はアプリ内のボタンからUAC昇格して都度実行する方式のまま、exeのマニフェスト自体はrequireAdministrator化していない）

## 掃除対象

**安全に消せるもの（既定でオン）**

- ユーザー一時ファイル `%LOCALAPPDATA%\Temp`
- Windows 一時ファイル `C:\Windows\Temp`
- Chrome キャッシュ（全プロファイルの Cache / Code Cache / GPUCache / Service Worker キャッシュ）
- Discord キャッシュ
- GPU シェーダーキャッシュ（NVIDIA DXCache/GLCache、D3DSCache 等）
- クラッシュダンプ・エラーレポート（CrashDumps / WER）
- サムネイル・アイコンキャッシュ
- Windows Update の残骸（SoftwareDistribution\Download、配信最適化）

**中身を確認してから消すもの（既定でオフ）**

- 開発ツールのキャッシュ（pip / npm / yarn / uv）
- Steam ダウンロードの残骸（downloading / temp / depotcache）
- ごみ箱
- ダウンロードフォルダの 90 日以上前のファイル（**ごみ箱送り**。復元可能）

履歴・Cookie・保存されたパスワード・インストール済みゲーム本体には触れない。

## 安全装置

削除は「消せるものを全部消す」ではなく「壊さないほうを優先する」設計。

1. **ルート外は絶対に消さない** — `targets.py` に定義したパス配下だけを対象とし、削除の直前にも配下判定を再検証する（`cleaner._is_inside`）
2. **ルートフォルダ自体は消さない** — 常に中身だけを消す
3. **リンクを辿らない** — ジャンクション・シンボリックリンク（リパースポイント）はスキャン対象から外す。Temp 配下にリンクが仕込まれていてもリンク先の実データを巻き込まない
4. **新しいファイルは触らない** — Temp 系は最終更新から 24 時間以内のものを除外（フォルダは配下の最新更新日で判定）
5. **部分削除をしない** — Temp 系は削除の直前にフォルダ内のロックを並列で調べ、使用中ファイルが 1 つでもあればフォルダごと見送る。PyInstaller 製アプリの展開先（`_MEI*`）などを半分だけ消してアプリを壊す事故を防ぐ。`_has_locked_file` は「ロックが無いと**確認できた**」ときだけ False を返す契約で、走査エラーや上限超過で調べ切れなかった場合は True（＝触らない）に倒す
6. **起動中アプリの検出** — Chrome / Discord / Steam が動いていたら該当カテゴリのチェックを自動で外し、警告を表示する
7. **強制解除はしない** — ロックされたファイルはスキップしてカウントするだけ（読み取り専用属性の解除のみ行う）
8. **ユーザーの実ファイルはごみ箱送り** — ダウンロードフォルダの項目は完全削除ではなく `SHFileOperationW` でごみ箱へ

## テスト

```
python test_safety.py
```

安全装置のテスト（ルート外の除外・ジャンクション非追従・ロック時の全体見送り・24時間フィルタ等）。
本物の掃除対象には触れず、テンポラリのサンドボックスのみを使う。

## 構成

| ファイル | 役割 |
|---|---|
| `app.py` | pywebview ウィンドウと JS API。**Api クラスに状態を持たせないこと**（pywebview が属性を再帰走査して無限再帰でクラッシュする） |
| `targets.py` | 掃除対象カテゴリの定義。ここに書かれたパスだけが削除対象になる |
| `scanner.py` | 並列スキャン。リパースポイントを除外し、削除単位（Item）を組み立てる |
| `cleaner.py` | 削除の実行と安全装置 |
| `winutil.py` | ごみ箱 API・管理者権限・プロセス列挙・空き容量（ctypes のみ、外部依存なし） |
| `static/` | UI（HTML / JS） |
| `build.spec` | PyInstaller のビルド定義。単体exe化に使う |

## 対象を追加するとき

`targets.py` の `build_targets()` に `Target` を足す。原則:

- アプリが再生成するキャッシュか、明確な一時ファイルだけを `risk=RISK_SAFE` にする
- ユーザーが作ったファイルが混ざりうるものは `RISK_CAUTION` + `default_on=False` + できれば `to_trash=True`
- 実行中アプリの作業フォルダになりうる場所は `check_locks=True`
- 対象アプリが起動中だと危ないものは `conflict_processes` を設定する

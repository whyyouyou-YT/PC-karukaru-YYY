# PC-karukaru-YYY

Windows の一時ファイル・キャッシュ削除ツール。Microsoft PC Manager の代替として作成。

## 設計方針（PC Manager の不満点への対応）

| PC Manager の不満 | このツールでの対応 |
|---|---|
| 常駐する・広告や余計な機能がある | 常駐なし。ウィンドウを閉じればプロセスも終了。掃除機能のみ |
| スキャンが遅い | 起動と同時にスキャン開始。ルートフォルダ単位で並列処理し、実測 **0.7〜1.0 秒**（12カテゴリ・約37GB検出時） |
| 何を消すのか分からない | 全項目のフルパス・サイズ・最終更新日を表示。クリックでエクスプローラーを開いて中身を確認できる |
| 勝手に判断される | チェックは自分で操作。危険寄りの項目は既定でオフ |
| 毎回チェックし直すのが面倒 | 前回の選択を `%LOCALAPPDATA%\PC-karukaru-YYY\settings.json` に記憶。起動中アプリで強制オフになった項目も「起動中以外を選択」ボタンでワンクリック復元できる |

## 表示テーマ

ヘッダーの「ライトモードにする / ダークモードにする」ボタンで切り替え可能。
選択は `%LOCALAPPDATA%\PC-karukaru-YYY\settings.json` に保存され、次回起動時に
自動で反映される（既定はダーク）。ヘッダーにはバージョン番号も表示される。

## 起動

```
python app.py            # 通常起動
python app.py --admin    # 管理者権限で起動
```

`S:\MY-life\実行ファイル\start-pc-karukaru-yyy.vbs`（コンソール非表示）からも起動できる。
Windows Temp・Windows Update の残骸まで消すには管理者権限が必要（`start-pc-karukaru-yyy-admin.vbs`）。
※ `実行ファイル/` は `.gitignore` 対象（Python の絶対パスを直書きしているため）。環境を移したら作り直すこと。

依存は `pywebview` のみ（`pip install -r requirements.txt`）。

## exeのビルド方法

Python環境なしで配布・起動できる単体exe（`dist/PC-karukaru-YYY.exe`）を作る手順。

1. 依存パッケージをインストール

   ```
   pip install -r requirements.txt
   pip install pyinstaller
   ```

2. ビルド

   ```
   pyinstaller build.spec --noconfirm
   ```

3. `dist/PC-karukaru-YYY.exe` が生成される。このexe単体を配布すれば、Python環境の追加インストールなしに動作する（管理者権限が必要な項目はアプリ内のボタンからUAC昇格して都度実行する方式のまま、exeのマニフェスト自体はrequireAdministrator化していない）

## インストーラーのビルド方法

Inno Setup 6が必要（`winget install --id JRSoftware.InnoSetup -e`で導入可能）。

1. 上記の手順で `dist/PC-karukaru-YYY.exe` をビルド済みにする
2. `installer.iss` の `MyAppVersion` を必要に応じて更新する
3. コンパイル

   ```
   "C:\Users\yuuma\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer.iss
   ```

4. `installer_dist\PC-karukaru-YYY-Setup-v<バージョン>.exe` が生成される。スタートメニュー登録・デスクトップアイコン任意作成・アンインストーラー付き

## アップデーターのビルド方法（既にインストール済みの人向け）

フルインストーラー（約20MB）を落とし直さず、本体exeだけを最新版に差し替える軽量ツール。
pywebview 等は使わず標準ライブラリのみなので `dist/PC-karukaru-YYY.exe` よりずっと軽い。

```
pyinstaller updater.spec --noconfirm
```

`dist/PC-karukaru-YYY-Updater.exe` が生成される。GitHub Release に本体exe・インストーラーと
並べてアップロードする。

**使い方（利用者側）**: `PC-karukaru-YYY-Updater.exe` をそのまま実行するだけ。インストール先は
レジストリのアンインストール情報から自動検出し、GitHub の最新Releaseから本体exeだけを
ダウンロードして上書きする。実行中は置き換えできないため、事前にアプリを閉じておくこと。
書き込み権限が無い場合は自動的に管理者権限で再起動する。

### リポジトリがPrivateの間のアクセス（知り合いに使ってもらう場合）

このリポジトリは現在Privateのため、GitHub Releases APIへの匿名アクセスは404になる。
知り合いにアップデーターを使ってもらうには、読み取り専用スコープのアクセストークンを
発行して渡す必要がある。

1. GitHubの [Settings > Developer settings > Fine-grained tokens](https://github.com/settings/tokens?type=beta) で新規発行
   - Repository access: `Only select repositories` → `PC-karukaru-YYY` のみ選択
   - Permissions: `Contents` を `Read-only` に設定（それ以外は付与しない）
   - 有効期限は必要に応じて短めに設定（失効すればいつでも同じ手順で再発行できる）
2. 発行されたトークン文字列（`github_pat_...`）を、渡したい相手にDiscord DM等の安全な経路で共有する
3. 相手は `PC-karukaru-YYY-Updater.exe` と同じフォルダに `token.txt` という名前のテキストファイルを作り、
   トークンだけを1行貼り付けて保存してから実行する（環境変数 `PC_KARUKARU_UPDATER_TOKEN` でも可）
4. リポジトリを将来Publicにした場合は `token.txt` は不要になる（無くてもそのまま動く）

トークンはリポジトリ単位・読み取り専用でスコープを絞ってあるため、漏れても実害は小さいが、
不要になったらGitHub側でいつでも失効させること。

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

履歴・Cookie・保存されたパスワード・インストール済みゲーム本体・ユーザーが作成したファイル（ダウンロードフォルダ等）には触れない。

## 安全装置

削除は「消せるものを全部消す」ではなく「壊さないほうを優先する」設計。

1. **ルート外は絶対に消さない** — `targets.py` に定義したパス配下だけを対象とし、削除の直前にも配下判定を再検証する（`cleaner._is_inside`）
2. **ルートフォルダ自体は消さない** — 常に中身だけを消す
3. **リンクを辿らない** — ジャンクション・シンボリックリンク（リパースポイント）はスキャン対象から外す。Temp 配下にリンクが仕込まれていてもリンク先の実データを巻き込まない
4. **新しいファイルは触らない** — Temp 系は最終更新から 24 時間以内のものを除外（フォルダは配下の最新更新日で判定）
5. **部分削除をしない** — Temp 系は削除の直前にフォルダ内のロックを並列で調べ、使用中ファイルが 1 つでもあればフォルダごと見送る。PyInstaller 製アプリの展開先（`_MEI*`）などを半分だけ消してアプリを壊す事故を防ぐ。`_has_locked_file` は「ロックが無いと**確認できた**」ときだけ False を返す契約で、走査エラーや上限超過で調べ切れなかった場合は True（＝触らない）に倒す
6. **起動中アプリの検出** — Chrome / Discord / Steam が動いていたら該当カテゴリのチェックを自動で外し、警告を表示する
7. **強制解除はしない** — ロックされたファイルはスキップしてカウントするだけ（読み取り専用属性の解除のみ行う）

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
| `settings.py` | 選択状態（チェック希望）・表示テーマの永続化。`%LOCALAPPDATA%\PC-karukaru-YYY\settings.json` に保存 |
| `version.py` | アプリのバージョン定義（単一の参照元）。リリース時は `installer.iss` の `MyAppVersion` と手動で合わせる |
| `static/` | UI（HTML / JS） |
| `build.spec` | PyInstaller のビルド定義。単体exe化に使う |
| `installer.iss` | Inno Setup のインストーラー定義 |
| `updater.py` / `updater.spec` | 既存インストールを最新Releaseの本体exeに差し替える軽量アップデーター。標準ライブラリのみで完結 |

## 対象を追加するとき

`targets.py` の `build_targets()` に `Target` を足す。原則:

- アプリが再生成するキャッシュか、明確な一時ファイルだけを `risk=RISK_SAFE` にする
- ユーザーが作ったファイルが混ざりうるものは `RISK_CAUTION` + `default_on=False` + できれば `to_trash=True`
- 実行中アプリの作業フォルダになりうる場所は `check_locks=True`
- 対象アプリが起動中だと危ないものは `conflict_processes` を設定する

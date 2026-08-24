"""アプリのバージョン定義。単一の参照元。

installer.iss の MyAppVersion はビルドツール（Inno Setup）側の都合で
別途手動で合わせる必要がある。リリース時はここと installer.iss の両方を更新すること。
"""

APP_VERSION = "1.1.2.1"

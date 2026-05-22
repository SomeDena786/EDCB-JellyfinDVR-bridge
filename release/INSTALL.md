# ビルド済みプラグインの導入

`jf-dvr-plugin_1.0.1.0/` がビルド済みの Jellyfin プラグインパッケージ。
ソースからビルドし直さずにそのまま導入できる。

## 手順

1. Jellyfin サーバーを停止する。

2. `jf-dvr-plugin_1.0.1.0` フォルダごと Jellyfin のプラグインフォルダにコピーする:

   ```
   %ProgramData%\Jellyfin\Server\plugins\jf-dvr-plugin_1.0.1.0\
   ```

   コピー後のフォルダ構成:

   ```
   %ProgramData%\Jellyfin\Server\plugins\jf-dvr-plugin_1.0.1.0\
     Jellyfin.Plugin.JfDvr.dll
     meta.json
   ```

3. Jellyfin サーバーを起動する。

4. ダッシュボード → プラグイン に「jf-dvr (EDCB Live TV)」が表示され、
   状態が **Active** になっていることを確認する。

## 注意

- プラグインは Python ブリッジ (`bridge/`) が稼働していることを前提とする。
  先にブリッジをセットアップすること (リポジトリ直下の `README.md` を参照)。
- 既に旧バージョンの `jf-dvr` プラグインフォルダがある場合は、二重ロードを
  避けるため**先に削除**してから新しいフォルダを置くこと。
- 動作対象は Jellyfin 10.11 系 (10.11.6 でビルド・確認)。

## 対応バージョン

| 項目 | 値 |
|---|---|
| プラグインバージョン | 1.0.1.0 |
| ターゲット ABI | 10.11.0.0 |
| ビルド構成 | .NET 9 / Release |

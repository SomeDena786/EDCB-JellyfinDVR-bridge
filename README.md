# jf-dvr

Bondriver + EDCB をバックエンドに、Windows 上の Jellyfin で
Live TV / DVR を実現するための連携ソフトウェア。

## 構成

EDCB が PT2 を専有して EPG 収集・録画・チューナー調停を担い、Jellyfin が
その前段の視聴 UI となる。両者を 2 つのコンポーネントで橋渡しする。

```
PT2 ─BonDriver─ EDCB (EpgDataCap_Bon / EpgTimerSrv)
                       │ CtrlCmd (TCP 4510) / NetworkTV
                       ▼
              Python ブリッジ (bridge/)          ← EDCB ゲートウェイ
                       │ REST + TS ストリーム (HTTP 40880)
                       ▼
              C# Jellyfin プラグイン (plugin/)   ← ILiveTvService アダプタ
                       │
                    Jellyfin
```

1. **Python ブリッジ** — EDCB ゲートウェイ。EDCB の CtrlCmd を叩き、チャンネル /
   番組表 / 予約 / 録画を REST で、ライブ TS と録画再生をストリームで提供する。
2. **C# Jellyfin プラグイン** — `ILiveTvService` を実装した薄いアダプタ。
   チャンネル・番組表・予約・ストリームをすべてブリッジに委譲する。Jellyfin の
   番組表からの録画予約が EDCB に登録され、EDCB 側の予約も Jellyfin の予約一覧
   (タイマー) に表示される。EPG も EDCB から取得する (XMLTV 設定は不要)。

## 必要環境

- **EDCB** (EpgDataCap_Bon / EpgTimerSrv) がセットアップ済みで稼働中であること。
  EpgTimerSrv の **CtrlCmd TCP サーバーを有効** にすること (既定ポート 4510)。
- **Python 3.11 以上** (ブリッジ用)。
- **Jellyfin 10.11 系** (プラグインは 10.11.6 でビルド・確認)。
- ブリッジは EDCB と同じ PC で動かす想定 (NetworkTV ストリーム中継のため
  CtrlCmd を TCP モードで使用する)。

## 1. Python ブリッジのセットアップ

```
cd jf-dvr
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

`config.example.toml` を `config.toml` にコピーして環境に合わせて編集する。

疎通確認:

```
.venv\Scripts\python tools\edcb_check.py     # EDCB と通信できるか
.venv\Scripts\python tools\selftest.py       # 各エンドポイントの自己診断
```

常駐化 (Windows タスクスケジューラに登録):

```
powershell -NoProfile -File tools\install_service.ps1
```

これで 2 つのタスクが登録される。

- **jf-dvr-bridge** — ブリッジ本体。SYSTEM 権限・システム起動時に開始 (常時稼働)。
- **jf-dvr-tray** — タスクトレイ常駐アイコン。ログオン中のユーザーのデスクトップ
  セッションで動き、ブリッジの稼働状態をトレイに表示する (緑 = 稼働中 / 灰 = 停止)。
  アイコンのメニューから Web UI やログを開ける。

手動起動する場合は `.venv\Scripts\python run.py`
(トレイ単体は `.venv\Scripts\pythonw.exe tools\tray.py`)。

## 2. Jellyfin プラグインのセットアップ

### ビルド済みパッケージを使う場合 (推奨)

`release/jf-dvr-plugin_1.0.1.0/` がビルド済みパッケージ。フォルダごと
Jellyfin のプラグインフォルダにコピーするだけでよい:

```
%ProgramData%\Jellyfin\Server\plugins\jf-dvr-plugin_1.0.1.0\
```

詳細な手順は [`release/INSTALL.md`](release/INSTALL.md) を参照。

### ソースからビルドする場合 (.NET 9 SDK 以上が必要)

```
dotnet build plugin\Jellyfin.Plugin.JfDvr.csproj -c Release
```

`plugin\bin\Release\net9.0\Jellyfin.Plugin.JfDvr.dll` と `plugin\meta.json` を
Jellyfin のプラグインフォルダにコピーする:

```
%ProgramData%\Jellyfin\Server\plugins\jf-dvr-plugin_1.0.1.0\
```

### 共通

Jellyfin を再起動するとプラグインが読み込まれる。ダッシュボード →
プラグイン → 「jf-dvr (EDCB Live TV)」が **Active** になっていることを確認し、
設定でブリッジ URL を確認する (既定 `http://127.0.0.1:40880`、ブリッジと
同一 PC ならそのままでよい)。

> 既に旧バージョンの `jf-dvr` プラグインフォルダがある場合は、二重ロードを
> 避けるため**先に削除**してから新しいフォルダを置くこと。

以後、Jellyfin の Live TV にチャンネル・番組表が現れ、番組表からの録画予約は
EDCB に登録される。M3U / XMLTV の設定は不要。

## config.toml

```toml
[edcb]
host = "127.0.0.1"   # EpgTimerSrv の接続先
port = 4510

[bridge]
host = "0.0.0.0"     # ブリッジが listen するアドレス
port = 40880

[epg]
days = 8             # EPG を何日先まで取得するか

[tuner]
# ライブ視聴が同時に使用できるチューナー数の上限 (種別ごと、0 = 無制限)。
live_max_isdbt = 1   # ISDB-T (地上波)
live_max_isdbs = 1   # ISDB-S (BS/CS)
# true の場合、EDCB が当該種別で録画中はライブ視聴を許可しない (録画を優先)。
block_live_while_recording = true
```

## REST / ストリーム エンドポイント (ブリッジ)

| メソッド・パス | 機能 |
|---|---|
| `GET /status` | EpgTimerSrv の動作状態 |
| `GET /channels` | チャンネル一覧 |
| `GET /epg?channel=&start=&end=` | 番組表 |
| `GET /reservations` / `POST` / `DELETE /{id}` | 録画予約の一覧・追加・削除 |
| `GET /recordings` | 録画済みファイルの一覧 |
| `GET /recordings/{id}/stream` | 録画ファイルの再生 |
| `GET /stream/{channel}` | ライブ TS ストリーム (NetworkTV) |
| `GET /docs` | 動作確認用 Swagger UI |

`{channel}` は `onid-tsid-sid` 形式 (例: `32736-32736-1024`)。

## ディレクトリ

| パス | 内容 |
|---|---|
| `edcb/` | EDCB CtrlCmd クライアント (vendored、`edcb/NOTICE` 参照) |
| `bridge/` | ブリッジ本体 (config / models / genre / gateway / streaming / server) |
| `plugin/` | C# Jellyfin プラグイン (ILiveTvService) |
| `tools/` | 補助スクリプト (疎通確認・自己診断・常駐化・トレイ・デプロイ) |
| `release/` | ビルド済みプラグインの配布物 ([`release/INSTALL.md`](release/INSTALL.md)) |
| `run.py` | ブリッジ起動エントリポイント |

## ライセンス

`edcb/` ディレクトリは [KonomiTV](https://github.com/tsukumijima/KonomiTV)
(MIT License) の EDCB クライアント実装を vendoring したもの。詳細と改変点は
[`edcb/NOTICE`](edcb/NOTICE) を参照。

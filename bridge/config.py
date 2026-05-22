from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic_core import Url


def _find_ffmpeg() -> str:
    """ ffmpeg.exe を既知の場所から探す。見つからなければ空文字。 """
    program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
    candidates = [
        Path(program_files) / 'Jellyfin' / 'Server' / 'ffmpeg.exe',
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return ''


class Config:
    """ config.toml を読み込むブリッジ設定。 """

    def __init__(self, path: str | Path) -> None:
        data = tomllib.loads(Path(path).read_text(encoding='utf-8'))
        edcb = data.get('edcb', {})
        bridge = data.get('bridge', {})
        epg = data.get('epg', {})
        tuner = data.get('tuner', {})

        # EDCB (EpgTimerSrv) の CtrlCmd TCP 接続先
        self.edcb_host: str = edcb.get('host', '127.0.0.1')
        self.edcb_port: int = int(edcb.get('port', 4510))
        # EDCB のインストールフォルダ (LogoData の読み取りに使う)
        self.edcb_folder: str = edcb.get('folder', '')
        # tsreadex (TS 整形ツール)。EDCB の Tools フォルダから自動導出する。
        self.tsreadex_path: str = (
            str(Path(self.edcb_folder) / 'Tools' / 'tsreadex.exe')
            if self.edcb_folder else ''
        )

        # ブリッジ自身の listen 設定
        self.bridge_host: str = bridge.get('host', '0.0.0.0')
        self.bridge_port: int = int(bridge.get('port', 40880))
        # ライブ視聴の remux に使う ffmpeg。未指定なら既知の場所から自動検出する。
        self.ffmpeg_path: str = bridge.get('ffmpeg_path', '') or _find_ffmpeg()

        # EPG を何日先まで取得するか
        self.epg_days: int = int(epg.get('days', 8))

        # ライブ視聴のチューナー使用上限 (種別ごと、0 = 無制限)
        self.live_max_isdbt: int = int(tuner.get('live_max_isdbt', 1))
        self.live_max_isdbs: int = int(tuner.get('live_max_isdbs', 1))
        # true の場合、EDCB が当該種別で録画中はライブ視聴を許可しない
        self.block_live_while_recording: bool = bool(
            tuner.get('block_live_while_recording', True))

        # メインと同一番組のサブチャンネルを一覧から隠す
        channels = data.get('channels', {})
        self.hide_simulcast_subchannels: bool = bool(
            channels.get('hide_simulcast_subchannels', True))

    @property
    def edcb_url(self) -> Url:
        """ vendored edcb パッケージに渡す接続先 URL (tcp://host:port/)。 """
        return Url(f'tcp://{self.edcb_host}:{self.edcb_port}/')


def load_config(path: str | Path | None = None) -> Config:
    """ config.toml を読み込む。path 省略時はプロジェクト直下の config.toml。 """
    if path is None:
        path = Path(__file__).resolve().parent.parent / 'config.toml'
    return Config(path)

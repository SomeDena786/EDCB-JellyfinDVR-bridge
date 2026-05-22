"""jf-dvr ブリッジのタスクトレイ常駐アイコン。

ブリッジ本体 (run.py) は Windows のタスクスケジューラから SYSTEM 権限・
セッション 0 で動くため、デスクトップにアイコンを出せない。このスクリプトは
ユーザーのログオン時に別途起動し、ブリッジの稼働状態をタスクトレイに表示する
軽量モニタとして動く。ブリッジ本体とは独立しており、ここを終了しても
ブリッジ (録画・配信) は止まらない。

    .venv\\Scripts\\pythonw.exe tools\\tray.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# tools/ の親 (プロジェクト直下) を import パスに加え、bridge.config を使えるようにする
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import pystray
from PIL import Image, ImageDraw

from bridge.config import load_config

_config = load_config()
_BRIDGE_URL = f'http://127.0.0.1:{_config.bridge_port}'
_LOG_PATH = _ROOT / 'bridge.log'

# 稼働状態のポーリング間隔 (秒)
_POLL_INTERVAL = 10

# 二重起動防止ロックに使う 127.0.0.1 のポート
_LOCK_PORT = 40881

_online = False

# 二重起動防止用ソケット (プロセス終了まで保持してポートを占有し続ける)
_lock_socket: socket.socket | None = None


def _acquire_single_instance() -> bool:
    """二重起動を防ぐ。既にトレイが起動済みなら False を返す。

    127.0.0.1 の固定ポートを bind して占有することで実現する。プロセスが
    終了すればポートは OS が解放するので、後始末は不要。
    """
    global _lock_socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR は付けない。Windows では付けると二重 bind が通ってしまう。
    try:
        sock.bind(('127.0.0.1', _LOCK_PORT))
    except OSError:
        sock.close()
        return False
    _lock_socket = sock
    return True


def _make_icon(online: bool) -> Image.Image:
    """ブリッジの稼働状態を表すトレイアイコンを描く (緑=稼働中, 灰=停止)。"""
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (40, 167, 69) if online else (128, 128, 128)
    # アンテナ
    draw.line([(32, 16), (18, 4)], fill=color, width=4)
    draw.line([(32, 16), (46, 4)], fill=color, width=4)
    # テレビ画面 (角丸の枠)
    draw.rounded_rectangle([(8, 16), (56, 52)], radius=6, outline=color, width=4)
    # 画面内のドット (稼働中は点灯を表す)
    draw.ellipse([(28, 30), (36, 38)], fill=color)
    # 脚
    draw.line([(22, 52), (18, 60)], fill=color, width=4)
    draw.line([(42, 52), (46, 60)], fill=color, width=4)
    return img


def _check_online() -> bool:
    """ブリッジの /status に到達できるか確認する。"""
    try:
        with urllib.request.urlopen(f'{_BRIDGE_URL}/status', timeout=4) as resp:
            return resp.status == 200
    except Exception:
        return False


def _status_text(_item) -> str:
    return '状態: 稼働中' if _online else '状態: 停止 / 応答なし'


def _open_web_ui(_icon, _item) -> None:
    webbrowser.open(f'{_BRIDGE_URL}/docs')


def _open_log(_icon, _item) -> None:
    if _LOG_PATH.is_file():
        os.startfile(_LOG_PATH)  # noqa: S606 - ローカルログを既定アプリで開くだけ
    else:
        webbrowser.open(f'{_BRIDGE_URL}/status')


def _quit(icon, _item) -> None:
    icon.stop()


def _poll_loop(icon: pystray.Icon) -> None:
    """定期的に稼働状態を確認し、アイコンとツールチップを更新する。"""
    global _online
    while True:
        online = _check_online()
        if online != _online or icon.icon is None:
            _online = online
            icon.icon = _make_icon(online)
            icon.title = (
                f'jf-dvr bridge — 稼働中 ({_BRIDGE_URL})'
                if online else
                'jf-dvr bridge — 停止 / 応答なし'
            )
            icon.update_menu()
        time.sleep(_POLL_INTERVAL)


def main() -> None:
    if not _acquire_single_instance():
        # 既にトレイが起動済み。静かに終了する。
        return

    menu = pystray.Menu(
        pystray.MenuItem('jf-dvr bridge', None, enabled=False),
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Web UI を開く', _open_web_ui, default=True),
        pystray.MenuItem('ログを開く', _open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('トレイ常駐を終了', _quit),
    )
    icon = pystray.Icon(
        'jf-dvr',
        icon=_make_icon(False),
        title='jf-dvr bridge',
        menu=menu,
    )
    thread = threading.Thread(target=_poll_loop, args=(icon,), daemon=True)
    thread.start()
    icon.run()


if __name__ == '__main__':
    main()

"""EDCB の LogoData から局ロゴを引く。

EDCB はインストールフォルダ配下の Setting\\LogoData.ini にサービスとロゴ識別の
対応を、Setting\\LogoData フォルダにロゴ画像 (PNG/BMP) を保存する。
ロゴファイル名は {network_id:04X}_{logo_id:03X}_{連番:03d}_{種別:02d}.{ext}。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from edcb.EDCBUtil import EDCBUtil

_PNG_MAGIC = b'\x89PNG'
_BMP_MAGIC = b'BM'

# LogoData の再読み込み間隔 (秒)。ロゴはめったに変わらない。
_CACHE_TTL = 600.0


class LogoStore:
    """ EDCB の LogoData を読み、(onid, sid) からロゴファイルを引く。 """

    def __init__(self, edcb_folder: str) -> None:
        self._setting_dir = Path(edcb_folder) / 'Setting' if edcb_folder else None
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._ini_text = ''
        self._files: list[str] = []

    def _refresh(self) -> None:
        if self._loaded_at > 0 and time.monotonic() - self._loaded_at < _CACHE_TTL:
            return
        with self._lock:
            if self._loaded_at > 0 and time.monotonic() - self._loaded_at < _CACHE_TTL:
                return
            ini_text = ''
            files: list[str] = []
            if self._setting_dir is not None:
                try:
                    ini_text = EDCBUtil.convertBytesToString(
                        (self._setting_dir / 'LogoData.ini').read_bytes())
                except Exception:
                    ini_text = ''
                try:
                    files = [p.name for p in (self._setting_dir / 'LogoData').iterdir()
                             if p.is_file()]
                except Exception:
                    files = []
            self._ini_text = ini_text
            self._files = files
            self._loaded_at = time.monotonic()

    def has_logo(self, onid: int, sid: int) -> bool:
        return self.find(onid, sid) is not None

    def find(self, onid: int, sid: int) -> tuple[Path, str] | None:
        """ (ロゴファイルパス, content-type) を返す。見つからなければ None。 """
        self._refresh()
        if self._setting_dir is None or not self._ini_text:
            return None

        logo_id = EDCBUtil.getLogoIDFromLogoDataIni(self._ini_text, onid, sid)
        if logo_id < 0:
            return None

        prefix = f'{onid:04X}_{logo_id:03X}_'
        # prefix 一致のファイルを集め、PNG 優先・種別の大きい順で選ぶ
        candidates: list[tuple[bool, int, str]] = []
        for name in self._files:
            if not name.upper().startswith(prefix):
                continue
            parts = name.rsplit('.', 1)[0].split('_')
            if len(parts) < 4:
                continue
            try:
                logo_type = int(parts[3])
            except ValueError:
                continue
            candidates.append((name.lower().endswith('.png'), logo_type, name))
        if not candidates:
            return None

        candidates.sort(reverse=True)
        path = self._setting_dir / 'LogoData' / candidates[0][2]

        try:
            head = path.read_bytes()[:8]
        except Exception:
            return None
        if head.startswith(_PNG_MAGIC):
            return path, 'image/png'
        if head.startswith(_BMP_MAGIC):
            return path, 'image/bmp'
        return path, 'application/octet-stream'

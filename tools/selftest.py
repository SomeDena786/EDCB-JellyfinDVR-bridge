"""ブリッジ自己診断スクリプト。

bridge を一時的に起動し、主要エンドポイントを順に叩いて結果を表示する。
取得したレスポンス JSON は プロジェクト直下に t_*.json として保存する。

実行例 (プロジェクト直下から):
    .venv\\Scripts\\python tools\\selftest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 端末/パイプに関わらず UTF-8 で出力する
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.config import load_config  # noqa: E402

PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
STARTUP_WAIT = 8.0


def fetch(base: str, path: str) -> tuple[int | None, bytes]:
    try:
        with urllib.request.urlopen(base + path, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        return None, repr(exc).encode()


def main() -> int:
    config = load_config()
    base = f'http://127.0.0.1:{config.bridge_port}'

    out_log = (ROOT / 'bridge.out.log').open('wb')
    err_log = (ROOT / 'bridge.err.log').open('wb')
    proc = subprocess.Popen(
        [str(PYTHON), 'run.py'],
        cwd=str(ROOT),
        stdout=out_log,
        stderr=err_log,
    )
    try:
        time.sleep(STARTUP_WAIT)
        if proc.poll() is not None:
            print(f'ブリッジが起動直後に終了しました (exit={proc.returncode})')
            return 1

        # /status
        code, body = fetch(base, '/status')
        (ROOT / 't_status.json').write_bytes(body)
        print(f'GET /status -> {code}')
        print('  ' + body.decode('utf-8', 'replace'))

        # /channels
        code, body = fetch(base, '/channels')
        (ROOT / 't_channels.json').write_bytes(body)
        channels = json.loads(body) if code == 200 else []
        print(f'GET /channels -> {code}, {len(channels)} channels')
        for ch in channels[:10]:
            print(f"  {ch['id']:>16}  [{ch['network_type']}] {ch['name']}")

        # /epg (先頭チャンネル)
        if channels:
            cid = channels[0]['id']
            code, body = fetch(base, f'/epg?channel={cid}')
            (ROOT / 't_epg.json').write_bytes(body)
            programs = json.loads(body) if code == 200 else []
            print(f'GET /epg?channel={cid} -> {code}, {len(programs)} programs')
            for pg in programs[:3]:
                print(f"  {pg['start']}  {pg['title']}")

        # /reservations
        code, body = fetch(base, '/reservations')
        (ROOT / 't_reservations.json').write_bytes(body)
        reservations = json.loads(body) if code == 200 else []
        print(f'GET /reservations -> {code}, {len(reservations)} reservations')

        # /recordings
        code, body = fetch(base, '/recordings')
        (ROOT / 't_recordings.json').write_bytes(body)
        recordings = json.loads(body) if code == 200 else []
        print(f'GET /recordings -> {code}, {len(recordings)} recordings')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out_log.close()
        err_log.close()

    err_text = (ROOT / 'bridge.err.log').read_text(encoding='utf-8', errors='replace')
    if err_text.strip():
        print('=== bridge.err.log ===')
        print(err_text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

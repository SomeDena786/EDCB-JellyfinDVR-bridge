"""jf-dvr ブリッジの起動エントリポイント。

    .venv\\Scripts\\python run.py
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from bridge.config import load_config
from bridge.server import app


def main() -> None:
    # Windows で asyncio サブプロセス (tsreadex) を使うため ProactorEventLoop を選ぶ
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    config = load_config()
    print(f'jf-dvr bridge を起動します: http://{config.bridge_host}:{config.bridge_port}')
    print(f'EDCB 接続先: {config.edcb_host}:{config.edcb_port}')
    uvicorn.run(app, host=config.bridge_host, port=config.bridge_port, log_level='info')


if __name__ == '__main__':
    main()

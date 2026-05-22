"""EDCB (EpgTimerSrv) への CtrlCmd 疎通確認スクリプト。

config.toml の [edcb] 接続先に対して、サーバー状態の取得とサービス
(チャンネル) 一覧の取得を試し、結果を表示する。

実行例 (プロジェクト直下から):
    .venv\\Scripts\\python tools\\edcb_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# プロジェクト直下を import パスに追加する
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.config import load_config
from edcb import set_edcb_url
from edcb.CtrlCmdUtil import CtrlCmdUtil
from edcb.EDCBUtil import EDCBUtil


async def main() -> int:
    config = load_config()
    print(f'EDCB 接続先: {config.edcb_host}:{config.edcb_port}')

    # vendored edcb パッケージ全体が参照するグローバル接続先を設定する
    set_edcb_url(config.edcb_url)

    # まず EpgTimerSrv の動作ステータスを確認する
    status = await EDCBUtil.getEDCBStatus(config.edcb_url)
    print(f'EpgTimerSrv ステータス: {status}')
    if status == 'Unknown':
        print()
        print('  EpgTimerSrv に接続できませんでした。以下を確認してください:')
        print('   - EpgTimerSrv (EpgTimer) が起動しているか')
        print('   - EpgTimer の設定「ネットワーク」で外部接続/TCP サーバーが有効か')
        print('   - config.toml の host / port が EpgTimerSrv の設定と一致しているか')
        return 1

    # サービス (チャンネル) 一覧を取得する
    edcb = CtrlCmdUtil(config.edcb_url)
    edcb.setConnectTimeOutSec(10)
    services = await edcb.sendEnumService()
    if services is None:
        print('EnumService に失敗しました。')
        return 1

    print(f'サービス数: {len(services)}')
    for service in services[:30]:
        print(
            f"  {service['onid']:5d}-{service['tsid']:5d}-{service['sid']:5d}"
            f"  type={service['service_type']:3d}"
            f"  {service['network_name']} / {service['service_name']}"
        )
    if len(services) > 30:
        print(f'  ... 他 {len(services) - 30} 件')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))

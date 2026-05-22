"""ライブ TS ストリーミング。

EDCB の NetworkTV モードで取得した生 TS は ISDB の多サービス TS のため、
ffmpeg がそのままでは映像を正しく復号できないことがある。tsreadex に通して
指定サービスのみの整形済み TS にしてから配信する。

ライブ視聴が終わったら NetworkTV の EpgDataCap_Bon を必ず終了してチューナーを
解放する。前回のクラッシュ等で閉じ損ねたチューナーは起動時に掃除する。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from edcb.CtrlCmdUtil import CtrlCmdUtil
from edcb.EDCBTuner import EDCBTuner

# 1 回の読み出しサイズ。188 (TS パケット) × 256
READ_CHUNK = 48128

# tsreadex 実行ファイルのパス (サーバー起動時に設定)
_tsreadex_path: str = ''


def set_tsreadex_path(path: str) -> None:
    """ tsreadex.exe のパスを設定する。空または存在しない場合は素通しになる。 """
    global _tsreadex_path
    _tsreadex_path = path or ''


# 起動中の NetworkTV チューナー ID を記録するファイル (クラッシュ後の掃除用)
_STATE_FILE = Path(__file__).resolve().parent.parent / 'nwtv_active.json'
_state_lock = asyncio.Lock()
_active_ids: set[int] = set()

# 切り離したチューナー解放タスクの参照を保持する (GC による消滅を防ぐ)
_cleanup_tasks: set = set()

# ライブ視聴で現在使用中のチューナー数 (種別ごと)
_live_lock = asyncio.Lock()
_live_counts: dict[str, int] = {'isdbt': 0, 'isdbs': 0}


def schedule_cleanup(session) -> None:
    """ チューナー解放を、リクエストのライフサイクルから切り離した独立タスクで実行する。 """
    try:
        task = asyncio.get_running_loop().create_task(session.aclose())
    except RuntimeError:
        return
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


async def _persist_state() -> None:
    try:
        _STATE_FILE.write_text(json.dumps(sorted(_active_ids)), encoding='utf-8')
    except Exception:
        pass


async def _mark_active(nwtv_id: int) -> None:
    async with _state_lock:
        _active_ids.add(nwtv_id)
        await _persist_state()


async def _mark_inactive(nwtv_id: int) -> None:
    async with _state_lock:
        _active_ids.discard(nwtv_id)
        await _persist_state()


async def cleanup_orphan_tuners() -> None:
    """ 前回起動時に閉じ損ねた NetworkTV チューナーを終了する (ブリッジ起動時に呼ぶ)。 """
    try:
        orphan_ids = json.loads(_STATE_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return
    except Exception:
        orphan_ids = []
    cmd = CtrlCmdUtil()
    cmd.setConnectTimeOutSec(10)
    for nwtv_id in orphan_ids:
        try:
            await cmd.sendNwTVIDClose(int(nwtv_id))
        except Exception:
            pass
    try:
        _STATE_FILE.unlink()
    except Exception:
        pass


class LiveStreamSession:
    """ チューナー・生 TS・tsreadex を束ねたライブ視聴セッション。 """

    def __init__(self, tuner: EDCBTuner, reader, owner_id: str, kind: str, sid: int) -> None:
        self._tuner = tuner
        self._reader = reader          # EDCB NetworkTV の生 TS
        self._owner_id = owner_id
        self._kind = kind
        self._sid = sid
        self._nwtv_id = tuner.getEDCBNetworkTVID()
        self._closed = False
        self._proc: asyncio.subprocess.Process | None = None
        self._pump_task: asyncio.Task | None = None

    async def start_tsreadex(self) -> None:
        """ tsreadex を起動し、生 TS を流し込むポンプタスクを開始する。

        tsreadex が使えない場合は素通し (生 TS を直接配信) になる。
        """
        if not _tsreadex_path or not Path(_tsreadex_path).is_file():
            return
        try:
            self._proc = await asyncio.create_subprocess_exec(
                _tsreadex_path, '-n', str(self._sid), '-',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f'[jf-dvr] tsreadex 起動失敗、素通しに切替: {exc!r}', flush=True)
            self._proc = None
            return
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        """ EDCB の生 TS を tsreadex の標準入力へ流し込む。 """
        try:
            while True:
                chunk = await self._reader.read(READ_CHUNK)
                if not chunk:
                    break
                self._proc.stdin.write(chunk)
                await self._proc.stdin.drain()
        except Exception:
            pass
        finally:
            try:
                self._proc.stdin.close()
            except Exception:
                pass

    async def iter_ts(self) -> AsyncIterator[bytes]:
        """ 整形済み TS (tsreadex の出力。無ければ生 TS) を読み出して配信する。 """
        source = self._proc.stdout if self._proc is not None else self._reader
        try:
            while True:
                chunk = await source.read(READ_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            # 後始末は独立タスクに逃がす (この generator がキャンセル/GC されても
            # 確実にチューナーを解放するため)
            schedule_cleanup(self)

    async def aclose(self) -> None:
        """ tsreadex を止め、NetworkTV の EpgDataCap_Bon を終了してチューナーを解放する。 """
        if self._closed:
            return
        self._closed = True
        print(f'[jf-dvr] ライブ視聴終了、チューナー解放を開始: nwtv_id={self._nwtv_id}', flush=True)

        # ポンプタスクと tsreadex を止める
        if self._pump_task is not None:
            self._pump_task.cancel()
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                pass

        # NetworkTV の EpgDataCap_Bon を終了する (close を先に行い、各 await は
        # タイムアウトで打ち切る。EDCBTuner.disconnect はハングし得るため)。
        closed = False
        for _ in range(3):
            try:
                if await asyncio.wait_for(
                        self._tuner.close(self._owner_id, force=True), timeout=12):
                    closed = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

        try:
            await asyncio.wait_for(self._tuner.disconnect(self._owner_id), timeout=5)
        except Exception:
            pass

        if closed:
            await _mark_inactive(self._nwtv_id)

        # ライブ視聴のチューナー使用数を戻す
        async with _live_lock:
            _live_counts[self._kind] = max(0, _live_counts.get(self._kind, 0) - 1)

        print(f'[jf-dvr] チューナー解放: nwtv_id={self._nwtv_id} '
              f'kind={self._kind} closed={closed}', flush=True)


async def open_live_stream(
    onid: int, tsid: int, sid: int, kind: str, cap: int,
) -> LiveStreamSession | None:
    """ 指定サービスの NetworkTV チューナーを起動し、ストリームを開く。

    kind は 'isdbt' / 'isdbs'。cap はその種別のライブ視聴チューナー数上限 (0 = 無制限)。
    上限到達・空きチューナー無し・EDCB 無応答のいずれかの場合は None を返す。
    """
    # ライブ視聴枠を確保する (種別ごとの上限チェック)
    async with _live_lock:
        if cap > 0 and _live_counts.get(kind, 0) >= cap:
            print(f'[jf-dvr] ライブ視聴上限に到達: kind={kind} cap={cap}', flush=True)
            return None
        _live_counts[kind] = _live_counts.get(kind, 0) + 1

    session: LiveStreamSession | None = None
    owner_id = uuid.uuid4().hex
    tuner = EDCBTuner.getOrCreate(owner_id)

    # EDCBTuner.setChannel の引数順は (network_id, service_id, transport_stream_id, owner)
    if await tuner.setChannel(onid, sid, tsid, owner_id):
        tuner.lock(owner_id)
        reader = await tuner.connect(owner_id)
        if reader is not None:
            await _mark_active(tuner.getEDCBNetworkTVID())
            session = LiveStreamSession(tuner, reader, owner_id, kind, sid)
            await session.start_tsreadex()
            print(f'[jf-dvr] ライブ視聴開始: nwtv_id={tuner.getEDCBNetworkTVID()} '
                  f'kind={kind} ch={onid}-{tsid}-{sid}', flush=True)

    if session is None:
        await tuner.close(owner_id, force=True)
        async with _live_lock:
            _live_counts[kind] = max(0, _live_counts.get(kind, 0) - 1)

    return session

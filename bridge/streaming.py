"""ライブ TS ストリーミング。

EDCB の NetworkTV モードで取得した生 TS は ISDB の多サービス TS のため、
Jellyfin 側の ffmpeg がそのまま扱うとストリーム解析 (find_stream_info) に
非常に長い時間がかかる。疎な字幕・データ・EIT のストリームを待ち続けるため、
ライブ視聴の頭が数分固まったように見える。

そこで配信前に 2 段のパイプラインで TS を整える:
  1. tsreadex — 指定サービスのみを抽出し、ISDB 特有の TS を ffmpeg 向けに整形。
  2. ffmpeg   — 映像+主音声のみへ remux する。Jellyfin の ffmpeg が即座に
                解析できる単純な mpegts にする (再エンコードはしない copy)。

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

# 外部ツールのパス (サーバー起動時に設定)
_tsreadex_path: str = ''
_ffmpeg_path: str = ''


def set_tsreadex_path(path: str) -> None:
    """ tsreadex.exe のパスを設定する。空または存在しない場合はこの段を飛ばす。 """
    global _tsreadex_path
    _tsreadex_path = path or ''


def set_ffmpeg_path(path: str) -> None:
    """ ffmpeg.exe のパスを設定する。空または存在しない場合はこの段を飛ばす。 """
    global _ffmpeg_path
    _ffmpeg_path = path or ''


# 起動中の NetworkTV チューナー ID を記録するファイル (クラッシュ後の掃除用)
_STATE_FILE = Path(__file__).resolve().parent.parent / 'nwtv_active.json'
_state_lock = asyncio.Lock()
_active_ids: set[int] = set()

# 切り離したチューナー解放タスクの参照を保持する (GC による消滅を防ぐ)
_cleanup_tasks: set = set()

# ライブ視聴で現在使用中のチューナー数 (種別ごと)
_live_lock = asyncio.Lock()
_live_counts: dict[str, int] = {'isdbt': 0, 'isdbs': 0, 'bs4k': 0}


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
    """ チューナー・生 TS・整形パイプラインを束ねたライブ視聴セッション。 """

    def __init__(self, tuner: EDCBTuner, reader, owner_id: str, kind: str, sid: int) -> None:
        self._tuner = tuner
        self._reader = reader          # EDCB NetworkTV の生 TS
        self._owner_id = owner_id
        self._kind = kind
        self._sid = sid
        self._nwtv_id = tuner.getEDCBNetworkTVID()
        self._closed = False
        self._procs: list[asyncio.subprocess.Process] = []
        self._tasks: list[asyncio.Task] = []
        # 配信元。パイプライン構築後に最終段の出力へ差し替える。
        self._output = reader

    async def start_pipeline(self) -> None:
        """ tsreadex → ffmpeg のパイプラインを起動する。

        各ツールが使えない場合はその段を飛ばす (両方無ければ生 TS を素通し)。
        BS4K は dantto4k がすでに単一サービスの TS にしているうえ、ブリッジ側
        パイプラインの処理速度が HEVC の高ビットレートに追いつかないと
        EpgDataCap_Bon → BonDriver_dantto4k に backpressure がかかってクラッシュ
        するため、パイプラインを素通しにする (プラグイン側の AnalyzeDurationMs で
        Jellyfin の ffmpeg 解析は別途短縮済み)。
        """
        if self._kind == 'bs4k':
            self._output = self._reader
            return

        source = self._reader

        # 段1: tsreadex。指定サービスのみ抽出し ISDB の TS を整形する。
        if _tsreadex_path and Path(_tsreadex_path).is_file():
            tsr = await self._spawn(
                _tsreadex_path,
                # EIT/SDTT/BIT を除去してストリーム解析を軽くする
                '-x', '18/38/39',
                # 指定サービスのみ抽出する
                '-n', str(self._sid),
                # 主音声を常に連続して存在させる (無ければ無音 AAC を生成)
                '-a', '13',
                # 標準入力から読む
                '-',
            )
            if tsr is not None:
                self._tasks.append(asyncio.create_task(self._pump(source, tsr.stdin)))
                source = tsr.stdout

        # 段2: ffmpeg。映像+主音声のみへ remux し、Jellyfin が即解析できる TS にする。
        if _ffmpeg_path and Path(_ffmpeg_path).is_file():
            ff = await self._spawn(
                _ffmpeg_path,
                '-hide_banner', '-loglevel', 'error',
                # 壊れたパケットは捨て、デコード/パースエラーは無視する
                # (BS4K dantto4k のように初期 TS が乱れがちなチューナー対策)
                '-fflags', '+discardcorrupt',
                '-err_detect', 'ignore_err',
                # 解析時間に多少余裕を持たせる (HEVC/BS4K の頭出しに少しかかる場合があるため)
                '-analyzeduration', '5000000', '-probesize', '10000000',
                '-i', 'pipe:0',
                # 映像と主音声のみ取り出す。字幕・データ・EIT は捨てる。
                '-map', '0:v:0?', '-map', '0:a:0?',
                '-c', 'copy',
                '-f', 'mpegts', 'pipe:1',
            )
            if ff is not None:
                self._tasks.append(asyncio.create_task(self._pump(source, ff.stdin)))
                source = ff.stdout

        self._output = source

    async def _spawn(self, *args: str) -> asyncio.subprocess.Process | None:
        """ 外部プロセスを起動する。失敗したら None を返してその段を飛ばす。 """
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f'[jf-dvr] プロセス起動失敗 ({Path(args[0]).name}): {exc!r}', flush=True)
            return None
        self._procs.append(proc)
        return proc

    @staticmethod
    async def _pump(src, dst) -> None:
        """ src から読んで dst へ書き込む。EOF または失敗で dst を閉じる。 """
        try:
            while True:
                chunk = await src.read(READ_CHUNK)
                if not chunk:
                    break
                dst.write(chunk)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass

    async def iter_ts(self) -> AsyncIterator[bytes]:
        """ 整形済み TS (パイプライン最終段の出力) を読み出して配信する。 """
        try:
            while True:
                chunk = await self._output.read(READ_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            # 後始末は独立タスクに逃がす (この generator がキャンセル/GC されても
            # 確実にチューナーを解放するため)
            schedule_cleanup(self)

    async def aclose(self) -> None:
        """ パイプラインを止め、NetworkTV を終了してチューナーを解放する。 """
        if self._closed:
            return
        self._closed = True
        print(f'[jf-dvr] ライブ視聴終了、チューナー解放を開始: nwtv_id={self._nwtv_id}', flush=True)

        # ポンプタスクと外部プロセス (tsreadex / ffmpeg) を止める
        for task in self._tasks:
            task.cancel()
        for proc in self._procs:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
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
            await session.start_pipeline()
            print(f'[jf-dvr] ライブ視聴開始: nwtv_id={tuner.getEDCBNetworkTVID()} '
                  f'kind={kind} ch={onid}-{tsid}-{sid}', flush=True)

    if session is None:
        await tuner.close(owner_id, force=True)
        async with _live_lock:
            _live_counts[kind] = max(0, _live_counts.get(kind, 0) - 1)

    return session

"""jf-dvr ブリッジの FastAPI アプリケーション。

C# Jellyfin プラグインが消費する REST API と、ライブ TS / 録画再生の
ストリーミングエンドポイントを提供する。
"""

from __future__ import annotations

import datetime
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from bridge.config import load_config
from bridge.edcb_gateway import EDCBError, EDCBGateway, network_type, parse_channel_id
from bridge.models import (
    AddReservationRequest,
    Channel,
    Program,
    Recording,
    Reservation,
)
from bridge.streaming import (
    cleanup_orphan_tuners,
    open_live_stream,
    schedule_cleanup,
    set_ffmpeg_path,
    set_tsreadex_path,
)
from edcb.EDCBTuner import EDCBTuner

config = load_config()
gateway = EDCBGateway(config)
set_tsreadex_path(config.tsreadex_path)
set_ffmpeg_path(config.ffmpeg_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時: 前回のクラッシュ等で閉じ損ねた NetworkTV チューナーを掃除する
    await cleanup_orphan_tuners()
    yield
    # 終了時に起動中の NetworkTV チューナーをすべて閉じる
    await EDCBTuner.closeAll()


app = FastAPI(title='jf-dvr bridge', version='0.1.0', lifespan=lifespan)


@app.exception_handler(EDCBError)
async def _edcb_error_handler(request: Request, exc: EDCBError) -> JSONResponse:
    return JSONResponse(status_code=502, content={'detail': str(exc)})


def _parse_channel(channel: str) -> tuple[int, int, int]:
    try:
        return parse_channel_id(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _ensure_aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    # タイムゾーン無しの日時は UTC とみなす (Jellyfin は UTC を送る)
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


@app.get('/')
async def root() -> dict:
    return {
        'name': 'jf-dvr bridge',
        'version': '0.1.0',
        'endpoints': [
            '/status', '/channels', '/epg', '/reservations',
            '/recordings', '/stream/{channel}', '/recordings/{id}/stream',
        ],
    }


@app.get('/status')
async def get_status() -> dict:
    return {
        'edcb_status': await gateway.get_status(),
        'edcb_host': config.edcb_host,
        'edcb_port': config.edcb_port,
    }


@app.get('/channels', response_model=list[Channel])
async def get_channels() -> list[Channel]:
    return await gateway.list_channels()


@app.get('/channels/{channel}/logo')
async def get_channel_logo(channel: str):
    _parse_channel(channel)
    logo = gateway.get_logo(channel)
    if logo is None:
        raise HTTPException(status_code=404, detail='局ロゴが見つかりません')
    path, content_type = logo
    return FileResponse(path, media_type=content_type)


@app.get('/epg', response_model=list[Program])
async def get_epg(
    channel: str | None = None,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> list[Program]:
    return await gateway.get_epg(channel, _ensure_aware(start), _ensure_aware(end))


@app.get('/reservations', response_model=list[Reservation])
async def get_reservations() -> list[Reservation]:
    return await gateway.list_reservations()


@app.post('/reservations', response_model=Reservation)
async def post_reservation(req: AddReservationRequest) -> Reservation:
    return await gateway.add_reservation(req)


@app.delete('/reservations/{reserve_id}')
async def delete_reservation(reserve_id: int) -> dict:
    await gateway.delete_reservation(reserve_id)
    return {'deleted': reserve_id}


@app.get('/recordings', response_model=list[Recording])
async def get_recordings() -> list[Recording]:
    return await gateway.list_recordings()


@app.get('/recordings/{rec_id}/stream')
async def get_recording_stream(rec_id: int):
    recording = await gateway.get_recording(rec_id)
    if recording is None or not recording.file_path:
        raise HTTPException(status_code=404, detail='録画が見つかりません')
    if not os.path.isfile(recording.file_path):
        raise HTTPException(status_code=404, detail=f'録画ファイルが存在しません: {recording.file_path}')
    return FileResponse(recording.file_path, media_type='video/mp2t')


@app.get('/stream/{channel}')
async def get_live_stream(channel: str):
    onid, tsid, sid = _parse_channel(channel)
    # ネットワーク種別ごとにチューナー枠を分ける (BS4K は別物理チューナー)
    nt = network_type(onid)
    if nt == 'GR':
        kind, cap = 'isdbt', config.live_max_isdbt
    elif nt == 'BS4K':
        kind, cap = 'bs4k', config.live_max_bs4k
    else:
        kind, cap = 'isdbs', config.live_max_isdbs

    # EDCB が当該種別で録画中ならライブ視聴を断る (録画を優先する)
    if config.block_live_while_recording and await gateway.is_recording(kind):
        raise HTTPException(
            status_code=503,
            detail=f'EDCB が録画中のためライブ視聴できません (種別: {kind})',
        )

    session = await open_live_stream(onid, tsid, sid, kind, cap)
    if session is None:
        raise HTTPException(
            status_code=503,
            detail='チューナーを確保できませんでした (視聴上限に達したか、空きチューナーがありません)',
        )
    # クライアント切断時、独立タスクでチューナー解放を確実に実行する
    return StreamingResponse(
        session.iter_ts(),
        media_type='video/mp2t',
        background=BackgroundTask(schedule_cleanup, session),
    )

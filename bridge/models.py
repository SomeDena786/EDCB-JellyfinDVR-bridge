"""ブリッジ REST API のリクエスト/レスポンスモデル。

C# Jellyfin プラグインがこの JSON 契約を消費する。
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel


class Channel(BaseModel):
    """ チャンネル (EDCB のサービス1件)。 """
    id: str                       # "onid-tsid-sid"
    onid: int
    tsid: int
    sid: int
    name: str
    network_name: str
    network_type: str             # GR / BS / CS / OTHER
    service_type: int
    remote_control_key_id: int
    channel_number: str
    has_logo: bool = False


class Program(BaseModel):
    """ 番組表の1番組。 """
    id: str                       # "<channel_id>_<event_id>"
    channel_id: str
    event_id: int
    start: datetime.datetime
    end: datetime.datetime
    duration_sec: int
    title: str
    description: str
    extended: str
    genres: list[str]
    is_free: bool


class Reservation(BaseModel):
    """ EDCB の録画予約1件。 """
    id: int                       # EDCB の reserve_id
    channel_id: str
    onid: int
    tsid: int
    sid: int
    event_id: int
    title: str
    start: datetime.datetime
    duration_sec: int
    station_name: str
    enabled: bool
    comment: str
    rec_file_names: list[str]


class Recording(BaseModel):
    """ EDCB の録画済みファイル1件。 """
    id: int
    channel_id: str
    title: str
    start: datetime.datetime
    duration_sec: int
    service_name: str
    file_path: str
    drops: int
    scrambles: int
    rec_status: int


class AddReservationRequest(BaseModel):
    """ 録画予約の追加リクエスト。

    title / start / duration_sec を省略した場合は、onid/tsid/sid/event_id を
    手がかりに EDCB の EPG から番組情報を補完する。
    """
    onid: int
    tsid: int
    sid: int
    event_id: int
    title: str | None = None
    start: datetime.datetime | None = None
    duration_sec: int | None = None
    priority: int = 2             # EDCB の優先度 (1-5)
    rec_mode: int = 1             # 1 = 指定サービスのみ録画

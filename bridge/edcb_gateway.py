"""EDCB ゲートウェイ — vendored `edcb` パッケージを使って EDCB (EpgTimerSrv) と
通信し、ブリッジの API モデルに変換する層。
"""

from __future__ import annotations

import datetime

from edcb import EventInfo, RecFileInfo, ReserveData, ReserveDataRequired, ServiceInfo, set_edcb_url
from edcb.CtrlCmdUtil import JST, CtrlCmdUtil
from edcb.EDCBUtil import EDCBUtil

from bridge.config import Config
from bridge.genre import genre_names
from bridge.logo import LogoStore
from bridge.models import (
    AddReservationRequest,
    Channel,
    Program,
    Recording,
    Reservation,
)


class EDCBError(Exception):
    """ EDCB との通信または操作に失敗したときに送出する。 """


def make_channel_id(onid: int, tsid: int, sid: int) -> str:
    return f'{onid}-{tsid}-{sid}'


def parse_channel_id(value: str) -> tuple[int, int, int]:
    """ "onid-tsid-sid" 形式の文字列を (onid, tsid, sid) に分解する。 """
    parts = value.split('-')
    if len(parts) != 3:
        raise ValueError(f'不正なチャンネル ID: {value}')
    try:
        onid, tsid, sid = (int(part) for part in parts)
    except ValueError:
        raise ValueError(f'不正なチャンネル ID: {value}')
    return onid, tsid, sid


def network_type(onid: int) -> str:
    """ ONID からネットワーク種別 (GR/BS/CS/BS4K/OTHER) を判定する。 """
    if 0x7880 <= onid <= 0x7FFF:
        return 'GR'
    if onid == 0x0004:
        return 'BS'
    if onid in (0x0006, 0x0007):
        return 'CS'
    if onid == 0x000B:
        return 'BS4K'   # 新4K衛星放送 (BS4K)。dantto4k 等で TS 化されて入ってくる
    return 'OTHER'


def _to_channel(service: ServiceInfo) -> Channel:
    onid, tsid, sid = service['onid'], service['tsid'], service['sid']
    ntype = network_type(onid)
    remocon = service.get('remote_control_key_id', 0)
    number = str(remocon) if (ntype == 'GR' and remocon > 0) else str(sid)
    return Channel(
        id=make_channel_id(onid, tsid, sid),
        onid=onid,
        tsid=tsid,
        sid=sid,
        name=service['service_name'],
        network_name=service['network_name'],
        network_type=ntype,
        service_type=service['service_type'],
        remote_control_key_id=remocon,
        channel_number=number,
    )


def _to_program(event: EventInfo) -> Program | None:
    # 開始時刻・長さが不明な番組は番組表に置けないのでスキップ
    if 'start_time' not in event or 'duration_sec' not in event:
        return None
    start = event['start_time']
    duration = event['duration_sec']
    short_info = event.get('short_info') or {}
    ext_info = event.get('ext_info') or {}
    cid = make_channel_id(event['onid'], event['tsid'], event['sid'])

    genres = genre_names(event.get('content_info'))

    return Program(
        id=f"{cid}_{event['eid']}",
        channel_id=cid,
        event_id=event['eid'],
        start=start,
        end=start + datetime.timedelta(seconds=duration),
        duration_sec=duration,
        title=short_info.get('event_name', ''),
        description=short_info.get('text_char', ''),
        extended=ext_info.get('text_char', ''),
        genres=genres,
        is_free=event.get('free_ca_flag', 0) == 0,
    )


def _to_reservation(reserve: ReserveDataRequired) -> Reservation:
    # rec_mode 5 以上は「無効」状態の予約
    rec_mode = reserve.get('rec_setting', {}).get('rec_mode', 0)
    return Reservation(
        id=reserve['reserve_id'],
        channel_id=make_channel_id(reserve['onid'], reserve['tsid'], reserve['sid']),
        onid=reserve['onid'],
        tsid=reserve['tsid'],
        sid=reserve['sid'],
        event_id=reserve['eid'],
        title=reserve['title'],
        start=reserve['start_time'],
        duration_sec=reserve['duration_second'],
        station_name=reserve['station_name'],
        enabled=rec_mode < 5,
        comment=reserve['comment'],
        rec_file_names=reserve.get('rec_file_name_list', []),
    )


def _to_recording(rec: RecFileInfo) -> Recording:
    return Recording(
        id=rec['id'],
        channel_id=make_channel_id(rec['onid'], rec['tsid'], rec['sid']),
        title=rec['title'],
        start=rec['start_time'],
        duration_sec=rec['duration_sec'],
        service_name=rec['service_name'],
        file_path=rec['rec_file_path'],
        drops=rec['drops'],
        scrambles=rec['scrambles'],
        rec_status=rec['rec_status'],
    )


class EDCBGateway:
    """ EDCB (EpgTimerSrv) との高レベルなやり取りを提供する。 """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.logo = LogoStore(config.edcb_folder)
        # vendored edcb パッケージ全体が参照するグローバル接続先を設定する
        set_edcb_url(config.edcb_url)

    def _cmd(self) -> CtrlCmdUtil:
        cmd = CtrlCmdUtil(self.config.edcb_url)
        cmd.setConnectTimeOutSec(15)
        return cmd

    async def get_status(self) -> str:
        """ EpgTimerSrv の動作ステータス (Normal/Recording/EPGGathering/Unknown)。 """
        return await EDCBUtil.getEDCBStatus(self.config.edcb_url)

    async def is_recording(self, kind: str) -> bool:
        """ 指定種別 (isdbt / isdbs / bs4k) のチューナーで EDCB が録画中かどうかを返す。 """
        tuners = await self._cmd().sendEnumTunerProcess()
        if not tuners:
            return False
        for tuner in tuners:
            if not tuner.get('rec_flag'):
                continue
            nt = network_type(tuner['onid'])
            if nt == 'GR':
                tuner_kind = 'isdbt'
            elif nt == 'BS4K':
                tuner_kind = 'bs4k'
            else:
                tuner_kind = 'isdbs'
            if tuner_kind == kind:
                return True
        return False

    async def list_channels(self) -> list[Channel]:
        """ 映像サービス (service_type == 1) のチャンネル一覧を返す。

        hide_simulcast_subchannels が有効なら、メインと同一番組のサブチャンネルを除く。
        """
        services = await self._cmd().sendEnumService()
        if services is None:
            raise EDCBError('EnumService に失敗しました (EpgTimerSrv に接続できません)')
        tv_services = [s for s in services if s['service_type'] == 1]

        channels: list[Channel] = []
        for service in tv_services:
            channel = _to_channel(service)
            channel.has_logo = self.logo.has_logo(service['onid'], service['sid'])
            channels.append(channel)

        if self.config.hide_simulcast_subchannels:
            hidden = await self._find_simulcast_subchannels(tv_services)
            channels = [c for c in channels if c.id not in hidden]
        return channels

    def get_logo(self, channel_id: str):
        """ チャンネルの局ロゴ (ファイルパス, content-type) を返す。無ければ None。 """
        onid, tsid, sid = parse_channel_id(channel_id)
        return self.logo.find(onid, sid)

    async def _epg_keys_by_service(self, services: list) -> dict:
        """ 指定サービス群の番組表を取得し、(onid,tsid,sid) ごとの番組キー集合を返す。 """
        if not services:
            return {}
        now = datetime.datetime.now(JST)
        service_time_list: list[int] = []
        for s in services:
            service_time_list += [0, (s['onid'] << 32) | (s['tsid'] << 16) | s['sid']]
        service_time_list.append(EDCBUtil.datetimeToFileTime(now, JST))
        service_time_list.append(EDCBUtil.datetimeToFileTime(
            now + datetime.timedelta(days=self.config.epg_days), JST))

        result = await self._cmd().sendEnumPgInfoEx(service_time_list)
        keys: dict = {}
        for service_event in result or []:
            info = service_event['service_info']
            event_keys = set()
            for event in service_event['event_list']:
                if 'start_time' not in event:
                    continue
                title = (event.get('short_info') or {}).get('event_name', '')
                # タイトルの無い番組はサイマル判定の手がかりにならないので除外する
                if not title.strip():
                    continue
                event_keys.add((event['start_time'].isoformat(), title))
            keys[(info['onid'], info['tsid'], info['sid'])] = event_keys
        return keys

    async def _find_simulcast_subchannels(self, services: list) -> set[str]:
        """ メインと同一の番組表を持つサブチャンネルの channel id 集合を返す。 """
        groups: dict = {}
        for s in services:
            groups.setdefault((s['onid'], s['tsid']), []).append(s)
        multi = {k: v for k, v in groups.items() if len(v) > 1}
        if not multi:
            return set()

        target = [s for group in multi.values() for s in group]
        epg = await self._epg_keys_by_service(target)

        hidden: set[str] = set()
        for group in multi.values():
            # サービス ID の小さいものをメインとみなす
            ordered = sorted(group, key=lambda s: s['sid'])
            main = ordered[0]
            main_keys = epg.get((main['onid'], main['tsid'], main['sid']), set())
            for sub in ordered[1:]:
                sub_keys = epg.get((sub['onid'], sub['tsid'], sub['sid']), set())
                # サブ独自の番組 (タイトルがありメインに無いもの) が1つも無ければ隠す。
                # = サブの番組がすべてメインにも存在する、または番組情報が空。
                if sub_keys.issubset(main_keys):
                    hidden.add(make_channel_id(sub['onid'], sub['tsid'], sub['sid']))
        return hidden

    async def get_epg(
        self,
        channel: str | None = None,
        start: datetime.datetime | None = None,
        end: datetime.datetime | None = None,
    ) -> list[Program]:
        """ 番組表を取得する。channel 指定時はそのチャンネルのみ。 """
        cmd = self._cmd()

        if channel is not None:
            onid, tsid, sid = parse_channel_id(channel)
            target_ids = [(onid << 32) | (tsid << 16) | sid]
        else:
            services = await cmd.sendEnumService()
            if services is None:
                raise EDCBError('EnumService に失敗しました (EpgTimerSrv に接続できません)')
            target_ids = [
                (s['onid'] << 32) | (s['tsid'] << 16) | s['sid']
                for s in services
                if s['service_type'] == 1
            ]

        if start is None:
            start = datetime.datetime.now(JST)
        if end is None:
            end = start + datetime.timedelta(days=self.config.epg_days)

        # service_time_list: [mask, id, mask, id, ..., start_filetime, end_filetime]
        # mask = 0 は「サービス ID 完全一致」を意味する
        service_time_list: list[int] = []
        for sid_full in target_ids:
            service_time_list += [0, sid_full]
        service_time_list.append(EDCBUtil.datetimeToFileTime(start, JST))
        service_time_list.append(EDCBUtil.datetimeToFileTime(end, JST))

        result = await cmd.sendEnumPgInfoEx(service_time_list)
        if result is None:
            raise EDCBError('EnumPgInfoEx に失敗しました')

        programs: list[Program] = []
        for service_event in result:
            for event in service_event['event_list']:
                program = _to_program(event)
                if program is not None:
                    programs.append(program)
        return programs

    async def list_reservations(self) -> list[Reservation]:
        """ EDCB の録画予約一覧を返す。 """
        reserves = await self._cmd().sendEnumReserve()
        if reserves is None:
            raise EDCBError('EnumReserve に失敗しました')
        return [_to_reservation(r) for r in reserves]

    async def _find_event(
        self,
        cmd: CtrlCmdUtil,
        onid: int,
        tsid: int,
        sid: int,
        event_id: int,
    ) -> EventInfo | None:
        sid_full = (onid << 32) | (tsid << 16) | sid
        now = datetime.datetime.now(JST)
        service_time_list = [
            0,
            sid_full,
            EDCBUtil.datetimeToFileTime(now - datetime.timedelta(hours=1), JST),
            EDCBUtil.datetimeToFileTime(now + datetime.timedelta(days=self.config.epg_days), JST),
        ]
        result = await cmd.sendEnumPgInfoEx(service_time_list)
        if not result:
            return None
        for service_event in result:
            for event in service_event['event_list']:
                if event['eid'] == event_id:
                    return event
        return None

    async def add_reservation(self, req: AddReservationRequest) -> Reservation:
        """ EDCB に録画予約を追加し、追加された予約を返す。 """
        cmd = self._cmd()

        title = req.title
        start = req.start
        duration = req.duration_sec

        # 不足している番組情報は EDCB の EPG から補完する
        if title is None or start is None or duration is None:
            event = await self._find_event(cmd, req.onid, req.tsid, req.sid, req.event_id)
            if event is None:
                raise EDCBError('指定の番組が EDCB の EPG に見つかりません')
            if title is None:
                title = (event.get('short_info') or {}).get('event_name', '')
            if start is None:
                start = event.get('start_time')
            if duration is None:
                duration = event.get('duration_sec', 0)

        if start is None:
            raise EDCBError('録画開始時刻を決定できません')

        # EDCB は JST の壁時計時刻を期待するため、タイムゾーンを JST に変換する
        if start.tzinfo is not None:
            start = start.astimezone(JST)

        reserve: ReserveData = {
            'title': title or '',
            'start_time': start,
            'duration_second': duration or 0,
            'station_name': '',
            'onid': req.onid,
            'tsid': req.tsid,
            'sid': req.sid,
            'eid': req.event_id,
            'comment': 'added by jf-dvr',
            'reserve_id': 0,
            'overlap_mode': 0,
            'start_time_epg': start,
            'rec_setting': {
                'rec_mode': req.rec_mode,
                'priority': req.priority,
                'tuijyuu_flag': True,
                'service_mode': 0,
                'pittari_flag': False,
                'bat_file_path': '',
                'rec_folder_list': [],
                'suspend_mode': 0,
                'reboot_flag': False,
                'continue_rec_flag': False,
                'partial_rec_flag': 0,
                'tuner_id': 0,
                'partial_rec_folder': [],
            },
            'rec_file_name_list': [],
        }

        if not await cmd.sendAddReserve([reserve]):
            raise EDCBError('AddReserve に失敗しました')

        # 追加された予約を照合して返す
        matched = [
            r for r in await self.list_reservations()
            if r.onid == req.onid
            and r.tsid == req.tsid
            and r.sid == req.sid
            and r.event_id == req.event_id
        ]
        if not matched:
            raise EDCBError('予約は追加されましたが照合できませんでした')
        return max(matched, key=lambda r: r.id)

    async def delete_reservation(self, reserve_id: int) -> None:
        """ EDCB の録画予約を削除する。 """
        if not await self._cmd().sendDelReserve([reserve_id]):
            raise EDCBError(f'DelReserve に失敗しました (reserve_id={reserve_id})')

    async def list_recordings(self) -> list[Recording]:
        """ EDCB の録画済みファイル一覧を返す。 """
        recs = await self._cmd().sendEnumRecInfoBasic()
        if recs is None:
            raise EDCBError('EnumRecInfoBasic に失敗しました')
        return [_to_recording(r) for r in recs]

    async def get_recording(self, rec_id: int) -> Recording | None:
        """ 録画済みファイル1件を取得する。 """
        rec = await self._cmd().sendGetRecInfo(rec_id)
        if rec is None:
            return None
        return _to_recording(rec)

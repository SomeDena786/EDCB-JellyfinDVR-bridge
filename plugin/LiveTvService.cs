using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.LiveTv;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.LiveTv;
using MediaBrowser.Model.MediaInfo;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.JfDvr;

/// <summary>EDCB を Jellyfin Live TV に橋渡しする ILiveTvService 実装。

/// チャンネル / 番組表 / 予約 / ストリームをすべて Python ブリッジに委譲する。</summary>
public class LiveTvService : ILiveTvService
{
    private readonly BridgeClient _bridge;
    private readonly ILogger<LiveTvService> _logger;

    public LiveTvService(IHttpClientFactory httpClientFactory, ILogger<LiveTvService> logger)
    {
        _bridge = new BridgeClient(httpClientFactory);
        _logger = logger;
    }

    public string Name => "jf-dvr (EDCB)";

    public string HomePageUrl => string.Empty;

    public async Task<IEnumerable<ChannelInfo>> GetChannelsAsync(CancellationToken cancellationToken)
    {
        var channels = await _bridge.GetChannelsAsync(cancellationToken).ConfigureAwait(false);
        return channels.Select(c => new ChannelInfo
        {
            Id = c.Id,
            Name = c.Name,
            Number = c.ChannelNumber,
            ChannelType = ChannelType.TV,
            ImageUrl = c.HasLogo ? _bridge.LogoUrl(c.Id) : null,
        }).ToList();
    }

    public async Task<IEnumerable<ProgramInfo>> GetProgramsAsync(string channelId, DateTime startDateUtc, DateTime endDateUtc, CancellationToken cancellationToken)
    {
        var programs = await _bridge.GetEpgAsync(channelId, startDateUtc, endDateUtc, cancellationToken).ConfigureAwait(false);
        return programs.Select(p =>
        {
            var genres = p.Genres ?? new List<string>();
            var overview = p.Description ?? string.Empty;
            if (!string.IsNullOrEmpty(p.Extended))
            {
                overview = string.IsNullOrEmpty(overview) ? p.Extended : overview + "\n\n" + p.Extended;
            }

            return new ProgramInfo
            {
                Id = p.Id,
                ChannelId = p.ChannelId,
                Name = p.Title,
                Overview = overview,
                ShortOverview = p.Description,
                StartDate = p.Start.UtcDateTime,
                EndDate = p.End.UtcDateTime,
                Genres = genres,
                IsMovie = genres.Contains("映画"),
                IsNews = genres.Contains("ニュース／報道"),
                IsSports = genres.Contains("スポーツ"),
            };
        }).ToList();
    }

    public async Task<IEnumerable<TimerInfo>> GetTimersAsync(CancellationToken cancellationToken)
    {
        var reservations = await _bridge.GetReservationsAsync(cancellationToken).ConfigureAwait(false);
        var now = DateTime.UtcNow;
        return reservations.Select(r =>
        {
            var start = r.Start.UtcDateTime;
            var end = start.AddSeconds(r.DurationSec);
            return new TimerInfo
            {
                Id = r.Id.ToString(CultureInfo.InvariantCulture),
                ChannelId = r.ChannelId,
                ProgramId = $"{r.ChannelId}_{r.EventId}",
                Name = r.Title,
                Overview = r.Comment,
                StartDate = start,
                EndDate = end,
                Status = (now >= start && now < end) ? RecordingStatus.InProgress : RecordingStatus.New,
            };
        }).ToList();
    }

    public async Task CreateTimerAsync(TimerInfo info, CancellationToken cancellationToken)
    {
        var request = BuildAddRequest(info.ProgramId, info.ChannelId, info.Name, info.StartDate, info.EndDate);
        await _bridge.AddReservationAsync(request, cancellationToken).ConfigureAwait(false);
        _logger.LogInformation("EDCB に録画予約を追加しました: {Name}", info.Name);
    }

    public async Task CancelTimerAsync(string timerId, CancellationToken cancellationToken)
    {
        if (int.TryParse(timerId, NumberStyles.Integer, CultureInfo.InvariantCulture, out var id))
        {
            await _bridge.DeleteReservationAsync(id, cancellationToken).ConfigureAwait(false);
            _logger.LogInformation("EDCB の録画予約を削除しました: {Id}", id);
        }
    }

    public Task UpdateTimerAsync(TimerInfo updatedTimer, CancellationToken cancellationToken)
    {
        // ブリッジは予約の更新 API を持たないため無処理。予約変更は EDCB 側で行う。
        _logger.LogInformation("UpdateTimerAsync は未対応です (EDCB 側で変更してください): {Name}", updatedTimer?.Name);
        return Task.CompletedTask;
    }

    public async Task CreateSeriesTimerAsync(SeriesTimerInfo info, CancellationToken cancellationToken)
    {
        // シリーズ予約 (キーワード自動予約) は暫定的に対象番組の単発予約として登録する。
        // 継続的なシリーズ録画は EDCB の EPG 自動予約を利用する。
        if (!string.IsNullOrEmpty(info?.ProgramId))
        {
            var request = BuildAddRequest(info.ProgramId, info.ChannelId, info.Name, info.StartDate, info.EndDate);
            await _bridge.AddReservationAsync(request, cancellationToken).ConfigureAwait(false);
        }

        _logger.LogWarning("シリーズ予約は単発予約として登録しました。継続録画は EDCB の自動予約をご利用ください: {Name}", info?.Name);
    }

    public Task UpdateSeriesTimerAsync(SeriesTimerInfo info, CancellationToken cancellationToken)
        => Task.CompletedTask;

    public Task CancelSeriesTimerAsync(string timerId, CancellationToken cancellationToken)
        => Task.CompletedTask;

    public Task<IEnumerable<SeriesTimerInfo>> GetSeriesTimersAsync(CancellationToken cancellationToken)
        => Task.FromResult(Enumerable.Empty<SeriesTimerInfo>());

    public Task<SeriesTimerInfo> GetNewTimerDefaultsAsync(CancellationToken cancellationToken, ProgramInfo program = null)
    {
        return Task.FromResult(new SeriesTimerInfo
        {
            PrePaddingSeconds = 0,
            PostPaddingSeconds = 0,
            RecordAnyChannel = false,
            RecordAnyTime = false,
            RecordNewOnly = true,
        });
    }

    public Task<MediaSourceInfo> GetChannelStream(string channelId, string streamId, CancellationToken cancellationToken)
        => Task.FromResult(BuildMediaSource(channelId));

    public Task<List<MediaSourceInfo>> GetChannelStreamMediaSources(string channelId, CancellationToken cancellationToken)
        => Task.FromResult(new List<MediaSourceInfo> { BuildMediaSource(channelId) });

    public Task CloseLiveStream(string id, CancellationToken cancellationToken) => Task.CompletedTask;

    public Task ResetTuner(string id, CancellationToken cancellationToken) => Task.CompletedTask;

    private MediaSourceInfo BuildMediaSource(string channelId)
    {
        return new MediaSourceInfo
        {
            Id = channelId,
            Path = _bridge.StreamUrl(channelId),
            Protocol = MediaProtocol.Http,
            Container = "mpegts",
            IsInfiniteStream = true,
            RequiresOpening = false,
            RequiresClosing = false,
            SupportsProbing = true,
            MediaStreams = new List<MediaStream>
            {
                new MediaStream { Type = MediaStreamType.Video, Index = -1 },
                new MediaStream { Type = MediaStreamType.Audio, Index = -1 },
            },
        };
    }

    private static AddReservationRequest BuildAddRequest(string programId, string channelId, string title, DateTime startUtc, DateTime endUtc)
    {
        // programId は "<onid>-<tsid>-<sid>_<eventId>" 形式
        int onid = 0, tsid = 0, sid = 0, eventId = 0;
        if (!string.IsNullOrEmpty(programId))
        {
            var underscore = programId.LastIndexOf('_');
            if (underscore > 0)
            {
                ParseChannelId(programId.Substring(0, underscore), out onid, out tsid, out sid);
                int.TryParse(programId.Substring(underscore + 1), out eventId);
            }
        }

        if (onid == 0 && tsid == 0 && sid == 0 && !string.IsNullOrEmpty(channelId))
        {
            ParseChannelId(channelId, out onid, out tsid, out sid);
        }

        return new AddReservationRequest
        {
            Onid = onid,
            Tsid = tsid,
            Sid = sid,
            EventId = eventId,
            Title = title,
            Start = new DateTimeOffset(DateTime.SpecifyKind(startUtc, DateTimeKind.Utc)),
            DurationSec = (int)Math.Max(0, (endUtc - startUtc).TotalSeconds),
        };
    }

    private static void ParseChannelId(string channelId, out int onid, out int tsid, out int sid)
    {
        onid = 0;
        tsid = 0;
        sid = 0;
        var parts = channelId.Split('-');
        if (parts.Length == 3)
        {
            int.TryParse(parts[0], out onid);
            int.TryParse(parts[1], out tsid);
            int.TryParse(parts[2], out sid);
        }
    }
}

using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Common.Net;

namespace Jellyfin.Plugin.JfDvr;

// --- Python ブリッジの JSON 契約に対応する DTO ---

public class BridgeChannel
{
    public string Id { get; set; }

    public int Onid { get; set; }

    public int Tsid { get; set; }

    public int Sid { get; set; }

    public string Name { get; set; }

    public string NetworkName { get; set; }

    public string NetworkType { get; set; }

    public int ServiceType { get; set; }

    public int RemoteControlKeyId { get; set; }

    public string ChannelNumber { get; set; }

    public bool HasLogo { get; set; }
}

public class BridgeProgram
{
    public string Id { get; set; }

    public string ChannelId { get; set; }

    public int EventId { get; set; }

    public DateTimeOffset Start { get; set; }

    public DateTimeOffset End { get; set; }

    public int DurationSec { get; set; }

    public string Title { get; set; }

    public string Description { get; set; }

    public string Extended { get; set; }

    public List<string> Genres { get; set; }

    public bool IsFree { get; set; }
}

public class BridgeReservation
{
    public int Id { get; set; }

    public string ChannelId { get; set; }

    public int Onid { get; set; }

    public int Tsid { get; set; }

    public int Sid { get; set; }

    public int EventId { get; set; }

    public string Title { get; set; }

    public DateTimeOffset Start { get; set; }

    public int DurationSec { get; set; }

    public string StationName { get; set; }

    public bool Enabled { get; set; }

    public string Comment { get; set; }

    public List<string> RecFileNames { get; set; }
}

public class BridgeRecording
{
    public int Id { get; set; }

    public string ChannelId { get; set; }

    public string Title { get; set; }

    public DateTimeOffset Start { get; set; }

    public int DurationSec { get; set; }

    public string ServiceName { get; set; }

    public string FilePath { get; set; }

    public long Drops { get; set; }

    public long Scrambles { get; set; }

    public int RecStatus { get; set; }
}

public class AddReservationRequest
{
    public int Onid { get; set; }

    public int Tsid { get; set; }

    public int Sid { get; set; }

    public int EventId { get; set; }

    public string Title { get; set; }

    public DateTimeOffset? Start { get; set; }

    public int? DurationSec { get; set; }
}

/// <summary>Python ブリッジの REST API を呼び出すクライアント。</summary>
public class BridgeClient
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly IHttpClientFactory _httpClientFactory;

    public BridgeClient(IHttpClientFactory httpClientFactory)
    {
        _httpClientFactory = httpClientFactory;
    }

    private static string BaseUrl
    {
        get
        {
            var url = Plugin.Instance?.Configuration?.BridgeBaseUrl;
            return string.IsNullOrEmpty(url) ? "http://127.0.0.1:40880" : url.TrimEnd('/');
        }
    }

    private HttpClient CreateClient() => _httpClientFactory.CreateClient(NamedClient.Default);

    public async Task<List<BridgeChannel>> GetChannelsAsync(CancellationToken cancellationToken)
    {
        await using var stream = await CreateClient()
            .GetStreamAsync($"{BaseUrl}/channels", cancellationToken).ConfigureAwait(false);
        return await JsonSerializer.DeserializeAsync<List<BridgeChannel>>(stream, JsonOptions, cancellationToken)
            .ConfigureAwait(false) ?? new List<BridgeChannel>();
    }

    public async Task<List<BridgeProgram>> GetEpgAsync(string channelId, DateTime startUtc, DateTime endUtc, CancellationToken cancellationToken)
    {
        var url = $"{BaseUrl}/epg?channel={Uri.EscapeDataString(channelId)}"
                + $"&start={Uri.EscapeDataString(startUtc.ToString("o"))}"
                + $"&end={Uri.EscapeDataString(endUtc.ToString("o"))}";
        await using var stream = await CreateClient()
            .GetStreamAsync(url, cancellationToken).ConfigureAwait(false);
        return await JsonSerializer.DeserializeAsync<List<BridgeProgram>>(stream, JsonOptions, cancellationToken)
            .ConfigureAwait(false) ?? new List<BridgeProgram>();
    }

    public async Task<List<BridgeReservation>> GetReservationsAsync(CancellationToken cancellationToken)
    {
        await using var stream = await CreateClient()
            .GetStreamAsync($"{BaseUrl}/reservations", cancellationToken).ConfigureAwait(false);
        return await JsonSerializer.DeserializeAsync<List<BridgeReservation>>(stream, JsonOptions, cancellationToken)
            .ConfigureAwait(false) ?? new List<BridgeReservation>();
    }

    public async Task AddReservationAsync(AddReservationRequest request, CancellationToken cancellationToken)
    {
        var json = JsonSerializer.Serialize(request, JsonOptions);
        using var content = new StringContent(json, Encoding.UTF8, "application/json");
        using var response = await CreateClient()
            .PostAsync($"{BaseUrl}/reservations", content, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
    }

    public async Task DeleteReservationAsync(int reserveId, CancellationToken cancellationToken)
    {
        using var response = await CreateClient()
            .DeleteAsync($"{BaseUrl}/reservations/{reserveId}", cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
    }

    public async Task<List<BridgeRecording>> GetRecordingsAsync(CancellationToken cancellationToken)
    {
        await using var stream = await CreateClient()
            .GetStreamAsync($"{BaseUrl}/recordings", cancellationToken).ConfigureAwait(false);
        return await JsonSerializer.DeserializeAsync<List<BridgeRecording>>(stream, JsonOptions, cancellationToken)
            .ConfigureAwait(false) ?? new List<BridgeRecording>();
    }

    public string StreamUrl(string channelId) => $"{BaseUrl}/stream/{Uri.EscapeDataString(channelId)}";

    public string LogoUrl(string channelId) => $"{BaseUrl}/channels/{Uri.EscapeDataString(channelId)}/logo";

    public string RecordingStreamUrl(int recordingId) => $"{BaseUrl}/recordings/{recordingId}/stream";
}

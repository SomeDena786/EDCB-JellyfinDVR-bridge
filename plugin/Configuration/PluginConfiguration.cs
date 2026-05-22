using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.JfDvr.Configuration;

/// <summary>プラグイン設定。</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Python ブリッジのベース URL。</summary>
    public string BridgeBaseUrl { get; set; } = "http://127.0.0.1:40880";
}

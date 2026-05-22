using System;
using System.Collections.Generic;
using Jellyfin.Plugin.JfDvr.Configuration;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;

namespace Jellyfin.Plugin.JfDvr;

/// <summary>jf-dvr Jellyfin プラグイン本体。</summary>
public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
{
    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer)
    {
        Instance = this;
    }

    public static Plugin Instance { get; private set; }

    public override string Name => "jf-dvr (EDCB Live TV)";

    public override Guid Id => Guid.Parse("e8b5a6d4-3c2f-4a1e-9b7d-6f0c1a2e3d4b");

    public override string Description => "EDCB をバックエンドにした Jellyfin Live TV / DVR 連携。";

    public IEnumerable<PluginPageInfo> GetPages() => new[]
    {
        new PluginPageInfo
        {
            Name = Name,
            EmbeddedResourcePath = GetType().Namespace + ".Configuration.configPage.html",
        },
    };
}

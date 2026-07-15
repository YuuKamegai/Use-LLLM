using System.Diagnostics;
using System.Net;
using System.Text.Json;

namespace Use_LLLM_WebUI;

internal static class Program
{
    private const string WebUrl = "http://127.0.0.1:8765/general/";
    private const string LauncherHealthUrl = WebUrl + "api/launcher-health";

    private enum ServerState
    {
        Absent,
        Healthy,
        Incompatible,
    }

    [STAThread]
    private static void Main(string[] args)
    {
        try
        {
            bool noBrowser = args.Contains("--no-browser", StringComparer.OrdinalIgnoreCase);
            ServerState serverState = ProbeServerAsync().GetAwaiter().GetResult();
            if (serverState == ServerState.Healthy)
            {
                if (!noBrowser)
                {
                    OpenBrowser();
                }
                return;
            }
            if (serverState == ServerState.Incompatible)
            {
                MessageBox.Show(
                    "ポート8765で古い、または互換性のないUse-LLLMサーバーが稼働しています。\n\n" +
                    "そのサーバーを終了してから、Use-LLLM-WebUI.exeをもう一度起動してください。",
                    "Use-LLLM WebUI",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
                return;
            }

            string root = FindProjectRoot();
            string script = Path.Combine(root, "Start-WebUI.ps1");
            var start = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            start.ArgumentList.Add("-NoLogo");
            start.ArgumentList.Add("-NoProfile");
            start.ArgumentList.Add("-ExecutionPolicy");
            start.ArgumentList.Add("Bypass");
            start.ArgumentList.Add("-File");
            start.ArgumentList.Add(script);
            if (noBrowser)
            {
                start.ArgumentList.Add("-NoBrowser");
            }
            Process.Start(start);
        }
        catch (Exception error)
        {
            MessageBox.Show(
                $"Use-LLLM WebUIを起動できませんでした。\n\n{error.Message}",
                "Use-LLLM WebUI",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }

    private static async Task<ServerState> ProbeServerAsync()
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(600) };
        try
        {
            using HttpResponseMessage response = await client.GetAsync(LauncherHealthUrl);
            if (response.StatusCode != HttpStatusCode.OK)
            {
                return ServerState.Incompatible;
            }

            await using Stream content = await response.Content.ReadAsStreamAsync();
            using JsonDocument payload = await JsonDocument.ParseAsync(content);
            JsonElement root = payload.RootElement;
            bool expectedApplication =
                root.TryGetProperty("application", out JsonElement application) &&
                application.GetString() == "use-lllm-webui";
            bool expectedSurface =
                root.TryGetProperty("surface", out JsonElement surface) &&
                surface.GetString() == "general";
            bool staticReady =
                root.TryGetProperty("static_ready", out JsonElement ready) &&
                ready.ValueKind == JsonValueKind.True;
            return expectedApplication && expectedSurface && staticReady
                ? ServerState.Healthy
                : ServerState.Incompatible;
        }
        catch (HttpRequestException)
        {
            return ServerState.Absent;
        }
        catch (TaskCanceledException)
        {
            return ServerState.Absent;
        }
        catch (JsonException)
        {
            return ServerState.Incompatible;
        }
    }

    private static void OpenBrowser()
    {
        Process.Start(new ProcessStartInfo(WebUrl) { UseShellExecute = true });
    }

    private static string FindProjectRoot()
    {
        foreach (string origin in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            DirectoryInfo? directory = new(origin);
            for (int depth = 0; directory is not null && depth < 6; depth += 1, directory = directory.Parent)
            {
                if (File.Exists(Path.Combine(directory.FullName, "Start-WebUI.ps1")))
                {
                    return directory.FullName;
                }
            }
        }

        throw new FileNotFoundException(
            "Start-WebUI.ps1が見つかりません。EXEをUse-LLLMフォルダ内に置いてください。");
    }
}

using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;

namespace Use_LLLM_WebUI;

internal static class Program
{
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
            int port = RequestedPort(args) ?? SelectAvailablePort();
            string webUrl = $"http://127.0.0.1:{port}/general/";
            string root = FindProjectRoot();
            Process process = StartServer(root, port);

            if (noBrowser)
            {
                return;
            }

            ServerState state = WaitForHealthyServerAsync(webUrl).GetAwaiter().GetResult();
            if (state == ServerState.Healthy)
            {
                OpenBrowser(webUrl);
                return;
            }

            string detail = process.HasExited
                ? $"サーバープロセスが終了しました（exit code: {process.ExitCode}）。"
                : "起動確認がタイムアウトしました。";
            MessageBox.Show(
                $"Use-LLLM WebUIを起動できませんでした。\n\n{detail}",
                "Use-LLLM WebUI",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
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

    private static Process StartServer(string root, int port)
    {
        string packagedServer = Path.Combine(root, "runtime", "Use-LLLM-Server.exe");
        if (File.Exists(packagedServer))
        {
            var packaged = new ProcessStartInfo
            {
                FileName = packagedServer,
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            packaged.ArgumentList.Add("serve");
            packaged.ArgumentList.Add("--host");
            packaged.ArgumentList.Add("127.0.0.1");
            packaged.ArgumentList.Add("--port");
            packaged.ArgumentList.Add(port.ToString(CultureInfo.InvariantCulture));
            return Process.Start(packaged) ?? throw new InvalidOperationException("サーバーを起動できませんでした。");
        }

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
        start.ArgumentList.Add("-Port");
        start.ArgumentList.Add(port.ToString(CultureInfo.InvariantCulture));
        start.ArgumentList.Add("-NoBrowser");
        return Process.Start(start) ?? throw new InvalidOperationException("サーバーを起動できませんでした。");
    }

    private static int? RequestedPort(string[] args)
    {
        for (int index = 0; index < args.Length; index += 1)
        {
            if (!args[index].Equals("--port", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (index + 1 >= args.Length ||
                !int.TryParse(args[index + 1], NumberStyles.None, CultureInfo.InvariantCulture, out int port) ||
                port is < 1 or > 65535)
            {
                throw new ArgumentException("--portには1〜65535を指定してください。");
            }
            return port;
        }
        return null;
    }

    private static int SelectAvailablePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            return ((IPEndPoint)listener.LocalEndpoint).Port;
        }
        finally
        {
            listener.Stop();
        }
    }

    private static async Task<ServerState> WaitForHealthyServerAsync(string webUrl)
    {
        for (int attempt = 0; attempt < 80; attempt += 1)
        {
            ServerState state = await ProbeServerAsync(webUrl + "api/launcher-health");
            if (state != ServerState.Absent)
            {
                return state;
            }
            await Task.Delay(250);
        }
        return ServerState.Absent;
    }

    private static async Task<ServerState> ProbeServerAsync(string launcherHealthUrl)
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(600) };
        try
        {
            using HttpResponseMessage response = await client.GetAsync(launcherHealthUrl);
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

    private static void OpenBrowser(string webUrl)
    {
        Process.Start(new ProcessStartInfo(webUrl) { UseShellExecute = true });
    }

    private static string FindProjectRoot()
    {
        foreach (string origin in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            DirectoryInfo? directory = new(origin);
            for (int depth = 0; directory is not null && depth < 6; depth += 1, directory = directory.Parent)
            {
                if (File.Exists(Path.Combine(directory.FullName, "Start-WebUI.ps1")) ||
                    File.Exists(Path.Combine(directory.FullName, "runtime", "Use-LLLM-Server.exe")))
                {
                    return directory.FullName;
                }
            }
        }

        throw new FileNotFoundException(
            "Use-LLLMの実行ファイルが見つかりません。アプリを再インストールしてください。");
    }
}

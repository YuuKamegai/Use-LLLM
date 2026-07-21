using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Security;
using System.Text;
using Microsoft.Win32;

namespace Use_LLLM_Installer;

internal static class Program
{
    private const string ProductName = "Use-LLLM";
    private const string Publisher = "Use-LLLM";
    private const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Use-LLLM";

    [STAThread]
    private static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        bool quiet = args.Contains("--quiet", StringComparer.OrdinalIgnoreCase);
        if (args.Contains("--uninstall", StringComparer.OrdinalIgnoreCase))
        {
            Uninstall(quiet);
            return;
        }
        if (args.Contains("--install", StringComparer.OrdinalIgnoreCase) && quiet)
        {
            try
            {
                Install(false, (_, _) => { });
            }
            catch
            {
                Environment.ExitCode = 1;
            }
            return;
        }
        Application.Run(new InstallerForm());
    }

    internal static string InstallDirectory()
    {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        return Path.GetFullPath(Path.Combine(local, "Programs", ProductName));
    }

    internal static void Install(bool desktopShortcut, Action<int, string> progress)
    {
        string installDirectory = InstallDirectory();
        EnsureSafeInstallDirectory(installDirectory);
        if (Process.GetProcessesByName("Use-LLLM-Server").Length > 0)
        {
            throw new InvalidOperationException("Use-LLLMを終了してからインストールしてください。");
        }

        string staging = Path.Combine(Path.GetTempPath(), $"Use-LLLM-{Guid.NewGuid():N}");
        string backup = installDirectory + ".previous";
        Directory.CreateDirectory(staging);
        try
        {
            progress(10, "アプリを展開しています...");
            ExtractPayload(staging);
            if (!File.Exists(Path.Combine(staging, "Use-LLLM-WebUI.exe")) ||
                !File.Exists(Path.Combine(staging, "runtime", "Use-LLLM-Server.exe")))
            {
                throw new InvalidDataException("インストールペイロードが不完全です。");
            }

            if (Directory.Exists(backup)) Directory.Delete(backup, true);
            if (Directory.Exists(installDirectory)) Directory.Move(installDirectory, backup);
            try
            {
                Directory.Move(staging, installDirectory);
            }
            catch
            {
                if (!Directory.Exists(installDirectory) && Directory.Exists(backup))
                    Directory.Move(backup, installDirectory);
                throw;
            }
            try
            {
                progress(72, "ショートカットを作成しています...");
                string uninstaller = Path.Combine(installDirectory, "Use-LLLM-Uninstall.exe");
                File.Copy(Application.ExecutablePath, uninstaller, true);
                CreateShortcut(StartMenuShortcut(), Path.Combine(installDirectory, "Use-LLLM-WebUI.exe"));
                if (desktopShortcut)
                    CreateShortcut(DesktopShortcut(), Path.Combine(installDirectory, "Use-LLLM-WebUI.exe"));
                else
                    File.Delete(DesktopShortcut());
                RegisterUninstaller(uninstaller, installDirectory);
                if (Directory.Exists(backup)) Directory.Delete(backup, true);
                progress(100, "インストールが完了しました。");
            }
            catch
            {
                Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
                File.Delete(StartMenuShortcut());
                if (Directory.Exists(installDirectory)) Directory.Delete(installDirectory, true);
                if (Directory.Exists(backup)) Directory.Move(backup, installDirectory);
                throw;
            }
        }
        finally
        {
            if (Directory.Exists(staging)) Directory.Delete(staging, true);
        }
    }

    private static void ExtractPayload(string destination)
    {
        using Stream payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("UseLLLM.Payload.zip")
            ?? throw new InvalidDataException("インストールペイロードがありません。");
        using var archive = new ZipArchive(payload, ZipArchiveMode.Read);
        string root = Path.GetFullPath(destination) + Path.DirectorySeparatorChar;
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string target = Path.GetFullPath(Path.Combine(destination, entry.FullName));
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                throw new SecurityException("不正なペイロードパスを検出しました。");
            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(target);
                continue;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            entry.ExtractToFile(target, true);
        }
    }

    private static void RegisterUninstaller(string uninstaller, string installDirectory)
    {
        using RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey, true);
        string version = Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.1.0";
        key.SetValue("DisplayName", ProductName);
        key.SetValue("DisplayVersion", version);
        key.SetValue("Publisher", Publisher);
        key.SetValue("InstallLocation", installDirectory);
        key.SetValue("DisplayIcon", Path.Combine(installDirectory, "Use-LLLM-WebUI.exe"));
        key.SetValue("UninstallString", $"\"{uninstaller}\" --uninstall");
        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
    }

    private static void Uninstall(bool quiet)
    {
        if (!quiet && MessageBox.Show(
                "Use-LLLMをアンインストールしますか？\n\n会話、Knowledge、設定はこのPCに保持されます。",
                ProductName,
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question) != DialogResult.Yes)
            return;

        string installDirectory = InstallDirectory();
        EnsureSafeInstallDirectory(installDirectory);
        File.Delete(StartMenuShortcut());
        File.Delete(DesktopShortcut());
        Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
        string script = $"$p=Get-Process -Id {Environment.ProcessId} -ErrorAction SilentlyContinue; " +
                        "if($p){$p | Wait-Process}; " +
                        $"Remove-Item -LiteralPath '{installDirectory.Replace("'", "''")}' -Recurse -Force -ErrorAction SilentlyContinue";
        StartEncodedPowerShell(script);
        if (!quiet)
            MessageBox.Show(
                "アンインストールを開始しました。ローカルデータは保持されます。",
                ProductName,
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
    }

    private static string StartMenuShortcut() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.StartMenu),
        "Programs", "Use-LLLM.lnk");

    private static string DesktopShortcut() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
        "Use-LLLM.lnk");

    private static void CreateShortcut(string shortcut, string target)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(shortcut)!);
        string escapedShortcut = shortcut.Replace("'", "''");
        string escapedTarget = target.Replace("'", "''");
        string script = "$w=New-Object -ComObject WScript.Shell; " +
                        $"$s=$w.CreateShortcut('{escapedShortcut}'); " +
                        $"$s.TargetPath='{escapedTarget}'; $s.WorkingDirectory='{Path.GetDirectoryName(target)!.Replace("'", "''")}'; $s.Save()";
        using Process process = StartEncodedPowerShell(script);
        process.WaitForExit();
        if (process.ExitCode != 0 || !File.Exists(shortcut))
            throw new InvalidOperationException("ショートカットを作成できませんでした。");
    }

    private static Process StartEncodedPowerShell(string script)
    {
        string encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
        var start = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        };
        start.ArgumentList.Add("-NoLogo");
        start.ArgumentList.Add("-NoProfile");
        start.ArgumentList.Add("-NonInteractive");
        start.ArgumentList.Add("-EncodedCommand");
        start.ArgumentList.Add(encoded);
        return Process.Start(start) ?? throw new InvalidOperationException("PowerShellを起動できませんでした。");
    }

    private static void EnsureSafeInstallDirectory(string path)
    {
        string expected = Path.GetFullPath(Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", ProductName));
        if (!Path.GetFullPath(path).Equals(expected, StringComparison.OrdinalIgnoreCase))
            throw new SecurityException("インストール先が安全な範囲外です。");
    }

    internal static void LaunchInstalledApp()
    {
        string executable = Path.Combine(InstallDirectory(), "Use-LLLM-WebUI.exe");
        Process.Start(new ProcessStartInfo(executable) { UseShellExecute = true });
    }
}

internal sealed class InstallerForm : Form
{
    private readonly Label _status = new() { AutoSize = true, Text = "このPCへUse-LLLMをインストールします。" };
    private readonly ProgressBar _progress = new() { Minimum = 0, Maximum = 100, Value = 0 };
    private readonly CheckBox _desktop = new() { Text = "デスクトップにショートカットを作成", Checked = true, AutoSize = true };
    private readonly CheckBox _launch = new() { Text = "完了後にUse-LLLMを起動", Checked = true, AutoSize = true };
    private readonly Button _install = new() { Text = "インストール", AutoSize = true };

    internal InstallerForm()
    {
        Text = "Use-LLLM Setup";
        Width = 540;
        Height = 310;
        MinimumSize = new Size(500, 290);
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Segoe UI", 10);
        var title = new Label { Text = "Use-LLLM", AutoSize = true, Font = new Font("Segoe UI", 24, FontStyle.Bold) };
        var description = new Label
        {
            AutoSize = true,
            MaximumSize = new Size(450, 0),
            Text = "ローカルLLMと任意のMCPサーバーを使う汎用チャットを、ユーザー単位でインストールします。",
        };
        _progress.Width = 450;
        _install.Click += InstallClicked;
        var layout = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.TopDown,
            WrapContents = false,
            Padding = new Padding(28),
            AutoScroll = true,
        };
        layout.Controls.AddRange([title, description, _desktop, _launch, _progress, _status, _install]);
        Controls.Add(layout);
    }

    private async void InstallClicked(object? sender, EventArgs eventArgs)
    {
        _install.Enabled = false;
        try
        {
            await Task.Run(() => Program.Install(_desktop.Checked, ReportProgress));
            _status.Text = "インストールが完了しました。";
            _install.Text = "閉じる";
            _install.Enabled = true;
            _install.Click -= InstallClicked;
            _install.Click += (_, _) => Close();
            if (_launch.Checked) Program.LaunchInstalledApp();
        }
        catch (Exception error)
        {
            _status.Text = "インストールに失敗しました。";
            MessageBox.Show(error.Message, "Use-LLLM Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            _install.Enabled = true;
        }
    }

    private void ReportProgress(int value, string message)
    {
        if (InvokeRequired)
        {
            BeginInvoke(() => ReportProgress(value, message));
            return;
        }
        _progress.Value = Math.Clamp(value, 0, 100);
        _status.Text = message;
    }
}

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: System.Reflection.AssemblyTitle("声织 Voice Loom")]
[assembly: System.Reflection.AssemblyVersion("1.2.0.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]

internal static class Program
{
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 1048576 };
    internal static readonly bool Portable = File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "portable.mode"));
    internal static readonly string Home = Portable
        ? Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "user-data")
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VoiceLoom");
    internal static readonly string DefaultData = Path.GetFullPath(Path.Combine(Home, "data"));
    internal static string Data;
    internal static string Reports;
    internal static string Diagnostics;
    internal static int Port = 17861;
    internal static double Zoom = 1.0;
    private static Mutex instance;
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);

    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Data = DefaultData;
        try
        {
            Directory.CreateDirectory(Home);
            string settings = Path.Combine(Home, "desktop.json");
            if (File.Exists(settings))
            {
                Dictionary<string, object> saved = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(settings));
                if (saved.ContainsKey("data_directory") && saved["data_directory"] != null)
                {
                    string savedData = Convert.ToString(saved["data_directory"]);
                    if (!String.IsNullOrWhiteSpace(savedData))
                    {
                        string candidate = Path.IsPathRooted(savedData)
                            ? Path.GetFullPath(savedData)
                            : Path.GetFullPath(Path.Combine(Home, savedData));
                        if (!Portable || Directory.Exists(candidate)) Data = candidate;
                    }
                }
                if (saved.ContainsKey("port")) Port = Convert.ToInt32(saved["port"]);
                if (saved.ContainsKey("zoom")) Zoom = Convert.ToDouble(saved["zoom"], CultureInfo.InvariantCulture);
                if (saved.ContainsKey("reports_directory")) Reports = Convert.ToString(saved["reports_directory"]);
            }
            string environment = Environment.GetEnvironmentVariable("VOICE_LOOM_DATA");
            if (!String.IsNullOrWhiteSpace(environment)) Data = Path.GetFullPath(environment);
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--data" && i + 1 < args.Length) Data = Path.GetFullPath(args[++i]);
                else if (args[i] == "--port" && i + 1 < args.Length) Port = Int32.Parse(args[++i]);
                else if (args[i] == "--diagnostics" && i + 1 < args.Length) Diagnostics = Path.GetFullPath(args[++i]);
            }
            if (Port < 1024 || Port > 65535) throw new Exception("本机服务端口设置无效。");
            Zoom = Math.Max(.75, Math.Min(1.75, Zoom));
            bool created;
            string user = System.Security.Principal.WindowsIdentity.GetCurrent().User.Value;
            instance = new Mutex(true, "Local\\VoiceLoom.Desktop." + user, out created);
            if (!created && Diagnostics == null)
            {
                string executable = Application.ExecutablePath;
                foreach (Process process in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(executable)))
                    try
                    {
                        if (process.Id != Process.GetCurrentProcess().Id && process.MainWindowHandle != IntPtr.Zero &&
                            String.Equals(process.MainModule.FileName, executable, StringComparison.OrdinalIgnoreCase))
                        { ShowWindow(process.MainWindowHandle, 9); SetForegroundWindow(process.MainWindowHandle); break; }
                    }
                    catch (System.ComponentModel.Win32Exception) { }
                instance.Dispose();
                return;
            }
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += delegate(object sender, ThreadExceptionEventArgs e) { Log(e.Exception); MessageBox.Show("界面遇到问题，请关闭后重新打开。诊断已保存在本机日志目录。", "声织", MessageBoxButtons.OK, MessageBoxIcon.Error); };
            Application.Run(new LoomWindow(new Backend(), true, "/"));
            if (created) instance.ReleaseMutex();
            instance.Dispose();
        }
        catch (Exception error)
        {
            Log(error);
            MessageBox.Show("声织启动失败：" + error.Message, "声织", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    internal static void SavePreferences()
    {
        try
        {
            string path = Path.Combine(Home, "desktop.json");
            string temporary = path + ".tmp";
            string customData = String.Equals(Path.GetFullPath(Data), DefaultData, StringComparison.OrdinalIgnoreCase) ? null : Data;
            File.WriteAllText(temporary, Json.Serialize(new { data_directory = customData, reports_directory = Reports, port = Port, zoom = Zoom }), new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(temporary, path, null); else File.Move(temporary, path);
        }
        catch (IOException error) { Log(error); }
    }

    internal static void Log(Exception error)
    {
        try { Directory.CreateDirectory(Home); File.AppendAllText(Path.Combine(Home, "desktop-error.log"), DateTime.Now.ToString("s") + " " + error.GetType().Name + ": " + error.Message + Environment.NewLine); }
        catch (IOException) { }
    }
}

internal sealed class Backend
{
    internal readonly Uri Origin = new Uri("http://127.0.0.1:" + Program.Port + "/");
    internal string Session;
    internal Process Child;
    private ProcessJob job;
    private HttpClient client = new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(3), MaxResponseContentBufferSize = 65536 };
    private static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
    private static bool Same(string a, string b)
    {
        if (a == null || b == null || a.Length != b.Length) return false;
        int difference = 0; for (int i = 0; i < a.Length; i++) difference |= a[i] ^ b[i]; return difference == 0;
    }
    private static string Sign(string secret, string text)
    { using (HMACSHA256 hmac = new HMACSHA256(Encoding.ASCII.GetBytes(secret))) return Hex(hmac.ComputeHash(Encoding.ASCII.GetBytes(text))); }

    private async Task<bool> Probe()
    {
        try
        {
            string path = Path.Combine(Program.Data, ".local-access.json");
            if (!File.Exists(path) || new FileInfo(path).Length > 16384) return false;
            Dictionary<string, object> saved = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
            string protectedSecret = Convert.ToString(saved["protected_secret"]);
            if (!protectedSecret.StartsWith("dpapi:", StringComparison.Ordinal)) return false;
            string secret = Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(protectedSecret.Substring(6)), null, DataProtectionScope.CurrentUser));
            byte[] nonce = new byte[32]; using (RandomNumberGenerator random = RandomNumberGenerator.Create()) random.GetBytes(nonce);
            string challenge = Hex(nonce);
            using (HttpResponseMessage response = await client.GetAsync(new Uri(Origin, "api/health?challenge=" + challenge)))
            {
                response.EnsureSuccessStatusCode();
                using (Stream stream = await response.Content.ReadAsStreamAsync())
                using (MemoryStream buffer = new MemoryStream())
                {
                    byte[] block = new byte[4096]; int count;
                    while ((count = await stream.ReadAsync(block, 0, block.Length)) > 0)
                    { if (buffer.Length + count > 65536) return false; buffer.Write(block, 0, count); }
                    Dictionary<string, object> health = Program.Json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(buffer.ToArray()));
                    if (!health.ContainsKey("proof") || !Same(Sign(secret, "health-proof:" + challenge), Convert.ToString(health["proof"]))) return false;
                    Session = Sign(secret, "browser-session-v1");
                    return true;
                }
            }
        }
        catch (HttpRequestException) { return false; }
        catch (TaskCanceledException) { return false; }
        catch (IOException) { return false; }
        catch (CryptographicException) { return false; }
        catch (ArgumentException) { return false; }
        catch (KeyNotFoundException) { return false; }
        catch (InvalidOperationException) { return false; }
    }

    internal async Task Start()
    {
        if (await Probe()) return;
        if (Child != null)
        {
            if (!Child.HasExited) Child.Kill();
            Child.Dispose(); Child = null;
            if (job != null) { job.Dispose(); job = null; }
        }
        string service = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "service", "VoiceLoom.Service.exe");
        if (!File.Exists(service)) throw new FileNotFoundException("工作台运行文件不完整，请重新解压整个便携包。", service);
        Directory.CreateDirectory(Program.Data);
        ProcessStartInfo info = new ProcessStartInfo(service, "--port " + Program.Port);
        info.WorkingDirectory = Path.GetDirectoryName(service);
        info.UseShellExecute = false; info.CreateNoWindow = true;
        info.RedirectStandardError = true; info.RedirectStandardOutput = true;
        info.EnvironmentVariables["VOICE_LOOM_DATA"] = Program.Data;
        info.EnvironmentVariables["VOICE_LOOM_PORT"] = Program.Port.ToString(CultureInfo.InvariantCulture);
        info.EnvironmentVariables["VOICE_LOOM_APP_DIR"] = AppDomain.CurrentDomain.BaseDirectory;
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        if (!String.IsNullOrWhiteSpace(Program.Reports)) info.EnvironmentVariables["VOICE_LOOM_REPORTS"] = Program.Reports;
        info.EnvironmentVariables.Remove("PYTHONPATH");
        Child = new Process { StartInfo = info };
        Child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (!String.IsNullOrEmpty(e.Data)) Program.Log(new Exception(e.Data)); };
        Child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
        Child.Start();
        try { job = new ProcessJob(Child); }
        catch { if (!Child.HasExited) Child.Kill(); throw; }
        Child.BeginErrorReadLine(); Child.BeginOutputReadLine();
        for (int i = 0; i < 100; i++)
        {
            if (await Probe()) return;
            if (Child.HasExited) throw new Exception("本机后台未能启动。可能存在旧后台或端口冲突，请查看本机诊断日志。");
            await Task.Delay(300);
        }
        throw new TimeoutException("本机后台启动超时，请重试或查看诊断日志。");
    }

    internal bool IsLocal(string text)
    { Uri uri; return Uri.TryCreate(text, UriKind.Absolute, out uri) && uri.Scheme == Origin.Scheme && uri.Host == Origin.Host && uri.Port == Origin.Port && String.IsNullOrEmpty(uri.UserInfo); }

    internal async Task Stop(bool shutdown)
    {
        try
        {
            if (Session != null)
            {
            using (HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, new Uri(Origin, "api/listen/stop")))
            { request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", Session); using (HttpResponseMessage response = await client.SendAsync(request)) { } }
            if (shutdown && Child != null && !Child.HasExited)
                using (HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, new Uri(Origin, "api/desktop/shutdown")))
                { request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", Session); using (HttpResponseMessage response = await client.SendAsync(request)) { } }
            }
        }
        catch (HttpRequestException) { }
        catch (TaskCanceledException) { }
        if (shutdown && Child != null && !Child.HasExited)
        {
            Process ownChild = Child;
            bool exited = await Task.Run(delegate { return ownChild.WaitForExit(15000); });
            if (!exited && !ownChild.HasExited) ownChild.Kill();
        }
        if (shutdown && job != null) { job.Dispose(); job = null; }
    }
}

internal sealed class ProcessJob : IDisposable
{
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimits
    { public long PerProcessTime, PerJobTime; public uint Flags; public UIntPtr MinimumWorkingSet, MaximumWorkingSet; public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass; }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters
    { public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits
    { public BasicLimits Basic; public IoCounters Io; public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimits limits, int length);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    private IntPtr handle;
    internal ProcessJob(Process process)
    {
        handle = CreateJobObject(IntPtr.Zero, null);
        ExtendedLimits limits = new ExtendedLimits(); limits.Basic.Flags = 0x2000;
        if (handle == IntPtr.Zero || !SetInformationJobObject(handle, 9, ref limits, Marshal.SizeOf(limits)) || !AssignProcessToJobObject(handle, process.Handle))
        { int error = Marshal.GetLastWin32Error(); Dispose(); throw new System.ComponentModel.Win32Exception(error, "无法启用后台生命周期保护"); }
    }
    public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } GC.SuppressFinalize(this); }
    ~ProcessJob() { Dispose(); }
}

internal sealed class LoomWindow : Form
{
    private readonly Backend backend;
    private readonly bool primary;
    private readonly string initialPath;
    private WebView2 view;
    private Panel cover;
    private Label message;
    private bool closing;
    private bool mayClose;
    private bool ready;
    private bool initializing;
    [DllImport("user32.dll")] private static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] private static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);

    internal LoomWindow(Backend service, bool main, string path)
    {
        backend = service; primary = main; initialPath = path;
        Text = main ? "声织 · Voice Loom" : "声织 · 试听与字幕";
        Font = new Font("Microsoft YaHei UI", 9F);
        AutoScaleDimensions = new SizeF(96F, 96F); AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(1480, 900); MinimumSize = new Size(520, 320);
        BackColor = Color.FromArgb(28, 38, 54); StartPosition = FormStartPosition.CenterScreen;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        view = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = BackColor };
        cover = new Panel { Dock = DockStyle.Fill, BackColor = BackColor };
        message = new Label { Dock = DockStyle.Fill, ForeColor = Color.FromArgb(230, 237, 246), TextAlign = ContentAlignment.MiddleCenter, Text = "声织\r\n正在启动本机工作台…", Font = new Font("Microsoft YaHei UI", 14F) };
        FlowLayoutPanel actions = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 52, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(16, 5, 0, 0) };
        Button retry = new Button { Text = "重新连接", AutoSize = true }; retry.Click += async delegate { await Initialize(); };
        Button logs = new Button { Text = "打开日志目录", AutoSize = true }; logs.Click += delegate { Process.Start("explorer.exe", "\"" + Program.Home + "\""); };
        Button runtime = new Button { Text = "获取 WebView2 运行时", AutoSize = true }; runtime.Click += delegate { OpenExternal("https://developer.microsoft.com/microsoft-edge/webview2/"); };
        actions.Controls.Add(retry); actions.Controls.Add(logs); actions.Controls.Add(runtime); cover.Controls.Add(message); cover.Controls.Add(actions);
        Controls.Add(view); Controls.Add(cover); cover.BringToFront();
        HandleCreated += delegate { UpdateCaption(); };
        Shown += async delegate { Rectangle work = Screen.FromControl(this).WorkingArea; if (Width > work.Width || Height > work.Height) Bounds = new Rectangle(work.X + 12, work.Y + 12, Math.Min(Width, work.Width - 24), Math.Min(Height, work.Height - 24)); await Initialize(); };
        DpiChanged += delegate { UpdateStatus(); };
        FormClosing += OnClosing;
    }

    private async Task Initialize()
    {
        if (initializing) return; initializing = true;
        cover.Visible = true; message.Text = "声织\r\n正在连接本机工作台…";
        try
        {
            if (primary || backend.Session == null) await backend.Start();
            CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Program.Home, "WebView2"));
            if (view.CoreWebView2 == null)
            {
                await view.EnsureCoreWebView2Async(environment);
                CoreWebView2 core = view.CoreWebView2;
                core.Settings.AreHostObjectsAllowed = false; core.Settings.IsWebMessageEnabled = false;
                core.Settings.AreDevToolsEnabled = true; core.Settings.AreDefaultContextMenusEnabled = true;
                core.Settings.IsStatusBarEnabled = false; core.Settings.IsPasswordAutosaveEnabled = false;
                core.Settings.IsGeneralAutofillEnabled = false;
                core.PermissionRequested += delegate(object sender, CoreWebView2PermissionRequestedEventArgs e)
                {
                    e.State = e.PermissionKind == CoreWebView2PermissionKind.ClipboardRead
                        ? CoreWebView2PermissionState.Allow
                        : CoreWebView2PermissionState.Deny;
                    e.Handled = true;
                };
                core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
                {
                    Uri action;
                    if (Uri.TryCreate(e.Uri, UriKind.Absolute, out action) && action.Scheme == "voiceloom")
                    { e.Cancel = true; if (e.IsUserInitiated && ready && backend.IsLocal(core.Source)) DesktopAction(action); return; }
                    if (!backend.IsLocal(e.Uri)) { e.Cancel = true; if (e.IsUserInitiated) OpenExternal(e.Uri); }
                };
                core.FrameNavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { if (!backend.IsLocal(e.Uri)) e.Cancel = true; };
                core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
                {
                    e.Handled = true;
                    if (!e.IsUserInitiated) return;
                    if (backend.IsLocal(e.Uri)) new LoomWindow(backend, false, e.Uri).Show(this); else OpenExternal(e.Uri);
                };
                core.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs e) { if (!backend.IsLocal(e.DownloadOperation.Uri)) e.Cancel = true; };
                core.ProcessFailed += delegate { message.Text = "界面进程已停止，请点击“重新连接”。"; cover.Visible = true; ready = false; };
                core.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
                {
                    if (!e.IsSuccess)
                    {
                        if (e.WebErrorStatus == CoreWebView2WebErrorStatus.OperationCanceled) return;
                        message.Text = "界面加载失败，请重新连接。"; cover.Visible = true; return;
                    }
                    ready = true; cover.Visible = false; view.Focus(); UpdateStatus();
                    if (primary && Program.Diagnostics != null) await VerifyRendering();
                };
                await core.AddScriptToExecuteOnDocumentCreatedAsync("Object.defineProperty(window,'__voiceLoomDesktop',{value:true});");
            }
            CoreWebView2Cookie cookie = view.CoreWebView2.CookieManager.CreateCookie("voice_loom_session", backend.Session, "127.0.0.1", "/");
            cookie.IsHttpOnly = true; cookie.SameSite = CoreWebView2CookieSameSiteKind.Strict;
            view.CoreWebView2.CookieManager.AddOrUpdateCookie(cookie);
            view.ZoomFactor = Program.Zoom;
            view.Source = new Uri(backend.Origin, initialPath);
        }
        catch (WebView2RuntimeNotFoundException) { message.Text = "需要 Microsoft Edge WebView2 运行时。\r\n请从微软官方网站安装后，点击重新连接。\r\nhttps://developer.microsoft.com/microsoft-edge/webview2/"; }
        catch (Exception error) { Program.Log(error); message.Text = "声织暂未连接\r\n" + error.Message; }
        finally { initializing = false; }
    }

    private void OpenExternal(string text)
    {
        Uri uri;
        if (Uri.TryCreate(text, UriKind.Absolute, out uri) && uri.Scheme == "https" && String.IsNullOrEmpty(uri.UserInfo))
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
    }

    private void SetZoom(double value)
    {
        Program.Zoom = Math.Round(Math.Max(.75, Math.Min(1.75, value)), 2);
        if (view.CoreWebView2 != null) view.ZoomFactor = Program.Zoom;
        Program.SavePreferences(); UpdateStatus();
    }
    private void UpdateStatus()
    {
        if (view.CoreWebView2 != null)
            view.CoreWebView2.ExecuteScriptAsync("window.dispatchEvent(new CustomEvent('voice-loom-display',{detail:{zoom:" + Program.Zoom.ToString(CultureInfo.InvariantCulture) + ",dpi:" + DeviceDpi + "}}))");
    }
    private void UpdateCaption()
    {
        int dark = 1, background = 0x0036261c, foreground = 0x00f6ede6;
        DwmSetWindowAttribute(Handle, 20, ref dark, 4);
        DwmSetWindowAttribute(Handle, 35, ref background, 4);
        DwmSetWindowAttribute(Handle, 36, ref foreground, 4);
    }
    private void DesktopAction(Uri action)
    {
        if (action.Host != "desktop" || !String.IsNullOrEmpty(action.Query) || !String.IsNullOrEmpty(action.UserInfo)) return;
        switch (action.AbsolutePath)
        {
            case "/data": Process.Start("explorer.exe", "\"" + Program.Data + "\""); break;
            case "/logs": Process.Start("explorer.exe", "\"" + Program.Home + "\""); break;
            case "/help":
                string guide = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "使用说明.txt");
                if (File.Exists(guide)) Process.Start(new ProcessStartInfo(guide) { UseShellExecute = true }); break;
            case "/compare": new LoomWindow(backend, false, "/comparison/index.html").Show(this); break;
            case "/zoom-in": SetZoom(Program.Zoom + .1); break;
            case "/zoom-out": SetZoom(Program.Zoom - .1); break;
            case "/zoom-reset": SetZoom(1); break;
            case "/quit": Close(); break;
        }
    }

    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        if (keyData == (Keys.Control | Keys.Oemplus) || keyData == (Keys.Control | Keys.Add)) { SetZoom(Program.Zoom + .1); return true; }
        if (keyData == (Keys.Control | Keys.OemMinus) || keyData == (Keys.Control | Keys.Subtract)) { SetZoom(Program.Zoom - .1); return true; }
        if (keyData == (Keys.Control | Keys.D0)) { SetZoom(1); return true; }
        return base.ProcessCmdKey(ref msg, keyData);
    }

    private async void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (mayClose || !primary) return;
        e.Cancel = true; if (closing) return; closing = true;
        try
        {
            if (ready && backend.IsLocal(view.Source.AbsoluteUri))
            {
                string dirty = await view.CoreWebView2.ExecuteScriptAsync("document.documentElement.dataset.unsavedChanges==='true'");
                if (dirty == "true" && MessageBox.Show(this, "有尚未保存的设置。退出会放弃这些修改，是否退出？", "声织", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) != DialogResult.Yes)
                { closing = false; return; }
            }
            Enabled = false; Text = "声织 · 正在停止监听并退出…";
            await backend.Stop(true);
            Program.SavePreferences(); mayClose = true; Close();
        }
        catch (Exception error) { Program.Log(error); mayClose = true; Close(); }
    }

    private async Task VerifyRendering()
    {
        string output = Program.Diagnostics; Program.Diagnostics = null;
        Directory.CreateDirectory(output);
        try
        {
            for (int attempt = 0; attempt < 80; attempt++)
            { if (await view.CoreWebView2.ExecuteScriptAsync("!!document.querySelector('.app-shell')") == "true") break; await Task.Delay(100); }
            List<object> checks = new List<object>();
            view.ZoomFactor = 1.0;
            foreach (double scale in new double[] { 1, 1.25, 1.5, 2 })
            {
                await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setDeviceMetricsOverride",
                    Program.Json.Serialize(new { width = Math.Max(320, (int)(view.ClientSize.Width / scale)), height = Math.Max(200, (int)(view.ClientSize.Height / scale)), deviceScaleFactor = scale, mobile = false }));
                await Task.Delay(400);
                string measured = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify({dpr:devicePixelRatio,width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,font:getComputedStyle(document.body).fontFamily,ready:!!document.querySelector('.app-shell'),audioPlaying:[...document.querySelectorAll('audio')].filter(x=>!x.paused).length})");
                string decoded = Program.Json.Deserialize<string>(measured);
                Dictionary<string, object> layout = Program.Json.Deserialize<Dictionary<string, object>>(decoded);
                bool monitorV2 = AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), new IntPtr(-4));
                checks.Add(new { scale = scale, method = "renderer_device_scale_emulation", per_monitor_v2 = monitorV2, native_dpi = DeviceDpi, layout = layout });
                using (FileStream file = File.Create(Path.Combine(output, "desktop-" + Math.Round(scale * 100) + ".png")))
                    await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, file);
            }
            File.WriteAllText(Path.Combine(output, "dpi-rendering.json"), Program.Json.Serialize(checks), new UTF8Encoding(false));
            await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.clearDeviceMetricsOverride", "{}"); view.ZoomFactor = Program.Zoom;
            UpdateStatus();
        }
        catch (Exception error) { Program.Log(error); File.WriteAllText(Path.Combine(output, "diagnostic-error.txt"), error.ToString()); }
    }
}

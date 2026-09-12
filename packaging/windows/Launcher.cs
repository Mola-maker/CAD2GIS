// Thin process launcher: all behavior lives in the installed Python package.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;

class Launcher
{
    [StructLayout(LayoutKind.Sequential)]
    struct BasicJobLimits
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JobIoCounters
    {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedJobLimits
    {
        public BasicJobLimits BasicLimitInformation;
        public JobIoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int informationClass, IntPtr information, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);

    // Intentionally owned until OS process teardown, not a finalizable handle:
    // closing this handle while Main is running would also terminate this launcher.
    static IntPtr desktopJob;

    static void OwnDesktopProcessTree()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            var limits = new ExtendedJobLimits();
            limits.BasicLimitInformation.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            int size = Marshal.SizeOf(typeof(ExtendedJobLimits));
            IntPtr buffer = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(limits, buffer, false);
                if (!SetInformationJobObject(job, 9, buffer, (uint)size))
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
            finally { Marshal.FreeHGlobal(buffer); }
            // Bind the launcher BEFORE starting Python. Descendants inherit
            // membership, including during startup; there is no unowned-child gap.
            using (var current = Process.GetCurrentProcess())
                if (!AssignProcessToJobObject(job, current.Handle))
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            desktopJob = job;
        }
        catch { CloseHandle(job); throw; }
    }

    // CommandLineToArgvW-compatible quoting, including trailing backslashes.
    static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    static int Main(string[] args)
    {
        string executable = Assembly.GetExecutingAssembly().Location;
        string name = Path.GetFileNameWithoutExtension(executable);
        string root = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(executable), ".."));
        bool desktop = name == "cad2gis-desktop";
        string module = desktop ? "cad2gis.application" :
            name == "cad2gis-agent-mcp" ? "cad2gis.agent_mcp" : "cad2gis.cli";
        switch (name)
        {
            case "cad2gis-symbols": module = "cad2gis.symbol_assets"; break;
            case "cad2gis-qgis-package": module = "cad2gis.qgis_package"; break;
            case "cad2gis-qgis-verify": module = "cad2gis.qgis_verify"; break;
            case "cad2gis-qgis-review": module = "cad2gis.qgis_review"; break;
            case "cad2gis-qgis-review-verify": module = "cad2gis.qgis_review_verify"; break;
            case "cad2gis-svg-delivery": module = "cad2gis.svg_delivery"; break;
        }
        var arguments = new StringBuilder("-I -B -X utf8 -m " + module);
        foreach (string arg in args) arguments.Append(" " + Quote(arg));
        var start = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"), arguments.ToString());
        start.UseShellExecute = false;
        start.CreateNoWindow = desktop;
        start.EnvironmentVariables["CAD2GIS_BUNDLE_ROOT"] = root;
        if (String.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("CAD2GIS_LIBREDWG_CLI")))
            start.EnvironmentVariables["CAD2GIS_LIBREDWG_CLI"] = Path.Combine(root, "libredwg", "dwg2dxf.exe");
        start.EnvironmentVariables["PATH"] = Path.Combine(root, "bin") + ";" + start.EnvironmentVariables["PATH"];
        start.EnvironmentVariables.Remove("PYTHONPATH");
        start.EnvironmentVariables.Remove("PYTHONHOME");
        try
        {
            if (desktop) OwnDesktopProcessTree();
            using (var process = new Process())
            {
                process.StartInfo = start;
                StreamWriter log = null;
                if (desktop)
                {
                    string stateDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CAD2GIS");
                    for (int i = 0; i < args.Length; i++)
                    {
                        if (args[i] == "--state-dir" && i + 1 < args.Length) stateDirectory = Path.GetFullPath(args[++i]);
                        else if (args[i].StartsWith("--state-dir=", StringComparison.Ordinal)) stateDirectory = Path.GetFullPath(args[i].Substring(12));
                    }
                    string logs = Path.Combine(stateDirectory, "logs");
                    Directory.CreateDirectory(logs);
                    log = new StreamWriter(Path.Combine(logs, "desktop-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff") + ".log"));
                    log.AutoFlush = true;
                    start.RedirectStandardOutput = true;
                    start.RedirectStandardError = true;
                    start.StandardOutputEncoding = Encoding.UTF8;
                    start.StandardErrorEncoding = Encoding.UTF8;
                    process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
                    process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
                }
                process.Start();
                if (desktop) { process.BeginOutputReadLine(); process.BeginErrorReadLine(); }
                process.WaitForExit();
                if (log != null) log.Dispose();
                if (desktop && process.ExitCode != 0)
                    System.Windows.Forms.MessageBox.Show("启动未完成。请查看用户本地应用数据目录 CAD2GIS/logs 中的启动日志。", "CAD2GIS");
                return process.ExitCode;
            }
        }
        catch (Exception e)
        {
            if (desktop) System.Windows.Forms.MessageBox.Show(e.Message, "CAD2GIS 启动失败");
            else Console.Error.WriteLine("CAD2GIS launcher: " + e.Message);
            return 1;
        }
    }
}

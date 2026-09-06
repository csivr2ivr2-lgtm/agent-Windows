using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Program
{
    private static string Quote(string value)
    {
        if (String.IsNullOrEmpty(value)) return "\"\"";
        if (value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return value;
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string installRoot = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
        string stateRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "AgentWindowsAI"
        );
        string python = Path.Combine(installRoot, "python-runtime", "pythonw.exe");
        string envFile = Path.Combine(stateRoot, ".env");
        string tools = Path.Combine(installRoot, "tools");

        if (!File.Exists(python) || !File.Exists(envFile))
        {
            MessageBox.Show(
                "AI Aharon installation is incomplete. Run the installer again.",
                "AI Aharon",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 2;
        }

        var command = new StringBuilder();
        command.Append("-m agent_windows.desktop_gui --env ");
        command.Append(Quote(envFile));
        bool minimized = Array.Exists(
            args,
            arg => String.Equals(arg, "--minimized", StringComparison.Ordinal)
        );
        if (minimized)
        {
            command.Append(" --minimized");
        }

        var start = new ProcessStartInfo();
        start.FileName = python;
        start.Arguments = command.ToString();
        start.WorkingDirectory = stateRoot;
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.EnvironmentVariables["AGENT_WINDOWS_HOME"] = stateRoot;
        start.EnvironmentVariables["AGENT_WINDOWS_INSTALL_ROOT"] = installRoot;
        if (Directory.Exists(tools))
        {
            start.EnvironmentVariables["PATH"] = tools + ";" + start.EnvironmentVariables["PATH"];
        }

        try
        {
            Process.Start(start);
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "Could not start AI Aharon.\n\n" + ex.Message,
                "AI Aharon",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }
    }
}

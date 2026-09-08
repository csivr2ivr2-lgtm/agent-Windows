using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Program
{
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
        string envTemplate = Path.Combine(installRoot, ".env.example");
        string tools = Path.Combine(installRoot, "tools");

        if (!File.Exists(python))
        {
            MessageBox.Show(
                "AI Aharon cannot find its bundled Python runtime.\n\nMissing: " + python +
                "\n\nRun the latest installer again.",
                "AI Aharon",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 2;
        }

        try
        {
            Directory.CreateDirectory(stateRoot);
            if (!File.Exists(envFile))
            {
                if (File.Exists(envTemplate))
                {
                    File.Copy(envTemplate, envFile, false);
                }
                else
                {
                    File.WriteAllText(envFile, "# AI Aharon local settings\r\n");
                }
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "AI Aharon could not initialize its local settings.\n\n" + ex.Message,
                "AI Aharon",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 2;
        }

        bool minimized = Array.Exists(
            args,
            arg => String.Equals(arg, "--minimized", StringComparison.Ordinal)
        );

        var start = new ProcessStartInfo();
        start.FileName = python;
        start.Arguments = minimized ? "-m agent_windows.first_run --minimized" : "-m agent_windows.first_run";
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

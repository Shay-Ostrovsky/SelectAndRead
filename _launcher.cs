using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

[assembly: System.Reflection.AssemblyTitle("SelectAndRead")]
[assembly: System.Reflection.AssemblyProduct("SelectAndRead")]

class Program {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern void SetCurrentProcessExplicitAppUserModelID(string AppID);

    static void Main() {
        try { SetCurrentProcessExplicitAppUserModelID("SelectAndRead.App"); } catch {}

        string appDir = Path.GetDirectoryName(
            System.Reflection.Assembly.GetExecutingAssembly().Location);

        string python;
        string cfg = Path.Combine(appDir, "python_path.txt");
        if (File.Exists(cfg))
            python = File.ReadAllText(cfg).Trim().Replace("pythonw.exe", "python.exe");
        else
            python = Path.Combine(appDir, "venv", "Scripts", "python.exe");

        if (!File.Exists(python)) {
            System.Windows.Forms.MessageBox.Show(
                "Python not found. Run setup.bat first.\n\nLooked for:\n" + python,
                "SelectAndRead",
                System.Windows.Forms.MessageBoxButtons.OK,
                System.Windows.Forms.MessageBoxIcon.Error);
            return;
        }

        string mainPy = Path.Combine(appDir, "main.py");
        Process.Start(new ProcessStartInfo {
            FileName         = python,
            Arguments        = "\"" + mainPy + "\"",
            WorkingDirectory = appDir,
            UseShellExecute  = false,
            CreateNoWindow   = true,
        });
    }
}

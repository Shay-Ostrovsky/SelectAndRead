using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("SelectAndRead")]
[assembly: System.Reflection.AssemblyProduct("SelectAndRead")]

class Program {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern void SetCurrentProcessExplicitAppUserModelID(string AppID);

    [STAThread]
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
            MessageBox.Show("Python not found. Run setup.bat first.\n\nLooked for:\n" + python,
                            "SelectAndRead",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        string mainPy   = Path.Combine(appDir, "main.py");
        string iconPath = Path.Combine(appDir, "icon.ico");

        Process pyProc;
        try {
            pyProc = Process.Start(new ProcessStartInfo {
                FileName         = python,
                Arguments        = "\"" + mainPy + "\"",
                WorkingDirectory = appDir,
                UseShellExecute  = false,
                CreateNoWindow   = true,
            });
        } catch (Exception ex) {
            MessageBox.Show("Failed to launch Python:\n" + ex.Message, "SelectAndRead",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        if (pyProc == null) return;

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SplashForm(iconPath, pyProc));
    }
}

class SplashForm : Form {
    private readonly Process pyProc;
    private readonly Timer pollTimer;
    private readonly Timer animTimer;
    private readonly Label loadingLabel;
    private int dots = 0;
    private int polls = 0;

    public SplashForm(string iconPath, Process pyProc) {
        this.pyProc = pyProc;

        FormBorderStyle = FormBorderStyle.None;
        StartPosition   = FormStartPosition.CenterScreen;
        Size            = new Size(320, 140);
        BackColor       = Color.FromArgb(28, 28, 32);
        ForeColor       = Color.White;
        TopMost         = true;
        ShowInTaskbar   = false;
        Text            = "SelectAndRead Loading";

        Paint += (s, e) => {
            using (var pen = new Pen(Color.FromArgb(80, 80, 95), 1))
                e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
        };

        if (File.Exists(iconPath)) {
            try {
                Icon ico = new Icon(iconPath, 64, 64);
                this.Icon = ico;
                Controls.Add(new PictureBox {
                    Image     = ico.ToBitmap(),
                    SizeMode  = PictureBoxSizeMode.CenterImage,
                    Location  = new Point(24, 36),
                    Size      = new Size(72, 72),
                    BackColor = Color.Transparent,
                });
            } catch {}
        }

        Controls.Add(new Label {
            Text      = "SelectAndRead",
            ForeColor = Color.White,
            Font      = new Font("Segoe UI", 14, FontStyle.Bold),
            AutoSize  = false,
            Location  = new Point(110, 38),
            Size      = new Size(195, 28),
            BackColor = Color.Transparent,
        });

        loadingLabel = new Label {
            Text      = "Loading...",
            ForeColor = Color.FromArgb(170, 170, 180),
            Font      = new Font("Segoe UI", 10),
            AutoSize  = false,
            Location  = new Point(110, 70),
            Size      = new Size(195, 22),
            BackColor = Color.Transparent,
        };
        Controls.Add(loadingLabel);

        animTimer = new Timer { Interval = 350 };
        animTimer.Tick += (s, e) => {
            dots = (dots + 1) % 4;
            loadingLabel.Text = "Loading" + new string('.', dots);
        };
        animTimer.Start();

        pollTimer = new Timer { Interval = 250 };
        pollTimer.Tick += (s, e) => {
            polls++;
            try {
                if (pyProc.HasExited) { Close(); return; }
                pyProc.Refresh();
                if (pyProc.MainWindowHandle != IntPtr.Zero) { Close(); return; }
            } catch { Close(); return; }
            if (polls > 480) Close();   // 2 min timeout
        };
        pollTimer.Start();
    }

    protected override void OnFormClosing(FormClosingEventArgs e) {
        animTimer.Stop();
        pollTimer.Stop();
        base.OnFormClosing(e);
    }
}

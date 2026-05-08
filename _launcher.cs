using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
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
    [DllImport("user32.dll")]
    static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int GetWindowTextLength(IntPtr hWnd);
    delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    private float angle = 0;
    private readonly Timer animTimer;
    private readonly Timer pollTimer;
    private readonly Process pyProc;
    private Image iconImage;
    private int polls = 0;

    public SplashForm(string iconPath, Process pyProc) {
        this.pyProc = pyProc;

        FormBorderStyle = FormBorderStyle.None;
        StartPosition   = FormStartPosition.CenterScreen;
        Size            = new Size(170, 170);
        BackColor       = Color.FromArgb(20, 20, 26);
        TopMost         = true;
        ShowInTaskbar   = false;
        DoubleBuffered  = true;
        Text            = "SelectAndRead Loading";

        // Rounded corners
        using (var path = new GraphicsPath()) {
            int r = 22;
            path.AddArc(0,         0,         r, r, 180, 90);
            path.AddArc(Width-r-1, 0,         r, r, 270, 90);
            path.AddArc(Width-r-1, Height-r-1,r, r,   0, 90);
            path.AddArc(0,         Height-r-1,r, r,  90, 90);
            path.CloseFigure();
            Region = new Region(path);
        }

        if (File.Exists(iconPath)) {
            try { iconImage = new Icon(iconPath, 64, 64).ToBitmap(); } catch {}
        }

        Paint += OnPaint;

        animTimer = new Timer { Interval = 16 };  // ~60 fps
        animTimer.Tick += (s, e) => {
            angle = (angle + 4f) % 360f;
            Invalidate();
        };
        animTimer.Start();

        pollTimer = new Timer { Interval = 200 };
        pollTimer.Tick += (s, e) => {
            polls++;
            try {
                if (pyProc.HasExited) { Close(); return; }
                if (HasVisibleWindow((uint)pyProc.Id)) { Close(); return; }
            } catch { Close(); return; }
            if (polls > 900) Close();   // ~3 min timeout
        };
        pollTimer.Start();
    }

    void OnPaint(object sender, PaintEventArgs e) {
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;

        int cx = Width / 2, cy = Height / 2;
        int outerR = Math.Min(Width, Height) / 2 - 12;
        var ringRect = new Rectangle(cx - outerR, cy - outerR, outerR * 2, outerR * 2);

        // Faint background ring
        using (var bgPen = new Pen(Color.FromArgb(30, 255, 180, 60), 5f)) {
            g.DrawArc(bgPen, ringRect, 0, 360);
        }

        // Spinning gold-orange arc (~110 degrees)
        using (var pen = new Pen(Color.FromArgb(255, 255, 175, 50), 5f)) {
            pen.StartCap = LineCap.Round;
            pen.EndCap   = LineCap.Round;
            g.DrawArc(pen, ringRect, angle, 110);
        }

        // Brighter "head" of the arc
        using (var pen = new Pen(Color.FromArgb(255, 255, 215, 0), 5f)) {
            pen.StartCap = LineCap.Round;
            pen.EndCap   = LineCap.Round;
            g.DrawArc(pen, ringRect, angle + 80, 30);
        }

        // App icon in the center
        if (iconImage != null) {
            int s = 56;
            g.DrawImage(iconImage, cx - s/2, cy - s/2, s, s);
        }
    }

    static bool HasVisibleWindow(uint pid) {
        bool found = false;
        EnumWindows((hWnd, lParam) => {
            uint procId;
            GetWindowThreadProcessId(hWnd, out procId);
            if (procId == pid && IsWindowVisible(hWnd) && GetWindowTextLength(hWnd) > 0) {
                found = true;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    protected override void OnFormClosing(FormClosingEventArgs e) {
        animTimer.Stop();
        pollTimer.Stop();
        base.OnFormClosing(e);
    }
}

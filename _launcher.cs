using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
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
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc p, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr h);
    delegate bool EnumWindowsProc(IntPtr h, IntPtr l);

    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr h);
    [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr h, IntPtr dc);
    [DllImport("user32.dll")] static extern bool UpdateLayeredWindow(
        IntPtr hwnd, IntPtr hdcDst, ref POINT pptDst, ref SIZE psize,
        IntPtr hdcSrc, ref POINT pprSrc, int crKey, ref BLENDFUNCTION pblend, int dwFlags);
    [DllImport("gdi32.dll")] static extern IntPtr CreateCompatibleDC(IntPtr h);
    [DllImport("gdi32.dll")] static extern bool DeleteDC(IntPtr dc);
    [DllImport("gdi32.dll")] static extern IntPtr SelectObject(IntPtr dc, IntPtr o);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr o);

    [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct SIZE { public int cx, cy; }
    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    struct BLENDFUNCTION { public byte Op, Flags, Alpha, Format; }

    const int GWL_EXSTYLE       = -20;
    const int WS_EX_LAYERED     = 0x00080000;
    const int WS_EX_TOOLWINDOW  = 0x00000080;
    const int ULW_ALPHA         = 0x02;
    const byte AC_SRC_OVER      = 0x00;
    const byte AC_SRC_ALPHA     = 0x01;

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
        TopMost         = true;
        ShowInTaskbar   = false;
        Text            = "SelectAndRead Loading";

        if (File.Exists(iconPath)) {
            try { iconImage = new Icon(iconPath, 64, 64).ToBitmap(); } catch {}
        }

        animTimer = new Timer { Interval = 16 };
        animTimer.Tick += (s, e) => {
            angle = (angle + 4f) % 360f;
            Render();
        };

        pollTimer = new Timer { Interval = 200 };
        pollTimer.Tick += (s, e) => {
            polls++;
            try {
                if (pyProc.HasExited) { Close(); return; }
                if (HasSelectAndReadWindow((uint)pyProc.Id)) { Close(); return; }
            } catch { Close(); return; }
            if (polls > 1500) Close();   // ~5 min safety timeout
        };
    }

    protected override CreateParams CreateParams {
        get {
            var cp = base.CreateParams;
            cp.ExStyle |= WS_EX_LAYERED | WS_EX_TOOLWINDOW;
            return cp;
        }
    }

    protected override void OnShown(EventArgs e) {
        base.OnShown(e);
        Render();
        animTimer.Start();
        pollTimer.Start();
    }

    void Render() {
        using (var bmp = new Bitmap(Width, Height, PixelFormat.Format32bppArgb))
        using (var g = Graphics.FromImage(bmp)) {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;

            int cx = Width / 2, cy = Height / 2;
            int outerR = Math.Min(Width, Height) / 2 - 12;
            var ringRect = new Rectangle(cx - outerR, cy - outerR, outerR * 2, outerR * 2);

            // Faint background ring
            using (var bgPen = new Pen(Color.FromArgb(60, 255, 180, 60), 6f)) {
                g.DrawArc(bgPen, ringRect, 0, 360);
            }

            // Trailing arc
            using (var pen = new Pen(Color.FromArgb(255, 255, 165, 40), 6f)) {
                pen.StartCap = LineCap.Round;
                pen.EndCap   = LineCap.Round;
                g.DrawArc(pen, ringRect, angle, 110);
            }

            // Brighter head
            using (var pen = new Pen(Color.FromArgb(255, 255, 220, 0), 6f)) {
                pen.StartCap = LineCap.Round;
                pen.EndCap   = LineCap.Round;
                g.DrawArc(pen, ringRect, angle + 80, 30);
            }

            if (iconImage != null) {
                int s = 56;
                g.DrawImage(iconImage, cx - s/2, cy - s/2, s, s);
            }

            PushBitmap(bmp);
        }
    }

    void PushBitmap(Bitmap bmp) {
        IntPtr screenDc = GetDC(IntPtr.Zero);
        IntPtr memDc    = CreateCompatibleDC(screenDc);
        IntPtr hBmp     = bmp.GetHbitmap(Color.FromArgb(0));
        IntPtr oldBmp   = SelectObject(memDc, hBmp);

        var size  = new SIZE  { cx = bmp.Width, cy = bmp.Height };
        var pos   = new POINT { X = Left,       Y = Top         };
        var src   = new POINT { X = 0,          Y = 0           };
        var blend = new BLENDFUNCTION {
            Op     = AC_SRC_OVER, Flags  = 0,
            Alpha  = 255,         Format = AC_SRC_ALPHA,
        };

        try {
            UpdateLayeredWindow(Handle, screenDc, ref pos, ref size,
                                memDc, ref src, 0, ref blend, ULW_ALPHA);
        } finally {
            SelectObject(memDc, oldBmp);
            DeleteObject(hBmp);
            DeleteDC(memDc);
            ReleaseDC(IntPtr.Zero, screenDc);
        }
    }

    static bool HasSelectAndReadWindow(uint pid) {
        bool found = false;
        EnumWindows((hWnd, lParam) => {
            uint procId;
            GetWindowThreadProcessId(hWnd, out procId);
            if (procId == pid && IsWindowVisible(hWnd)) {
                int len = GetWindowTextLength(hWnd);
                if (len > 0) {
                    var sb = new StringBuilder(len + 1);
                    GetWindowText(hWnd, sb, len + 1);
                    if (sb.ToString() == "SelectAndRead") {
                        found = true;
                        return false;
                    }
                }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    protected override void OnFormClosing(FormClosingEventArgs e) {
        animTimer.Stop();
        pollTimer.Stop();
        if (iconImage != null) { iconImage.Dispose(); iconImage = null; }
        base.OnFormClosing(e);
    }
}

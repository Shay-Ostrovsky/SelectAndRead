# Creates a "SelectAndRead" shortcut on the Desktop.
# Run via create_shortcut.bat (double-click)

$appDir  = $PSScriptRoot
$exeFile = Join-Path $appDir "SelectAndRead.exe"
$vbsFile = Join-Path $appDir "_launch.vbs"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "SelectAndRead.lnk"

$shell                     = New-Object -ComObject WScript.Shell
$shortcut                  = $shell.CreateShortcut($lnkPath)

if (Test-Path $exeFile) {
    # Preferred: target the real .exe so taskbar pinning works correctly
    $shortcut.TargetPath = $exeFile
    $shortcut.Arguments  = ""
} elseif (Test-Path $vbsFile) {
    # Fallback: VBS launcher (taskbar pin will be less clean)
    $wscript = "$env:SystemRoot\System32\wscript.exe"
    $shortcut.TargetPath = $wscript
    $shortcut.Arguments  = "//B //Nologo `"$vbsFile`""
} else {
    Write-Host "ERROR: Neither SelectAndRead.exe nor _launch.vbs found in $appDir" -ForegroundColor Red
    Write-Host "Run setup.bat first." -ForegroundColor Yellow
    pause; exit 1
}

$shortcut.WorkingDirectory = $appDir
$shortcut.Description      = "SelectAndRead"
$iconIco = Join-Path $appDir "icon.ico"
if (Test-Path $iconIco) { $shortcut.IconLocation = $iconIco }
$shortcut.Save()

# Stamp AppUserModelID on the shortcut so the pinned button and the
# running app (which also sets this ID) merge into one taskbar button.
$aumidOk = $false
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    [PreserveSig] int GetCount(out uint c);
    [PreserveSig] int GetAt(uint i, out PKEY k);
    [PreserveSig] int GetValue(ref PKEY k, IntPtr pv);
    [PreserveSig] int SetValue(ref PKEY k, IntPtr pv);
    [PreserveSig] int Commit();
}
[StructLayout(LayoutKind.Sequential, Pack=4)]
public struct PKEY { public Guid fmtid; public uint pid; }
public static class Aumid {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    static extern int SHGetPropertyStoreFromParsingName(
        string path, IntPtr pbc, int flags, ref Guid iid, out IPropertyStore ps);
    [DllImport("ole32.dll")] static extern int PropVariantClear(IntPtr pv);

    static readonly Guid IID_IPropertyStore = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
    static PKEY PKEY_AppUserModel_ID = new PKEY {
        fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5
    };

    public static bool Set(string path, string id) {
        var iid = IID_IPropertyStore;
        IPropertyStore ps;
        if (SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 6, ref iid, out ps) != 0)
            return false;

        IntPtr pv = Marshal.AllocCoTaskMem(24);
        IntPtr str = IntPtr.Zero;
        bool ok = false;
        try {
            for (int i = 0; i < 24; i++) Marshal.WriteByte(pv, i, 0);
            Marshal.WriteInt16(pv, 0, 31);            // VT_LPWSTR
            str = Marshal.StringToCoTaskMemUni(id);
            Marshal.WriteIntPtr(pv, 8, str);
            int hr = ps.SetValue(ref PKEY_AppUserModel_ID, pv);
            if (hr == 0) { ps.Commit(); ok = true; }
        } finally {
            if (str != IntPtr.Zero) Marshal.FreeCoTaskMem(str);
            Marshal.FreeCoTaskMem(pv);
            Marshal.ReleaseComObject(ps);
        }
        return ok;
    }

    public static string Get(string path) {
        var iid = IID_IPropertyStore;
        IPropertyStore ps;
        if (SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 0, ref iid, out ps) != 0)
            return null;
        IntPtr pv = Marshal.AllocCoTaskMem(24);
        try {
            for (int i = 0; i < 24; i++) Marshal.WriteByte(pv, i, 0);
            if (ps.GetValue(ref PKEY_AppUserModel_ID, pv) != 0) return null;
            ushort vt = (ushort)Marshal.ReadInt16(pv, 0);
            if (vt != 31) return null;
            IntPtr s = Marshal.ReadIntPtr(pv, 8);
            string val = s == IntPtr.Zero ? null : Marshal.PtrToStringUni(s);
            PropVariantClear(pv);
            return val;
        } finally {
            Marshal.FreeCoTaskMem(pv);
            Marshal.ReleaseComObject(ps);
        }
    }
}
'@ -ErrorAction Stop
    [void][Aumid]::Set($lnkPath, "SelectAndRead.App")
    $check = [Aumid]::Get($lnkPath)
    if ($check -eq "SelectAndRead.App") { $aumidOk = $true }
} catch {
    Write-Host "AUMID stamping failed: $_" -ForegroundColor Yellow
}

Write-Host "Shortcut created at: $lnkPath" -ForegroundColor Green
if ($aumidOk) {
    Write-Host "AppUserModelID set successfully (taskbar grouping will work)." -ForegroundColor Green
} else {
    Write-Host "AppUserModelID could NOT be verified -- pinned button may not merge with the running app." -ForegroundColor Yellow
}
pause

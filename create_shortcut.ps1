# Creates a "SelectAndRead" shortcut on the Desktop.
# Run via create_shortcut.bat (double-click)

$appDir  = $PSScriptRoot
$vbsFile = Join-Path $appDir "_launch.vbs"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "SelectAndRead.lnk"
$wscript = "$env:SystemRoot\System32\wscript.exe"

if (-not (Test-Path $vbsFile)) {
    Write-Host "ERROR: _launch.vbs not found in $appDir" -ForegroundColor Red
    pause; exit 1
}

$shell                     = New-Object -ComObject WScript.Shell
$shortcut                  = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath       = $wscript
$shortcut.Arguments        = "//B //Nologo `"$vbsFile`""
$shortcut.WorkingDirectory = $appDir
$shortcut.Description      = "SelectAndRead"
$iconIco = Join-Path $appDir "icon.ico"
if (Test-Path $iconIco) { $shortcut.IconLocation = $iconIco }
else                    { $shortcut.IconLocation = "$wscript,0" }
$shortcut.Save()

# Stamp AppUserModelID on the shortcut so the pinned button and
# the running app (which also sets this ID) merge into one taskbar button.
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
    [PreserveSig] int SetValue(ref PKEY k, ref PROPVAR pv);
    [PreserveSig] int Commit();
}
[StructLayout(LayoutKind.Sequential, Pack=4)]
public struct PKEY { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit, Size=16)]
public struct PROPVAR {
    [FieldOffset(0)] public ushort vt;
    [FieldOffset(8)] public IntPtr ptr;
}
public static class Aumid {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    static extern int SHGetPropertyStoreFromParsingName(
        string path, IntPtr pbc, int flags, ref Guid iid, out IPropertyStore ps);
    public static void Set(string path, string id) {
        var iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore ps;
        if (SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 6, ref iid, out ps) != 0) return;
        var key = new PKEY { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
        var pv  = new PROPVAR { vt = 31, ptr = Marshal.StringToCoTaskMemUni(id) };
        try   { ps.SetValue(ref key, ref pv); ps.Commit(); }
        finally { Marshal.FreeCoTaskMem(pv.ptr); Marshal.ReleaseComObject(ps); }
    }
}
'@ -ErrorAction SilentlyContinue
    [Aumid]::Set($lnkPath, "SelectAndRead.App")
} catch {}

Write-Host "Shortcut created at: $lnkPath" -ForegroundColor Green
pause

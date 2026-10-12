$ErrorActionPreference = 'Stop'
$appDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$shortcutFile = Join-Path $appDirectory 'Run MontageMaker.lnk'
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutFile)
$shortcut.TargetPath = Join-Path $appDirectory 'run.bat'
$shortcut.WorkingDirectory = $appDirectory
$shortcut.IconLocation = (Join-Path $appDirectory 'assets\montage.ico') + ',0'
$shortcut.Description = 'Open Montage Maker Studio'
$shortcut.WindowStyle = 7
$shortcut.Save()
$desktopShortcutFile = Join-Path $shellObject.SpecialFolders.Item('Desktop') 'Montage Maker Studio.lnk'
$desktopShortcut = $shellObject.CreateShortcut($desktopShortcutFile)
$desktopShortcut.TargetPath = Join-Path $appDirectory 'run.bat'
$desktopShortcut.WorkingDirectory = $appDirectory
$desktopShortcut.IconLocation = (Join-Path $appDirectory 'assets\montage.ico') + ',0'
$desktopShortcut.Description = 'Open Montage Maker Studio'
$desktopShortcut.WindowStyle = 7
$desktopShortcut.Save()
Write-Output 'Run MontageMaker shortcut created.'

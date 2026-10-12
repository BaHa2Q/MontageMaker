"""Desktop executable entry point with persistent startup diagnostics."""
import json
import os
import sys
import traceback


if __name__ == '__main__':
    try:
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            # Qt uses Windows ICU; OpenCV's bundled ICU has different exported names.
            # Load the OS implementation before either library to avoid a DLL-name clash.
            import ctypes
            windows_icu = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'icuuc.dll')
            ctypes.WinDLL(windows_icu)
        from app import main
        result = main()
    except Exception as ex:
        error = traceback.format_exc()
        folder = os.path.join(os.environ.get('LOCALAPPDATA', os.path.dirname(sys.executable)),
                              'MontageMakerStudio', 'Cache')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'startup_error.txt'), 'w', encoding='utf-8') as handle:
            handle.write(error)
        if len(sys.argv) == 4 and sys.argv[1] == '--self-test':
            with open(sys.argv[3], 'w', encoding='utf-8') as handle:
                json.dump(dict(ok=False, error=str(ex), traceback=error), handle, indent=2)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, 'تعذّر تشغيل البرنامج. التفاصيل في:\n' +
                os.path.join(folder, 'startup_error.txt'), 'Montage Maker Studio', 0x10)
        result = 1
    sys.exit(result)

# Montage Maker Studio 3

محرر فيديو لويندوز بواجهة عربية، عرض مباشر، مسارات صوت متعددة، قوالب حركة وخلفيات، وتحديد عناصر لحمايتها من تأثيرات الخلفية.

## تشغيل السورس

1. ثبّت Python 3.12 على Windows 64-bit.
2. شغّل `setup.bat` لتجهيز المكتبات وFFmpeg.
3. شغّل `run.bat` لفتح البرنامج.

التعليمات الكاملة موجودة في [README.txt](README.txt).

## الملفات

- `app.py`: نقطة تشغيل المحرر.
- `ui/`: الواجهة، المكتبة، المسارات والصوت والتحديد.
- `engine/`: معالجة الفيديو، العرض المباشر، المؤثرات والصوت.
- `assets/`: أيقونة البرنامج.
- `installer.iss`: إعداد ملف تنصيب واحد باستخدام Inno Setup.

## تجهيز التنصيب

بعد تشغيل `setup.bat`، شغّل الأوامر التالية من مجلد المشروع:

```powershell
.\venv\Scripts\python.exe -m pip install PyInstaller==6.22.3
.\venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name MontageMaker --icon assets\montage.ico --add-data "assets;assets" --add-data "ffmpeg\bin;ffmpeg\bin" launcher.py
```

ثبّت Inno Setup 6 من موقعه الرسمي، ثم افتح `installer.iss` واختر Compile. ملف التنصيب الناتج يكون `dist/MontageMaker-Setup.exe`.

المستودع يحتوي السورس والأيقونة فقط. ملفات الفيديو والصوت والمشاريع وبيئة Python والملفات التنفيذية لا تدخل في Git.

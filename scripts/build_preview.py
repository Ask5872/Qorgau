from pathlib import Path

root = Path(__file__).resolve().parents[1]
page = (root / "web/index.html").read_text(encoding="utf-8")
# Keep a useful first render when a mobile document viewer blocks JavaScript.
page = page.replace('<main id="main"></main>', '<main id="main">' + (root / "web/preview-main.html").read_text(encoding="utf-8") + '</main>')
page = page.replace('<nav id="nav" aria-label="Основная навигация"></nav>', '<nav id="nav" aria-label="Основная навигация">' + (root / "web/preview-nav.html").read_text(encoding="utf-8") + '</nav>')
fallback = '''<div id="preview-fallback-notice" role="status" style="margin:20px 20px 0;padding:18px 20px;border-radius:12px;background:#f4f4f4;color:#3f3f3f;border:1px solid #e5e5e5;font:14px/1.7 Arial,sans-serif">
<strong>Предварительный просмотр Qorgau</strong><br>
Если эта надпись остаётся, просмотрщик не запустил интерактивную часть. Ниже виден начальный экран приложения.
Чтобы пройти демонстрационный тест и нажимать кнопки, скачайте файл и откройте его в Chrome, Edge или Safari на компьютере.
PIN преподавателя в демо: <strong>2026</strong>. Камера в этой демонстрации не используется.
</div>'''
page = page.replace('<main id="main">', fallback + '<main id="main">', 1)
page = page.replace('<link rel="stylesheet" href="/assets/style.css?v=calibration197">', '<style>' + (root / "web/style.css").read_text(encoding="utf-8") + '</style>')
page = page.replace('<script src="/assets/app.js?v=calibration197"></script>', '<script>window.QORGAU_PREVIEW=true;\n' + (root / "web/app.js").read_text(encoding="utf-8") + '</script>')
page = page.replace('__CSRF_TOKEN__', 'preview')
(root / "Qorgau-Preview.html").write_text(page, encoding="utf-8")
print("Built Qorgau-Preview.html")

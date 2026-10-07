#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import socket
import sys
import threading
import time
import webbrowser


def main():
    parser = argparse.ArgumentParser(description="Qorgau — локальная система прокторинга")
    parser.add_argument("--demo", action="store_true", help="Демонстрация без камеры и блокировки")
    parser.add_argument("--desktop", action="store_true", help="Обычное окно; защита Windows только во время теста")
    parser.add_argument("--isolate", action="store_true", help="Окно Windows с изоляцией только активной попытки")
    parser.add_argument("--isolation-control", help=argparse.SUPPRESS)
    parser.add_argument("--exam-supervisor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--exam-window", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reset-pin", action="store_true", help="Сбросить PIN преподавателя")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not 1024 <= args.port <= 65535:
        parser.error("Порт должен быть от 1024 до 65535")
    if args.exam_supervisor or args.exam_window:
        if sys.platform != "win32" or not args.isolation_control or (args.exam_supervisor and args.exam_window):
            parser.error("Внутренний запуск окна экзамена требует наблюдателя Windows")
        if args.exam_supervisor:
            from qorgau.desktop_session import run_supervisor
            return run_supervisor(root, args.isolation_control)
        from qorgau.exam_window import run_exam_window
        return run_exam_window(args.isolation_control)
    if args.isolation_control:
        parser.error("Управление изоляцией доступно только внутреннему окну экзамена")
    if args.isolate:
        if sys.platform != "win32" or args.demo:
            parser.error("Изолированная попытка доступна только для настоящего теста в Windows")
        # Compatibility with earlier launchers. The hub always stays on the
        # normal desktop; each attempt creates its own temporary exam window.
        args.desktop = True
    try:
        with socket.socket() as check:
            check.bind(("127.0.0.1", args.port))
    except OSError as exc:
        parser.error(f"Не удалось открыть локальный порт {args.port}: {exc}. Если Qorgau уже запущен, закройте прежнее окно и его консоль. В защищённой старой версии используйте Ctrl+Shift+Q. Затем запустите 02_start.cmd из обновлённой папки.")
    from qorgau import __version__
    from qorgau.server import create_app
    import uvicorn
    app = create_app(root, args.data or root / "data", args.demo, args.reset_pin, os.getenv("QORGAU_INSTRUCTOR_PIN"))
    rt = app.state.runtime
    url = f"http://127.0.0.1:{args.port}"
    print("\nQORGAU", __version__, "/ локальный прокторинг", flush=True)
    print("Папка проекта:", root, flush=True)
    print("Режим:", "ДЕМОНСТРАЦИЯ" if args.demo else "РЕАЛЬНАЯ КАМЕРА", flush=True)
    print("Адрес:", url, flush=True)
    if rt.auth.initial_pin:
        print("PIN ПРЕПОДАВАТЕЛЯ:", rt.auth.initial_pin, "— сохраните его. Сброс: --reset-pin", flush=True)
        if args.desktop and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "PIN преподавателя: " + rt.auth.initial_pin +
                "\n\nСохраните его для настройки теста и проверки результатов.", "Qorgau — PIN преподавателя", 0x40)
    print("Кабинеты открываются в обычном окне. Защита включается только после начала теста.", flush=True)
    print("Аварийный выход во время теста: Ctrl+Shift+Q.\n", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False))
    if args.desktop:
        try:
            import webview
        except ImportError:
            print("Установите оболочку: python -m pip install -r requirements-desktop.txt")
            return 1
        if sys.platform == "win32" and not args.demo:
            from qorgau.attempt_guard import AttemptDesktopGuard
            rt.guard = AttemptDesktopGuard(rt.emit, rt.emergency, root, url, rt.csrf)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("Локальный сервер не запустился")
        from qorgau.hub_window import HubWindowBridge
        from qorgau.window_layout import normal_window_options, fit_native_window
        bridge = HubWindowBridge(rt)
        window = webview.create_window("Qorgau " + __version__ + " — кабинеты", url,
                                       background_color="#ffffff", text_select=True,
                                       js_api=bridge, **normal_window_options())
        bridge._bind(window)
        rt.guard.window = window
        # No navigation hooks or focus watchdog in the hub. Calibration can
        # voluntarily fill the screen through its short-lived bridge lease.
        # Standard Windows controls belong to the ordinary native frame.
        def shown():
            if sys.platform == "win32":
                fit_native_window(window, free=True)
        window.events.shown += shown
        if sys.platform == "win32":
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
            webview.settings["ALLOW_DOWNLOADS"] = True
        def closing():
            if rt.active_id or rt.cleanup_in_progress:
                return False
        window.events.closing += closing
        try:
            webview.start(debug=False, private_mode=True, gui="edgechromium" if sys.platform == "win32" else None)
        finally:
            bridge._dispose()
            rt.close()
            server.should_exit = True
    else:
        if not args.no_browser:
            def open_when_ready():
                for _ in range(100):
                    if server.started:
                        webbrowser.open(url)
                        return
                    time.sleep(0.05)
            threading.Thread(target=open_when_ready, daemon=True).start()
        server.run()


if __name__ == "__main__":
    raise SystemExit(main() or 0)

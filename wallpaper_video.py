"""ctypes bridge for WallpaperVideoEngine.dll."""
from __future__ import annotations

import atexit
import ctypes
import os
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Optional

PAUSE_ON_FULLSCREEN = 0x1
PAUSE_ON_BATTERY = 0x2
# Keep the wallpaper moving on battery; pause only while a full-screen app is open.
DEFAULT_FLAGS = PAUSE_ON_FULLSCREEN


class VideoWallpaperError(RuntimeError):
    """Native live-wallpaper operation failed."""


class VideoWallpaperEngine:
    def __init__(self, dll_path: Optional[os.PathLike | str] = None) -> None:
        self._requested_path = Path(dll_path).resolve() if dll_path else None
        self._dll = None
        self._lock = threading.Lock()
        self._dll_directory = None
        self._registered = False

    def _candidates(self) -> list[Path]:
        if self._requested_path:
            return [self._requested_path]
        override = os.environ.get("WALLPAPER_VIDEO_DLL")
        if override:
            return [Path(override).expanduser().resolve()]
        if getattr(sys, "frozen", False):
            roots = [Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)),
                     Path(sys.executable).parent]
        else:
            roots = [Path(__file__).resolve().parent]
        return [root / rel for root in roots for rel in (
            Path("native/build-raised-layer5/Release/WallpaperVideoEngine.dll"),
            Path("native/build-raised-layer4/Release/WallpaperVideoEngine.dll"),
            Path("native/build-raised-layer3/Release/WallpaperVideoEngine.dll"),
            Path("native/build-raised-layer2/Release/WallpaperVideoEngine.dll"),
            Path("native/build-desktop-layer/Release/WallpaperVideoEngine.dll"),
            Path("native/build-hotfix/Release/WallpaperVideoEngine.dll"),
            Path("native/build/Release/WallpaperVideoEngine.dll"),
            Path("native/WallpaperVideoEngine.dll"),
            Path("WallpaperVideoEngine.dll"),
        )]

    def _load(self):
        with self._lock:
            if self._dll is not None:
                return self._dll
            if sys.platform != "win32":
                raise VideoWallpaperError("Live video wallpapers require Windows.")
            candidates = self._candidates()
            path = next((p for p in candidates if p.is_file()), None)
            if path is None:
                raise VideoWallpaperError(
                    "WallpaperVideoEngine.dll was not found. Build it or set "
                    "WALLPAPER_VIDEO_DLL. Searched: "
                    + ", ".join(str(p) for p in candidates)
                )
            if hasattr(os, "add_dll_directory"):
                self._dll_directory = os.add_dll_directory(str(path.parent))
            try:
                dll = ctypes.CDLL(str(path))
            except OSError as exc:
                raise VideoWallpaperError(f"Could not load {path}: {exc}") from exc
            dll.WPV_StartW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            dll.WPV_StartW.restype = ctypes.c_int
            for name in ("WPV_Stop", "WPV_Pause", "WPV_Resume"):
                fn = getattr(dll, name)
                fn.argtypes = []
                fn.restype = ctypes.c_int
            for name in ("WPV_GetMonitorsW", "WPV_GetLastErrorW"):
                fn = getattr(dll, name)
                fn.argtypes = [ctypes.POINTER(ctypes.c_wchar), ctypes.c_size_t]
                fn.restype = ctypes.c_size_t
            self._dll = dll
            if not self._registered:
                atexit.register(self.stop)
                self._registered = True
            return dll

    @staticmethod
    def _read_string(fn) -> str:
        count = int(fn(None, 0))
        buffer = ctypes.create_unicode_buffer(max(1, count))
        fn(buffer, len(buffer))
        return buffer.value

    def _check(self, result: int) -> None:
        if result:
            error = self._read_string(self._load().WPV_GetLastErrorW)
            raise VideoWallpaperError(error or f"Native engine error {result}.")

    def list_monitors(self) -> list[str]:
        return [line.strip() for line in
                self._read_string(self._load().WPV_GetMonitorsW).splitlines()
                if line.strip()]

    def start(self, video_path: os.PathLike | str, monitor: str = "all",
              flags: int = DEFAULT_FLAGS) -> None:
        path = Path(video_path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in {".mp4", ".webm"}:
            raise VideoWallpaperError("Choose an existing MP4 or WebM file.")
        if monitor != "all" and monitor not in self.list_monitors():
            raise VideoWallpaperError(f"Display is not connected: {monitor}")
        self._check(self._load().WPV_StartW(str(path), monitor, int(flags)))

    def start_async(self, video_path: os.PathLike | str, monitor: str = "all",
                    flags: int = DEFAULT_FLAGS) -> Future:
        """Start on a worker thread so the PySide6 UI stays responsive."""
        self._load()
        future: Future = Future()

        def run() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                self.start(video_path, monitor, flags)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(None)

        threading.Thread(target=run, name="WallpaperVideoEngine-start",
                         daemon=True).start()
        return future

    def stop(self) -> None:
        if self._dll is not None:
            self._check(self._dll.WPV_Stop())

    def pause(self) -> None:
        self._check(self._load().WPV_Pause())

    def resume(self) -> None:
        self._check(self._load().WPV_Resume())

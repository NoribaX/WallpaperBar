import os
import sys
import ctypes
import hashlib
import json
import keyboard
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import (
    Qt, QPoint, QSize, Signal, QTimer, QPropertyAnimation, QUrl,
    QEasingCurve, QEvent, QMimeData
)
from PySide6 import QtWidgets
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QPainterPath, QShortcut, 
    QKeySequence, QColor, QImage, QDrag, QGuiApplication
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QFileDialog, QScrollArea, QGraphicsDropShadowEffect,
    QDialog, QLabel, QMenu, QSystemTrayIcon, QMessageBox
)
from PySide6.QtWidgets import QInputDialog
from wallpaper_video import VideoWallpaperEngine, VideoWallpaperError

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
except ImportError:
    QAudioOutput = QMediaPlayer = QVideoSink = None


class ScrollArea(QScrollArea):
    """Custom scroll area to capture mouse wheel movement"""
    wheel_up = Signal()
    wheel_down = Signal()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.wheel_up.emit()
        elif delta < 0:
            self.wheel_down.emit()
        event.accept()


class ShortcutsDialog(QDialog):
    """Modern popup dialog displaying all application keyboard shortcuts"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Application Shortcuts")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(420, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        card = QWidget()
        card.setObjectName("dialog_card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(25, 20, 25, 20)

        title = QLabel("⌨ Keyboard Shortcuts")
        title.setObjectName("dialog_title")
        card_layout.addWidget(title)

        shortcuts_html = """
        <style>
            td { padding: 5px 0px; color: #D0D0D0; font-size: 13px; }
            .key { background-color: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.2); border-radius: 4px; padding: 2px 7px; color: #FFF; font-weight: bold; }
        </style>
        <table width='100%'>
            <tr><td><span class='key'>Ctrl</span> + <span class='key'>Alt</span> + <span class='key'>Q</span></td><td align='right'>Show / Hide Bar</td></tr>
            <tr><td><span class='key'>→</span> / <span class='key'>Scroll Up</span></td><td align='right'>Navigate Right</td></tr>
            <tr><td><span class='key'>←</span> / <span class='key'>Scroll Down</span></td><td align='right'>Navigate Left</td></tr>
            <tr><td><span class='key'>Enter</span></td><td align='right'>Apply Selected Wallpaper</td></tr>
            <tr><td><span class='key'>X</span> / <span class='key'>Delete</span> / <span class='key'>Backspace</span></td><td align='right'>Delete Selected</td></tr>
            <tr><td><span class='key'>R</span></td><td align='right'>Replace Selected</td></tr>
            <tr><td><span class='key'>Ctrl</span> + <span class='key'>←</span> / <span class='key'>→</span></td><td align='right'>Reorder Wallpapers</td></tr>
            <tr><td><span class='key'>F1</span></td><td align='right'>Show Shortcuts Menu</td></tr>
            <tr><td><span class='key'>Ctrl</span> + <span class='key'>Shift</span> + <span class='key'>Q</span></td><td align='right'>Quit Application</td></tr>
        </table>
        """
        content = QLabel(shortcuts_html)
        content.setTextFormat(Qt.TextFormat.RichText)
        card_layout.addWidget(content)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("close_dialog_btn")
        close_btn.clicked.connect(self.accept)
        card_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(card)


class WallpaperBar(QMainWindow):
    hotkey_delete = Signal()
    hotkey_show = Signal()
    video_start_failed = Signal(str)
    video_start_succeeded = Signal()

    def __init__(self):
        super().__init__()
        self.video_engine = VideoWallpaperEngine()
        self._init_video_thumbnails()
        self._video_start_in_progress = False
        self._pending_video_wallpaper_path = None
        self._silent_video_start = False
        self.video_start_failed.connect(self._show_video_error)
        self.video_start_succeeded.connect(self._on_video_start_succeeded)

        # 1. Dynamic program paths
        app_data_dir = os.path.join(
            os.environ.get('APPDATA') or os.path.expanduser('~'),
            "WallpaperBar",
        )
        os.makedirs(app_data_dir, exist_ok=True)
        self.config_path = os.path.join(app_data_dir, "config.json")
        if getattr(sys, 'frozen', False):
            self.data_dir = app_data_dir
        else:
            self.data_dir = os.path.dirname(os.path.abspath(__file__))

        self.wallpapers_dir = os.path.join(self.data_dir, "wallpapers")
        os.makedirs(self.wallpapers_dir, exist_ok=True)
        self._wallpaper_fingerprint_cache = {}

        # 2. Selection state
        self.current_index = 0
        self.selected_button = None
        self._home_pos = QPoint(0, 0)
        self._quit_requested = False

        # 3. GUI setup and styling
        self.styling()
        self.setup_gui()

        # 4. Bind global keyboard shortcuts
        self.hotkey_delete.connect(self._do_delete)
        self.hotkey_show.connect(self.toggle_bar)
        keyboard.add_hotkey('ctrl+alt+q', lambda: self.hotkey_show.emit())
        keyboard.add_hotkey('delete', lambda: self.hotkey_delete.emit())
        keyboard.add_hotkey('backspace', lambda: self.hotkey_delete.emit())

        # 5. Bind active window shortcuts
        QShortcut(QKeySequence("X"), self).activated.connect(self._do_delete)
        QShortcut(QKeySequence("Right"), self).activated.connect(self.on_wheel_up)
        QShortcut(QKeySequence("Left"), self).activated.connect(self.on_wheel_down)
        QShortcut(QKeySequence("R"), self).activated.connect(self._do_replace)
        QShortcut(QKeySequence("Return"), self).activated.connect(self.apply_wallpaper)
        QShortcut(QKeySequence("Enter"), self).activated.connect(self.apply_wallpaper)
        QShortcut(QKeySequence("F1"), self).activated.connect(self.show_shortcuts_dialog)
        QShortcut(QKeySequence("Ctrl+Left"), self).activated.connect(lambda: self._move_selected(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self).activated.connect(lambda: self._move_selected(1))
        QShortcut(QKeySequence("Ctrl+Shift+Q"), self).activated.connect(self.quit_application)

        # 6. Load data and adjust size
        self.load_wallpapers()
        self.resize(900, 250)
        self.recenter()
        self._add_to_startup()

    def _save_last_wallpaper(self, file_path, wallpaper_type):
        config = {
            "path": os.path.abspath(file_path),
            "type": wallpaper_type,
        }
        temporary_path = self.config_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(temporary_path, "w", encoding="utf-8") as config_file:
                json.dump(config, config_file, ensure_ascii=False, indent=2)
            os.replace(temporary_path, self.config_path)
        except (OSError, TypeError, ValueError) as exc:
            print(f"[WallpaperBar] Could not save wallpaper state: {exc}",
                  file=sys.stderr, flush=True)
            try:
                os.remove(temporary_path)
            except OSError:
                pass

    def _read_last_wallpaper(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as config_file:
                config = json.load(config_file)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WallpaperBar] Could not read wallpaper state: {exc}",
                  file=sys.stderr, flush=True)
            return None

        file_path = config.get("path") if isinstance(config, dict) else None
        wallpaper_type = config.get("type") if isinstance(config, dict) else None
        if not isinstance(file_path, str) or not file_path:
            return None

        file_path = os.path.abspath(os.path.expanduser(os.path.expandvars(file_path)))
        if wallpaper_type not in ("image", "video"):
            extension = os.path.splitext(file_path)[1].lower()
            wallpaper_type = "video" if extension in (".mp4", ".webm") else "image"
        return {"path": file_path, "type": wallpaper_type}

    def restore_last_wallpaper(self):
        """Apply the saved wallpaper without showing the selection bar."""
        state = self._read_last_wallpaper()
        if state is None:
            return

        file_path = state["path"]
        if not os.path.isfile(file_path):
            print(f"[WallpaperBar] Saved wallpaper was not found: {file_path}",
                  file=sys.stderr, flush=True)
            return

        if state["type"] == "video":
            self._start_video_wallpaper(file_path, silent=True)
            return

        try:
            applied = ctypes.windll.user32.SystemParametersInfoW(20, 0, file_path, 3)
        except (AttributeError, OSError) as exc:
            print(f"[WallpaperBar] Could not restore image wallpaper: {exc}",
                  file=sys.stderr, flush=True)
            return
        if not applied:
            print(f"[WallpaperBar] Windows could not restore image wallpaper: {file_path}",
                  file=sys.stderr, flush=True)

    def _init_video_thumbnails(self):
        self._video_thumbnail_available = QMediaPlayer is not None
        self._video_thumbnail_player = None
        self._video_thumbnail_sink = None
        self._video_thumbnail_queue = []
        self._video_thumbnail_target = None
        self._video_thumbnail_cache_file = None
        self._video_thumbnail_play_requested = False
        self._video_thumbnail_player = None
        self._video_thumbnail_sink = None

    def _ensure_video_thumbnail_player(self):
        if not self._video_thumbnail_available:
            return False
        if self._video_thumbnail_player is not None:
            return True

        self._video_thumbnail_player = QMediaPlayer(self)
        self._video_thumbnail_audio = QAudioOutput(self._video_thumbnail_player)
        self._video_thumbnail_audio.setMuted(True)
        self._video_thumbnail_player.setAudioOutput(self._video_thumbnail_audio)
        self._video_thumbnail_sink = QVideoSink(self._video_thumbnail_player)
        self._video_thumbnail_player.setVideoSink(self._video_thumbnail_sink)
        self._video_thumbnail_sink.videoFrameChanged.connect(self._on_video_thumbnail_frame)
        self._video_thumbnail_player.mediaStatusChanged.connect(
            self._on_video_thumbnail_media_status
        )
        self._video_thumbnail_player.errorOccurred.connect(self._on_video_thumbnail_error)
        return True

    def _thumbnail_cache_path(self, video_path):
        try:
            stat = os.stat(video_path)
            cache_root = os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "WallpaperBar", "VideoThumbnails"
            )
            os.makedirs(cache_root, exist_ok=True)
            identity = (f"{os.path.normcase(os.path.abspath(video_path))}|"
                        f"{stat.st_size}|{stat.st_mtime_ns}")
            key = hashlib.sha256(identity.encode("utf-8", "surrogatepass")).hexdigest()
            return os.path.join(cache_root, key + ".jpg")
        except OSError:
            return None

    def _queue_video_thumbnail(self, button):
        if not self._video_thumbnail_available:
            button.setText("Preview unavailable")
            return
        cache_file = self._thumbnail_cache_path(button.video_path)
        button._video_thumbnail_cache_file = cache_file
        if cache_file and os.path.isfile(cache_file):
            image = QImage(cache_file)
            if not image.isNull():
                button.setIcon(self.create_rounded_icon(image))
                button.setIconSize(QSize(280, 160))
                button.setText("")
                return
        if button is self._video_thumbnail_target or button in self._video_thumbnail_queue:
            return
        self._video_thumbnail_queue.append(button)
        QTimer.singleShot(150, self._start_next_video_thumbnail)

    def _start_next_video_thumbnail(self):
        if (not self._video_thumbnail_available
                or not self.isVisible()
                or self._video_thumbnail_target is not None
                or self._video_start_in_progress):
            return
        if not self._video_thumbnail_queue or not self._ensure_video_thumbnail_player():
            return
        while self._video_thumbnail_queue:
            button = self._video_thumbnail_queue.pop(0)
            if not hasattr(button, "video_path") or not os.path.isfile(button.video_path):
                continue

            self._video_thumbnail_target = button
            self._video_thumbnail_cache_file = getattr(
                button, "_video_thumbnail_cache_file", None
            )
            self._video_thumbnail_play_requested = False
            self._video_thumbnail_player.stop()
            # Clear the previous URL first. Re-adding the same video can reuse
            # the identical URL, which Qt may otherwise leave in its old stopped state.
            self._video_thumbnail_player.setSource(QUrl())
            QTimer.singleShot(
                0,
                lambda target=button: self._load_video_thumbnail_source(target),
            )
            return

    def _load_video_thumbnail_source(self, button):
        if (button is self._video_thumbnail_target
                and os.path.isfile(button.video_path)):
            self._video_thumbnail_player.setSource(
                QUrl.fromLocalFile(button.video_path)
            )

    def _on_video_thumbnail_media_status(self, status):
        if (self._video_thumbnail_target is None
                or self._video_thumbnail_play_requested
                or status not in (QMediaPlayer.MediaStatus.LoadedMedia,
                                  QMediaPlayer.MediaStatus.BufferedMedia)):
            return
        duration = self._video_thumbnail_player.duration()
        if duration > 0:
            self._video_thumbnail_player.setPosition(min(900, duration // 8))
        self._video_thumbnail_play_requested = True
        self._video_thumbnail_player.play()

    def _on_video_thumbnail_error(self, _error, error_string):
        if self._video_thumbnail_target is None:
            return
        print(f"[WallpaperBar] Video thumbnail failed: {error_string}",
              file=sys.stderr, flush=True)
        self._video_thumbnail_target.setText("Preview unavailable")
        self._video_thumbnail_target.setIcon(QIcon())
        self._video_thumbnail_target = None
        self._video_thumbnail_cache_file = None
        self._video_thumbnail_play_requested = False
        self._video_thumbnail_player.stop()
        QTimer.singleShot(0, self._start_next_video_thumbnail)

    def _on_video_thumbnail_frame(self, frame):
        button = self._video_thumbnail_target
        if button is None:
            return
        image = frame.toImage()
        if image.isNull():
            return

        image = image.scaled(
            QSize(280, 160), Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation
        ).copy()
        button.setIcon(self.create_rounded_icon(image))
        button.setIconSize(QSize(280, 160))
        button.setText("")
        cache_file = self._video_thumbnail_cache_file
        self._video_thumbnail_target = None
        self._video_thumbnail_cache_file = None
        self._video_thumbnail_play_requested = False
        self._video_thumbnail_player.stop()
        if cache_file:
            image.save(cache_file, "JPEG", 82)
        QTimer.singleShot(0, self._start_next_video_thumbnail)

    def _stop_video_thumbnail_capture(self, requeue=True):
        button = self._video_thumbnail_target
        self._video_thumbnail_target = None
        self._video_thumbnail_cache_file = None
        self._video_thumbnail_play_requested = False
        if self._video_thumbnail_player is not None:
            self._video_thumbnail_player.stop()
        if button is not None and requeue:
            self._video_thumbnail_queue.insert(0, button)

    def _discard_video_thumbnail(self, button, remove_cache=True):
        if button is self._video_thumbnail_target:
            self._stop_video_thumbnail_capture(requeue=False)
        self._video_thumbnail_queue = [
            queued for queued in self._video_thumbnail_queue if queued is not button
        ]
        cache_file = getattr(button, "_video_thumbnail_cache_file", None)
        if remove_cache and cache_file and os.path.isfile(cache_file):
            try:
                os.remove(cache_file)
            except OSError:
                pass
        if hasattr(button, "_video_thumbnail_cache_file"):
            del button._video_thumbnail_cache_file
        QTimer.singleShot(0, self._start_next_video_thumbnail)

    # --- Helpers and Creation ---

    def _create_add_button(self) -> QPushButton:
        """Unified function to create the add button"""
        add_btn = QPushButton("+")
        add_btn.setObjectName("add_button")
        add_btn.setAttribute(Qt.WA_TranslucentBackground, True)
        add_btn.setFixedSize(280, 160)
        add_btn.clicked.connect(self.add_photo)
        return add_btn

    def _create_help_button(self) -> QPushButton:
        """Function to create the help button"""
        help_btn = QPushButton("?")
        help_btn.setObjectName("help_button")
        help_btn.setAttribute(Qt.WA_TranslucentBackground, True)
        help_btn.setToolTip("Keyboard Shortcuts (F1)")
        help_btn.setFixedSize(45, 160)
        help_btn.clicked.connect(self.show_shortcuts_dialog)
        return help_btn

    def show_shortcuts_dialog(self):
        """Show shortcuts popup dialog"""
        dialog = ShortcutsDialog(self)
        dialog.exec()

    def get_resource_path(self, relative_path):
        """Get absolute path to read-only resources (works for dev and for PyInstaller)"""
        if getattr(sys, 'frozen', False):
            base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_path, relative_path)

    def styling(self):
        css_path = self.get_resource_path("style.css")
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as file:
                self.setStyleSheet(file.read())

    def setup_gui(self):
        self.main_layout = QHBoxLayout()
        self.main_layout.setContentsMargins(15, 40, 15, 50)
        self.main_layout.setSpacing(15)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.central_widget = QWidget()
        self.central_widget.setObjectName("central_widget")
        self.central_widget.setAcceptDrops(True)
        self.central_widget.installEventFilter(self)
        self.central_widget.setFixedHeight(260)
        self.central_widget.setAttribute(Qt.WA_StyledBackground, True)
        self.central_widget.setLayout(self.main_layout)

        self.scroll_area = ScrollArea()
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.central_widget)

        self.wrapper = QWidget()
        self.wrapper.setObjectName("wrapper")
        self.wrapper.setAttribute(Qt.WA_TranslucentBackground)
        self.wrapper_layout = QVBoxLayout(self.wrapper)
        # Leave enough viewport space around the fixed-height content widget so
        # the scroll area cannot clip its rounded lower corners after resizing.
        self.wrapper_layout.setContentsMargins(30, 20, 30, 20)
        self.wrapper_layout.addWidget(self.scroll_area)
        self.setCentralWidget(self.wrapper)

        bar_shadow = QGraphicsDropShadowEffect(self.scroll_area)
        bar_shadow.setBlurRadius(50)
        bar_shadow.setColor(QColor(0, 0, 0, 220))
        bar_shadow.setOffset(0, 12)
        self.scroll_area.setGraphicsEffect(bar_shadow)
        self._bar_shadow = bar_shadow

        self.main_layout.addWidget(self._create_add_button())
        self.main_layout.addWidget(self._create_help_button())

        self.scroll_area.wheel_up.connect(self.on_wheel_up)
        self.scroll_area.wheel_down.connect(self.on_wheel_down)

    def load_wallpapers(self):
        valid_extensions = ('.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm')
        wallpapers = sorted(
            (os.path.join(self.wallpapers_dir, f) for f in os.listdir(self.wallpapers_dir)
             if f.lower().endswith(valid_extensions)),
            key=lambda path: os.path.basename(path).casefold(),
        )

        for wallpaper_path in wallpapers:
            current_button = self.main_layout.itemAt(self.main_layout.count() - 2).widget()
            self._configure_wallpaper_button(current_button, wallpaper_path)

            # Always add a new add button and keep the help button at the end
            help_btn = self.main_layout.takeAt(self.main_layout.count() - 1).widget()
            self.main_layout.addWidget(self._create_add_button())
            self.main_layout.addWidget(help_btn)

    def _configure_wallpaper_button(self, button, wallpaper_path):
        self._discard_video_thumbnail(button)
        wallpaper_path = os.path.abspath(wallpaper_path)
        is_video = os.path.splitext(wallpaper_path)[1].lower() in ('.mp4', '.webm')
        button.setCheckable(True)
        button.setAttribute(Qt.WA_TranslucentBackground, True)
        button.setToolTip(os.path.basename(wallpaper_path))

        if is_video:
            button.setObjectName("video_wallpaper_btn")
            button.setIcon(QIcon())
            button.setIconSize(QSize(280, 160))
            button.setStyleSheet(
                "font-family: 'Segoe UI'; font-size: 12px; font-weight: 500; "
                "color: rgba(255, 255, 255, 0.72);"
            )
            button.setText("Loading…")
            button.video_path = wallpaper_path
            if hasattr(button, "image_path"):
                del button.image_path
        else:
            button.setObjectName("wallpaper_btn")
            button.setStyleSheet("")
            button.setText("")
            button.setIcon(self.create_rounded_icon(wallpaper_path))
            button.setIconSize(QSize(280, 160))
            button.image_path = wallpaper_path
            if hasattr(button, "video_path"):
                del button.video_path

        button.installEventFilter(self)
        try:
            button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        button.clicked.connect(
            lambda checked=False, b=button: self.on_button_clicked(b)
        )
        if is_video:
            self._queue_video_thumbnail(button)

    def create_rounded_icon(self, image_source, width=280, height=160, radius=12):
        if isinstance(image_source, QImage):
            pixmap = QPixmap.fromImage(image_source)
        elif isinstance(image_source, QPixmap):
            pixmap = image_source
        else:
            pixmap = QPixmap(image_source)
        scaled_pixmap = pixmap.scaled(
            width, height, 
            Qt.KeepAspectRatioByExpanding, 
            Qt.SmoothTransformation
        )

        rounded_pixmap = QPixmap(width, height)
        rounded_pixmap.fill(Qt.transparent)

        painter = QPainter(rounded_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, width, height, radius, radius)
        painter.setClipPath(path)

        x = (width - scaled_pixmap.width()) // 2
        y = (height - scaled_pixmap.height()) // 2
        painter.drawPixmap(x, y, scaled_pixmap)
        painter.end()

        return QIcon(rounded_pixmap)

    def _save_supported_image(self, file_path, dest_folder):
        file_name = os.path.basename(file_path)
        name, ext = os.path.splitext(file_name)

        if ext.lower() == '.webp':
            dest_path = os.path.join(dest_folder, f"{name}.png")
            img = QImage(file_path)
            img.save(dest_path, "PNG")
        else:
            dest_path = os.path.join(dest_folder, file_name)
            import shutil
            shutil.copy(file_path, dest_path)

        return dest_path

    def _save_supported_video(self, file_path, dest_folder):
        import shutil

        source = os.path.abspath(file_path)
        dest_path = os.path.join(dest_folder, os.path.basename(source))
        if os.path.exists(dest_path):
            try:
                if os.path.samefile(source, dest_path):
                    return dest_path
            except OSError:
                pass
            name, ext = os.path.splitext(os.path.basename(source))
            suffix = 2
            while os.path.exists(dest_path):
                dest_path = os.path.join(dest_folder, f"{name} ({suffix}){ext}")
                suffix += 1
        shutil.copy2(source, dest_path)
        return dest_path

    # --- Display and Resizing Logic ---

    def recenter(self):
        spacing = self.main_layout.spacing()
        margins = self.main_layout.contentsMargins()
        padding = margins.left() + margins.right()

        # Calculate exact content width based on button types
        content_width = 0
        for i in range(self.main_layout.count()):
            w = self.main_layout.itemAt(i).widget()
            if w and w.objectName() == "help_button":
                content_width += 45
            else:
                content_width += 280

        content_width += (self.main_layout.count() - 1) * spacing + padding
        desired_window_width = content_width + 60
        window_height = 320 

        screen_geometry = QApplication.primaryScreen().availableGeometry()
        max_safe_width = screen_geometry.width() - 200 # Safe margin to preserve the floating bar layout
        actual_window_width = min(desired_window_width, max_safe_width)

        self.resize(actual_window_width, window_height)
        self.apply_base_shadows()

        x = (screen_geometry.width() - actual_window_width) // 2
        y = screen_geometry.height() - window_height 

        self.move(x, y)
        self._home_pos = QPoint(x, y)

    def apply_base_shadows(self):
        for i in range(self.main_layout.count()):
            btn = self.main_layout.itemAt(i).widget()
            if btn and btn.graphicsEffect() is None:
                shadow = QGraphicsDropShadowEffect(btn)
                shadow.setBlurRadius(35)
                shadow.setOffset(0, 12)
                shadow.setColor(QColor(0, 0, 0, 0))
                btn.setGraphicsEffect(shadow)

    # --- Wallpapers and Events Management ---

    def _wallpaper_fingerprint(self, file_path, is_video):
        """Return a cached content fingerprint for duplicate detection."""
        try:
            stat = os.stat(file_path)
        except OSError:
            return None

        cache_key = (
            os.path.normcase(os.path.abspath(file_path)),
            stat.st_size,
            stat.st_mtime_ns,
            is_video,
        )
        cached = self._wallpaper_fingerprint_cache.get(cache_key)
        if cached is not None:
            return cached

        digest = hashlib.sha256()
        if not is_video:
            image = QImage(file_path)
            if not image.isNull():
                image = image.convertToFormat(QImage.Format.Format_RGBA8888)
                digest.update(f"{image.width()}x{image.height()}:".encode("ascii"))
                pixels = image.constBits()
                pixel_bytes = image.sizeInBytes()
                if hasattr(pixels, "setsize"):
                    pixels.setsize(pixel_bytes)
                digest.update(bytes(pixels))
                fingerprint = ("image", digest.digest())
                self._wallpaper_fingerprint_cache[cache_key] = fingerprint
                return fingerprint

        try:
            with open(file_path, "rb") as media_file:
                for chunk in iter(lambda: media_file.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None

        fingerprint = ("video" if is_video else "image", digest.digest())
        self._wallpaper_fingerprint_cache[cache_key] = fingerprint
        return fingerprint

    def _same_wallpaper_content(self, first_path, second_path, is_video):
        try:
            if os.path.samefile(first_path, second_path):
                return True
            if is_video and os.path.getsize(first_path) != os.path.getsize(second_path):
                return False
        except OSError:
            return False

        first_fingerprint = self._wallpaper_fingerprint(first_path, is_video)
        return (first_fingerprint is not None
                and first_fingerprint == self._wallpaper_fingerprint(second_path, is_video))

    def _already_added(self, file_path, is_video):
        """Check visible wallpaper cards, excluding unlisted files on disk."""
        path_attribute = "video_path" if is_video else "image_path"
        for index in range(self.main_layout.count()):
            button = self.main_layout.itemAt(index).widget()
            existing_path = getattr(button, path_attribute, None)
            if existing_path and self._same_wallpaper_content(
                    file_path, existing_path, is_video):
                return True
        return False

    def _find_stored_duplicate(self, file_path, is_video):
        """Find a matching file left on disk but no longer shown in the bar."""
        video_extensions = (".mp4", ".webm")
        image_extensions = (".png", ".jpg", ".jpeg", ".webp")
        supported_extensions = video_extensions if is_video else image_extensions
        try:
            entries = os.scandir(self.wallpapers_dir)
        except OSError:
            return None

        with entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if os.path.splitext(entry.name)[1].lower() not in supported_extensions:
                    continue
                if self._same_wallpaper_content(file_path, entry.path, is_video):
                    return entry.path
        return None

    def add_photo(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choose a wallpaper", "",
            "Wallpapers (*.png *.jpg *.jpeg *.webp *.mp4 *.webm);;"
            "Images (*.png *.jpg *.jpeg *.webp);;"
            "Videos (*.mp4 *.webm)"
        )
        if file_path:
            is_video = os.path.splitext(file_path)[1].lower() in (".mp4", ".webm")
            if self._already_added(file_path, is_video):
                media_type = "video" if is_video else "image"
                QMessageBox.information(
                    self,
                    "Already added",
                    f"This {media_type} is already added to your wallpapers.",
                )
                return

            if is_video:
                try:
                    dest_path = self._find_stored_duplicate(file_path, is_video)
                    if dest_path is None:
                        dest_path = self._save_supported_video(file_path, self.wallpapers_dir)
                    current_button = self.sender()
                    self._configure_wallpaper_button(current_button, dest_path)

                    help_btn = self.main_layout.takeAt(self.main_layout.count() - 1).widget()
                    self.main_layout.addWidget(self._create_add_button())
                    self.main_layout.addWidget(help_btn)
                    self.recenter()
                except OSError as exc:
                    self.video_start_failed.emit(str(exc))
                return
        if file_path:
            dest_path = self._find_stored_duplicate(file_path, is_video=False)
            if dest_path is None:
                dest_path = self._save_supported_image(file_path, self.wallpapers_dir)
            current_button = self.sender()
            self._configure_wallpaper_button(current_button, dest_path)

            # Move the add and help buttons back to the end
            help_btn = self.main_layout.takeAt(self.main_layout.count() - 1).widget()
            self.main_layout.addWidget(self._create_add_button())
            self.main_layout.addWidget(help_btn)

            self.recenter()

    def _start_video_wallpaper(self, video_path, silent=False):
        if self._video_start_in_progress:
            return
        self._stop_video_thumbnail_capture()
        self._pending_video_wallpaper_path = os.path.abspath(video_path)
        self._silent_video_start = silent
        try:
            monitors = self.video_engine.list_monitors()
            target = "all"
            if len(monitors) > 1 and not silent:
                choices = ["All monitors"] + monitors
                choice, accepted = QInputDialog.getItem(
                    self, "Live video wallpaper", "Display:", choices, 0, False
                )
                if not accepted:
                    self._pending_video_wallpaper_path = None
                    self._silent_video_start = False
                    return
                if choice != "All monitors":
                    target = choice

            self.video_engine.stop()
            self._video_start_in_progress = True
            self.central_widget.setEnabled(False)
            future = self.video_engine.start_async(video_path, monitor=target)
            future.add_done_callback(self._video_start_completed)
        except (OSError, VideoWallpaperError) as exc:
            self.video_start_failed.emit(str(exc))

    def _video_start_completed(self, future):
        try:
            future.result()
        except Exception as exc:
            print(f"[WallpaperBar] Live wallpaper failed: {exc}", file=sys.stderr, flush=True)
            self.video_start_failed.emit(str(exc))
        else:
            print("[WallpaperBar] Video engine reported playback ready.", flush=True)
            self.video_start_succeeded.emit()

    def _on_video_start_succeeded(self):
        self._video_start_in_progress = False
        self.central_widget.setEnabled(True)
        if self._pending_video_wallpaper_path:
            self._save_last_wallpaper(self._pending_video_wallpaper_path, "video")
        self._pending_video_wallpaper_path = None
        self._silent_video_start = False
        self.hide_bar_animated()

    def _show_video_error(self, message):
        silent = self._silent_video_start
        self._video_start_in_progress = False
        self.central_widget.setEnabled(True)
        self._pending_video_wallpaper_path = None
        self._silent_video_start = False
        if silent:
            print(f"[WallpaperBar] Could not restore live wallpaper: {message}",
                  file=sys.stderr, flush=True)
            return
        self.show_bar_animated()
        QtWidgets.QMessageBox.warning(
            self, "Live wallpaper could not start", message
        )

    def on_wheel_up(self):
        if self.selected_button is None:
            self.select_button(0, 1)
        else:
            self.select_button(self.current_index + 1, 1)

    def on_wheel_down(self):
        if self.selected_button is None:
            self.select_button(-1, -1)
        else:
            self.select_button(self.current_index - 1, -1)

    def select_button(self, index, step=1):
        count = self.main_layout.count()
        if count == 0:
            return
            
        index = index % count
        button = self.main_layout.itemAt(index).widget()

        attempts = 0
        while (button.text() in ["+", "?"]) and attempts < count:
            index = (index + step) % count
            button = self.main_layout.itemAt(index).widget()
            attempts += 1

        if button.text() in ["+", "?"]:
            return

        if self.selected_button is not None and self.selected_button != button:
            self.selected_button.setChecked(False)
            old_shadow = self.selected_button.graphicsEffect()
            if old_shadow is not None:
                if hasattr(self.selected_button, '_anim_color'):
                    self.selected_button._anim_color.stop()
                
                anim_out = QPropertyAnimation(old_shadow, b"color", self.selected_button)
                anim_out.setDuration(150)
                anim_out.setStartValue(old_shadow.color())
                anim_out.setEndValue(QColor(0, 0, 0, 0))
                anim_out.start()
                self.selected_button._anim_color = anim_out

        button.setChecked(True)
        shadow = button.graphicsEffect()
        
        if shadow is None:
            shadow = QGraphicsDropShadowEffect(button)
            shadow.setBlurRadius(35)
            shadow.setOffset(0, 12)
            shadow.setColor(QColor(0, 0, 0, 0))
            button.setGraphicsEffect(shadow)

        if hasattr(button, '_anim_color'):
            button._anim_color.stop()

        anim_in = QPropertyAnimation(shadow, b"color", button)
        anim_in.setDuration(280)
        anim_in.setStartValue(shadow.color())
        anim_in.setEndValue(QColor(0, 0, 0, 200))
        anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim_in.start()

        button._anim_color = anim_in
        self.selected_button = button
        self.current_index = index
        # Scroll to make the selected wallpaper visible
        self.scroll_area.ensureWidgetVisible(button, 50, 0)
        
        # Smart fix: if this is the last wallpaper, scroll to ensure the add and help buttons are fully visible
        if index == self.main_layout.count() - 3: 
            help_btn = self.main_layout.itemAt(self.main_layout.count() - 1).widget()
            if help_btn:
                self.scroll_area.ensureWidgetVisible(help_btn, 50, 0)

    def on_button_clicked(self, button):
        for i in range(self.main_layout.count()):
            if self.main_layout.itemAt(i).widget() is button:
                self.select_button(i)
                self.apply_wallpaper()
                return

    def apply_wallpaper(self):
        if self.selected_button is None or self._video_start_in_progress:
            return

        video_path = getattr(self.selected_button, "video_path", None)
        if video_path:
            self._start_video_wallpaper(video_path)
            return

        try:
            self.video_engine.stop()
        except VideoWallpaperError as exc:
            QMessageBox.warning(
                self, "Could not stop the live wallpaper", str(exc)
            )
            return

        image_path = self.selected_button.image_path
        if not ctypes.windll.user32.SystemParametersInfoW(20, 0, image_path, 3):
            QMessageBox.warning(self, "Could not apply wallpaper", image_path)
            return
        self._save_last_wallpaper(image_path, "image")
        self.hide_bar_animated()

    # --- Animation and Resetting ---

    def hide_bar_animated(self):
        if not self.isVisible():
            return
        self._stop_video_thumbnail_capture()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(300)
        anim.setStartValue(self._home_pos)
        anim.setEndValue(self._home_pos + QPoint(0, self.height() + 50))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.hide)
        self._hide_anim = anim
        anim.start()

    def show_bar_animated(self):
        if self.isVisible():
            return
        self.move(self._home_pos + QPoint(0, self.height() + 50))
        self.show()
        self._start_next_video_thumbnail()
        self.raise_()
        self.activateWindow()
        if self.selected_button is not None:
            self.scroll_area.ensureWidgetVisible(self.selected_button)

        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(300)
        anim.setStartValue(self._home_pos + QPoint(0, self.height() + 50))
        anim.setEndValue(self._home_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._show_anim = anim
        anim.start()

    def toggle_bar(self):
        if self.isVisible():
            self.hide_bar_animated()
        else:
            self.show_bar_animated()

    def setup_tray_icon(self):
        """Add tray controls so the bar can be restored while hidden."""
        icon = self.windowIcon()
        if icon.isNull():
            icon = QIcon(self.get_resource_path("app_icon.ico"))

        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("WallpaperBar")

        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Show/Hide Bar", self.toggle_bar)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit", self.quit_application)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.show()

    def _on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_bar_animated()

    def closeEvent(self, event):
        if self._quit_requested:
            event.accept()
            return
        # Keep the process and tray icon alive when the bar is closed.
        event.ignore()
        self.hide_bar_animated()

    def quit_application(self):
        """Exit fully when the user chooses Quit from an explicit control."""
        self._quit_requested = True
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        QApplication.instance().quit()

    # --- Advanced Operations (Delete, Replace, Move) ---

    def delete_selected(self):
        if self.selected_button is None or self._video_start_in_progress:
            return

        button = self.selected_button
        # Keep the thumbnail cache so re-adding a file that could not be
        # removed from disk can restore its preview immediately.
        self._discard_video_thumbnail(button, remove_cache=False)
        index = self.current_index
        wallpaper_path = getattr(button, "video_path", None) or getattr(button, "image_path", None)
        if not wallpaper_path:
            return

        if getattr(button, "video_path", None):
            try:
                self.video_engine.stop()
            except VideoWallpaperError as exc:
                self.video_start_failed.emit(str(exc))
                return

        try:
            os.remove(wallpaper_path)
        except OSError:
            pass

        self.main_layout.removeWidget(button)
        button.hide()
        button.deleteLater()

        self.selected_button = None

        self.recenter()

        count = self.main_layout.count() - 2
        if count == 0:
            self.current_index = 0
            return

        self.select_button(index)
        if self.selected_button and self.selected_button.text() == "+":
            self.select_button(index - 1)

    def _do_delete(self):
        if self.isActiveWindow() and self.selected_button is not None:
            self.delete_selected()

    def _do_replace(self):
        if (self._video_start_in_progress or self.selected_button is None
                or self.selected_button.text() in ["+", "?"]):
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choose a new wallpaper to replace", "",
            "Wallpapers (*.png *.jpg *.jpeg *.webp *.mp4 *.webm);;"
            "Images (*.png *.jpg *.jpeg *.webp);;"
            "Videos (*.mp4 *.webm)"
        )

        if file_path:
            button = self.selected_button
            old_wallpaper_path = (
                getattr(button, "video_path", None)
                or getattr(button, "image_path", None)
            )
            is_video = os.path.splitext(file_path)[1].lower() in (".mp4", ".webm")
            try:
                if is_video:
                    dest_path = self._save_supported_video(file_path, self.wallpapers_dir)
                else:
                    dest_path = self._save_supported_image(file_path, self.wallpapers_dir)

                if getattr(button, "video_path", None):
                    self.video_engine.stop()

                self._configure_wallpaper_button(button, dest_path)
            except (OSError, VideoWallpaperError) as exc:
                self.video_start_failed.emit(str(exc))
            else:
                try:
                    if (old_wallpaper_path and old_wallpaper_path != dest_path
                            and os.path.exists(old_wallpaper_path)):
                        os.remove(old_wallpaper_path)
                except OSError:
                    pass

    def _move_selected(self, direction):
        if self.selected_button is None or self.selected_button.text() in ["+", "?"]:
            return
            
        index = self.current_index
        new_index = index + direction
        
        if new_index < 0 or new_index >= self.main_layout.count() - 1:
            return
            
        target_widget = self.main_layout.itemAt(new_index).widget()
        if target_widget and target_widget.text() in ["+", "?"]:
            return
            
        self.main_layout.removeWidget(self.selected_button)
        self.main_layout.insertWidget(new_index, self.selected_button)
        
        self.current_index = new_index
        self.scroll_area.ensureWidgetVisible(self.selected_button, 50, 0)

    # --- Event Filter and Drag & Drop ---

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Enter:
            if QGuiApplication.mouseButtons() & Qt.LeftButton:
                return super().eventFilter(watched, event)
                
            if isinstance(watched, QPushButton) and watched.text() not in ["+", "?"]:
                for i in range(self.main_layout.count()):
                    if self.main_layout.itemAt(i).widget() is watched:
                        if self.current_index != i or self.selected_button is None:
                            self.select_button(i)
                        break

        elif event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.LeftButton and isinstance(watched, QPushButton) and watched.text() not in ["+", "?"]:
                self._drag_start_pos = event.pos()
                self._dragged_button = watched

        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.LeftButton:
                self._drag_start_pos = None

        elif event.type() == QEvent.Type.MouseMove:
            if hasattr(self, '_drag_start_pos') and self._drag_start_pos is not None:
                if isinstance(watched, QPushButton) and watched is getattr(self, '_dragged_button', None):
                    if (event.pos() - self._drag_start_pos).manhattanLength() > QApplication.startDragDistance():
                        drag = QDrag(watched)
                        mime_data = QMimeData()
                        mime_data.setText(str(id(watched))) 
                        drag.setMimeData(mime_data)
                        
                        pixmap = watched.icon().pixmap(watched.iconSize())
                        drag.setPixmap(pixmap)
                        drag.setHotSpot(event.pos())
                        
                        drag.exec(Qt.DropAction.MoveAction)
                        self._drag_start_pos = None
                        return True

        elif event.type() == QEvent.Type.DragEnter:
            if watched is self.central_widget:
                event.acceptProposedAction()
                return True

        elif event.type() == QEvent.Type.Drop:
            if watched is self.central_widget:
                dragged_id = event.mimeData().text()
                dragged_widget = None
                
                for i in range(self.main_layout.count()):
                    w = self.main_layout.itemAt(i).widget()
                    if str(id(w)) == dragged_id:
                        dragged_widget = w
                        break
                
                if dragged_widget:
                    pos_x = event.position().toPoint().x()
                    drop_index = -1
                    
                    for i in range(self.main_layout.count()):
                        w = self.main_layout.itemAt(i).widget()
                        if w.text() in ["+", "?"]:
                            continue
                        if pos_x < w.x() + (w.width() / 2):
                            drop_index = i
                            break
                    
                    if drop_index == -1:
                        drop_index = self.main_layout.count() - 3
                        
                    self.main_layout.removeWidget(dragged_widget)
                    self.main_layout.insertWidget(drop_index, dragged_widget)
                    
                    for i in range(self.main_layout.count()):
                        if self.main_layout.itemAt(i).widget() is self.selected_button:
                            self.current_index = i
                            break
                            
                event.acceptProposedAction()
                return True

        return super().eventFilter(watched, event)

    # --- OS Integration ---

    def _is_msix_packaged(self):
        """
        True when this process is running from an installed MSIX package.
        This matters because MSIX virtualizes HKCU: a plain registry "Run"
        key write (the method below, used for normal/dev installs) gets
        redirected into the package's private hive and never reaches the
        real Run key Explorer reads at logon, so "start with Windows"
        would silently stop working. Packaged builds must instead rely on
        the <desktop:StartupTask> extension declared in AppxManifest.xml.
        """
        try:
            length = ctypes.c_uint(0)
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
            # Not packaged -> APPMODEL_ERROR_NO_PACKAGE, length stays 0.
            # Packaged -> ERROR_INSUFFICIENT_BUFFER, length is set > 0.
            return length.value > 0
        except (AttributeError, OSError):
            return False

    def _add_to_startup(self):
        """Register the app to run at user logon, picking whichever
        mechanism actually works for how this build was launched."""
        if self._is_msix_packaged():
            self._enable_msix_startup_task()
        else:
            self._add_to_startup_registry()

    def _add_to_startup_registry(self):
        """Classic HKCU\\...\\Run entry. Only reliable for dev runs and
        plain (non-MSIX) .exe distributions, e.g. a PyInstaller build
        handed out directly or through a regular installer."""
        import winreg
        if getattr(sys, 'frozen', False):
            app_path = sys.executable
        else:
            app_path = os.path.abspath(sys.argv[0])

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "WallpaperBar"

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{app_path}"')
            winreg.CloseKey(key)
        except Exception:
            pass

    def _enable_msix_startup_task(self):
        """
        Turn on the StartupTask declared in AppxManifest.xml.
        TaskId here MUST match <desktop:StartupTask TaskId="..."> in the
        manifest (see AppxManifest.xml - "WallpaperBarStartupTask").
        Needs `pip install winsdk` bundled into the build. If that package
        isn't available this just does nothing - it never crashes the app,
        the user can still flip it on manually from Settings > Apps > Startup.
        """
        try:
            from winsdk.windows.applicationmodel import StartupTask, StartupTaskState
        except ImportError:
            return

        async def _request():
            task = await StartupTask.get_async("WallpaperBarStartupTask")
            if task.state == StartupTaskState.DISABLED_BY_USER:
                return  # user explicitly turned it off - respect that choice
            if task.state == StartupTaskState.DISABLED:
                await task.request_enable_async()

        try:
            import asyncio
            asyncio.run(_request())
        except Exception:
            pass


# --- Application Entry Point ---
if __name__ == "__main__":
    try:
        import ctypes
        myappid = 'wallpaperbar.desktop.app.1.0'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    server_name = "WallpaperBar_SingleInstance_Server"

    # 1. Check if an instance is already running in the background
    socket = QLocalSocket()
    socket.connectToServer(server_name)

    # If connected, another instance is already running!
    if socket.waitForConnected(500):
        # Send "SHOW" command to the existing instance and exit this one
        socket.write(b"SHOW")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        sys.exit(0)

    # 2. If no prior instance exists, create a local server to listen for future commands
    QLocalServer.removeServer(server_name)  # Clean up leftovers in case of a previous crash
    server = QLocalServer()
    server.listen(server_name)

    window = WallpaperBar()
    app.aboutToQuit.connect(window.video_engine.stop)

    # Callback function when the user tries to open the app again from the icon
    def handle_client_connection():
        client_socket = server.nextPendingConnection()
        if client_socket:
            client_socket.readyRead.connect(lambda: process_message(client_socket))

    def process_message(client_socket):
        msg = client_socket.readAll().data().decode('utf-8')
        if "SHOW" in msg:
            # Immediately show the bar when triggered
            window.show_bar_animated()

    server.newConnection.connect(handle_client_connection)

    # Configure window settings and app icon
    icon_path = window.get_resource_path("app_icon.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
        window.setWindowIcon(app_icon)

    window.setup_tray_icon()

    window.setAttribute(Qt.WA_TranslucentBackground)
    window.setWindowFlag(Qt.FramelessWindowHint)

    QTimer.singleShot(0, window.restore_last_wallpaper)
    sys.exit(app.exec())

"""
Debug Logs Viewer Component

Shows application debug logs in a popup window.
"""

import sys
import threading
import time
from collections import deque

import flet as ft


class SimpleLogCapture:
    """Simple, non-intrusive log capture system."""

    def __init__(self):
        self.logs = deque(maxlen=500)  # Keep last 500 log entries
        self.lock = threading.Lock()
        self.capture_enabled = True  # Start enabled by default

    def enable_capture(self):
        """Enable log capture only when needed."""
        self.capture_enabled = True

    def disable_capture(self):
        """Disable log capture."""
        self.capture_enabled = False

    def add_log(self, message):
        """Add a log entry if capture is enabled."""
        if self.capture_enabled:
            timestamp = time.strftime("%H:%M:%S")
            with self.lock:
                self.logs.append(f"[{timestamp}] {message}")

    def get_logs(self) -> str:
        """Get captured logs as string."""
        with self.lock:
            if not self.logs:
                return "No logs captured yet."
            return "\n".join(self.logs)

    def clear_logs(self):
        """Clear all logs."""
        with self.lock:
            self.logs.clear()


# Global log capture instance
_log_capture = SimpleLogCapture()


class DebugLogsViewer:
    """A component to display debug logs in a popup window."""

    def __init__(self, page: ft.Page):
        self.page = page
        self.logs_text = ft.TextField(
            multiline=True,
            read_only=True,
            value="Loading logs...",
            height=400,
            width=600,
            bgcolor="#1a1a2e",
            text_style=ft.TextStyle(color="#ffffff", size=12, font_family="Consolas"),
        )
        self.clear_button = ft.OutlinedButton(
            "Clear Logs",
            on_click=self._clear_logs,
            style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
        )
        self.export_game_logs_button = ft.OutlinedButton(
            "Export Game Logs",
            on_click=self._export_game_logs,
            style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
        )
        self.close_button = ft.OutlinedButton(
            "Close",
            on_click=self._close_dialog,
            style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
        )

        self.dialog = None

    def _get_recent_logs(self) -> str:
        """Get recent log entries from capture."""
        logs = _log_capture.get_logs()
        status = "CAPTURE ACTIVE" if _log_capture.capture_enabled else "CAPTURE INACTIVE"

        # Separate telemetry logs for better visibility
        all_lines = logs.split('\n')
        telemetry_lines = [line for line in all_lines if '[TELEMETRY]' in line or '[ANALYZER]' in line]
        other_lines = [line for line in all_lines if '[TELEMETRY]' not in line and '[ANALYZER]' not in line]

        result = f"{status}\n\n"
        
        if telemetry_lines:
            result += "=== TELEMETRY EVENTS ===\n"
            result += "\n".join(telemetry_lines[-20:]) + "\n\n"  # Show last 20 telemetry events
            result += "=== OTHER LOGS ===\n"
        
        result += "\n".join(other_lines[-30:])  # Show last 30 other logs
        
        return result

    def _clear_logs(self, e=None):
        """Clear logs."""
        _log_capture.clear_logs()
        self.logs_text.value = "Logs cleared."
        self.logs_text.update()

    def _export_game_logs(self, e=None):
        """Export game logs to file."""
        from ...utils.structured_logger import log_info, log_warning, log_error, log_exception, Component
        
        log_info(Component.DEBUG_LOGS, "Export game logs requested")
        
        try:
            # Get the app instance from the page
            app_instance = getattr(self.page, '_app_instance', None)
            
            if not app_instance:
                log_warning(Component.DEBUG_LOGS, "No app instance found")
                self._show_snackbar("App instance not available", "#ff6b6b")
                return
            
            if not hasattr(app_instance, '_log_parser'):
                log_warning(Component.DEBUG_LOGS, "App instance has no log parser")
                self._show_snackbar("Log parser not available", "#ff6b6b")
                return
            
            log_parser = app_instance._log_parser
            
            if not log_parser:
                log_warning(Component.DEBUG_LOGS, "Log parser is None")
                self._show_snackbar("Log parser not initialized", "#ff6b6b")
                return
            
            # Check if log buffer has content
            log_lines = log_parser.get_log_buffer()
            
            if not log_lines:
                log_warning(Component.DEBUG_LOGS, "Log buffer is empty")
                self._show_snackbar("No logs to export", "#ff6b6b")
                return
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
            filename = f"game_logs_{timestamp}.txt"
            filepath = os.path.join(downloads_path, filename)
            
            log_info(Component.DEBUG_LOGS, "Exporting game logs", filepath=filepath, lines=len(log_lines))
            
            # Export logs
            success = log_parser.export_logs_to_file(filepath)
            
            if success:
                log_info(Component.DEBUG_LOGS, "Game logs exported successfully", filename=filename)
                self._show_snackbar(f"Game logs exported to {filename}", "#51cf66")
            else:
                log_error(Component.DEBUG_LOGS, "Failed to export game logs")
                self._show_snackbar("Failed to export game logs", "#ff6b6b")
                
        except Exception as ex:
            log_exception(Component.DEBUG_LOGS, "Error exporting game logs", ex)
            self._show_snackbar(f"Error: {str(ex)}", "#ff6b6b")
    
    def _show_snackbar(self, message: str, bgcolor: str):
        """Helper to show a snackbar message."""
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=bgcolor
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _close_dialog(self, e=None):
        """Close the debug logs dialog."""
        if self.dialog:
            self.dialog.open = False
            self.page.update()

    def show_dialog(self):
        """Show the debug logs dialog."""
        self.logs_text.value = self._get_recent_logs()

        self.dialog = ft.AlertDialog(
            title=ft.Text("Debug Logs", size=20, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column(
                    [
                        self.logs_text,
                        ft.Row([self.clear_button, self.export_game_logs_button, self.close_button], spacing=10),
                    ],
                    spacing=10,
                ),
                width=650,
                padding=20,
            ),
            shape=ft.RoundedRectangleBorder(radius=12),
        )

        self.page.show_dialog(self.dialog)


def start_log_capture():
    """Start the global log capture system (smart, non-intrusive)."""
    try:
        is_frozen = getattr(sys, "frozen", False)

        original_stdout = getattr(sys, "stdout", None) or getattr(sys, "__stdout__", None)
        original_stderr = getattr(sys, "stderr", None) or getattr(sys, "__stderr__", None)

        class UniversalWriter:
            def __init__(self, original, capture):
                self.original = original
                self.capture = capture

            def write(self, text):
                if text is None:
                    return 0
                if not isinstance(text, str):
                    text = str(text)

                if self.original is not None:
                    try:
                        self.original.write(text)
                    except Exception:
                        pass

                if text.strip():
                    for line in text.rstrip().splitlines():
                        if line.strip():
                            self.capture.add_log(line.rstrip())

                return len(text)

            def flush(self):
                if self.original is not None:
                    try:
                        self.original.flush()
                    except Exception:
                        pass

            def isatty(self):
                if self.original is None:
                    return False
                try:
                    return self.original.isatty()
                except Exception:
                    return False

        sys.stdout = UniversalWriter(original_stdout, _log_capture)
        sys.stderr = UniversalWriter(original_stderr, _log_capture)

        _log_capture.add_log("[LOGS] Universal debug capture enabled")
        _log_capture.add_log(f"[LOGS] Running as built executable: {is_frozen}")

    except Exception as e:
        _log_capture.add_log(f"[LOGS] Failed to start log capture: {e}")
        try:
            import traceback

            _log_capture.add_log(traceback.format_exc())
        except Exception:
            pass



def show_debug_logs(page: ft.Page):
    """Show debug logs dialog."""
    viewer = DebugLogsViewer(page)
    viewer.show_dialog()


# Global function to add logs from anywhere
def add_debug_log(message: str):
    """Add a debug log entry."""
    _log_capture.add_log(message)

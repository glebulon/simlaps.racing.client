"""
Structured Logging System for SimLaps Client

Provides consistent, structured logging across all modules with proper
log levels, component tagging, and debug log integration.
"""

import sys
import time
from enum import Enum
from typing import Optional, Any, Dict
from pathlib import Path


class LogLevel(Enum):
    """Log levels for structured logging."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Component(Enum):
    """Component identifiers for structured logging."""
    APP = "APP"
    TELEMETRY = "TELEMETRY"
    ANALYZER = "ANALYZER"
    CAPTURE = "CAPTURE"
    UI = "UI"
    HOME = "HOME"
    DEBUG_LOGS = "DEBUG_LOGS"
    HISTORY = "HISTORY"
    PB_CACHE = "PB_CACHE"
    PB_VIEWER = "PB_VIEWER"
    LAP_CARD = "LAP_CARD"
    CONFIG = "CONFIG"
    SECURITY = "SECURITY"
    DISCORD = "DISCORD"
    API = "API"
    LOG_PARSER = "LOG_PARSER"


class StructuredLogger:
    """Centralized structured logger for the application."""
    
    def __init__(self):
        self._debug_enabled = True  # Always enable debug logging to debug logs viewer
    
    def _format_message(self, component: Component, level: LogLevel, message: str, **kwargs) -> str:
        """Format a structured log message."""
        timestamp = time.strftime("%H:%M:%S")
        
        # Base format: [timestamp] [COMPONENT] [LEVEL] message
        formatted = f"[{timestamp}] [{component.value}] [{level.value}] {message}"
        
        # Add extra context if provided
        if kwargs:
            context_parts = []
            for key, value in kwargs.items():
                if value is not None:
                    context_parts.append(f"{key}={value}")
            if context_parts:
                formatted += f" | {' '.join(context_parts)}"
        
        return formatted
    
    def log(self, component: Component, level: LogLevel, message: str, **kwargs):
        """Log a structured message."""
        formatted = self._format_message(component, level, message, **kwargs)
        
        # Always send to debug logs viewer
        if self._debug_enabled:
            from ..ui.components.debug_logs import add_debug_log
            add_debug_log(formatted)
        
        # Also print to console for critical levels
        if level in [LogLevel.ERROR, LogLevel.CRITICAL]:
            print(formatted, file=sys.stderr)
        elif level in [LogLevel.WARNING]:
            print(formatted, file=sys.stdout)
    
    def debug(self, component: Component, message: str, **kwargs):
        """Log a debug message."""
        self.log(component, LogLevel.DEBUG, message, **kwargs)
    
    def info(self, component: Component, message: str, **kwargs):
        """Log an info message."""
        self.log(component, LogLevel.INFO, message, **kwargs)
    
    def warning(self, component: Component, message: str, **kwargs):
        """Log a warning message."""
        self.log(component, LogLevel.WARNING, message, **kwargs)
    
    def error(self, component: Component, message: str, **kwargs):
        """Log an error message."""
        self.log(component, LogLevel.ERROR, message, **kwargs)
    
    def critical(self, component: Component, message: str, **kwargs):
        """Log a critical message."""
        self.log(component, LogLevel.CRITICAL, message, **kwargs)
    
    def exception(self, component: Component, message: str, exception: Exception, **kwargs):
        """Log an exception with traceback."""
        import traceback
        
        error_msg = f"{message}: {type(exception).__name__}: {exception}"
        self.error(component, error_msg, **kwargs)
        
        # Add traceback to debug logs
        if self._debug_enabled:
            from ..ui.components.debug_logs import add_debug_log
            tb_lines = traceback.format_exc().split('\n')
            for line in tb_lines:
                if line.strip():
                    add_debug_log(f"[{time.strftime('%H:%M:%S')}] [{component.value}] [TRACEBACK] {line}")


# Global logger instance
_logger = StructuredLogger()


def get_logger() -> StructuredLogger:
    """Get the global structured logger instance."""
    return _logger


# Convenience functions for direct usage
def log_debug(component: Component, message: str, **kwargs):
    """Log a debug message."""
    _logger.debug(component, message, **kwargs)


def log_info(component: Component, message: str, **kwargs):
    """Log an info message."""
    _logger.info(component, message, **kwargs)


def log_warning(component: Component, message: str, **kwargs):
    """Log a warning message."""
    _logger.warning(component, message, **kwargs)


def log_error(component: Component, message: str, **kwargs):
    """Log an error message."""
    _logger.error(component, message, **kwargs)


def log_critical(component: Component, message: str, **kwargs):
    """Log a critical message."""
    _logger.critical(component, message, **kwargs)


def log_exception(component: Component, message: str, exception: Exception, **kwargs):
    """Log an exception with traceback."""
    _logger.exception(component, message, exception, **kwargs)

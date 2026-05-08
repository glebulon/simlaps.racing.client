"""
SimLaps Telemetry Client - Main Entry Point

A desktop application that monitors ACE game logs and automatically
submits lap times to the SimLaps server.
"""

import asyncio
import sys
import os

# Ensure proper path setup for PyInstaller
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    application_path = os.path.dirname(sys.executable)
    # Add the _MEIPASS to path for bundled modules
    if hasattr(sys, '_MEIPASS'):
        sys.path.insert(0, sys._MEIPASS)
else:
    # Running as script - add parent of src to path
    application_path = os.path.dirname(os.path.abspath(__file__))
    parent_path = os.path.dirname(application_path)
    if parent_path not in sys.path:
        sys.path.insert(0, parent_path)

from src.ui.app import run_app


def main():
    """Main entry point for the application."""
    try:
        # Set up global exception handler for asyncio tasks
        def handle_asyncio_exception(loop, context):
            msg = context.get("message", "Unhandled exception in asyncio task")
            exception = context.get("exception")
            if exception:
                print(f"\n[ASYNCIO ERROR] {msg}: {exception}")
                import traceback
                traceback.print_exception(type(exception), exception, exception.__traceback__)
            else:
                print(f"\n[ASYNCIO ERROR] {msg}")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(handle_asyncio_exception)
        
        run_app()
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f"\n{'='*50}")
        print(f"Fatal error: {e}")
        print(f"{'='*50}")
        traceback.print_exc()
        # Keep console open so user can see the error
        if getattr(sys, 'frozen', False):
            input("\nPress Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    # Catch any import errors too
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\n{'='*50}")
        print(f"Startup error: {e}")
        print(f"{'='*50}")
        traceback.print_exc()
        if getattr(sys, 'frozen', False):
            input("\nPress Enter to exit...")
        sys.exit(1)

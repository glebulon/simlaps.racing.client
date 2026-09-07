"""
Settings Page - Configuration options.

Simplified: No API key required (uses signed payloads).
"""

import flet as ft
from dataclasses import replace
from typing import Optional, Callable

from ...utils.config import AppConfig, DEFAULT_SERVER_URL
from ...utils.structured_logger import Component, log_exception
from ...core.discord_notifier import DiscordNotifier
from ..components.feedback import show_snackbar
from ..components.mount_safe import mounted_page, safe_update


class SettingsPage(ft.Container):
    """
    Settings page for configuring the application.
    
    Note: No API key field - authentication uses signed payloads with
    an embedded app secret.
    """
    
    def __init__(
        self,
        config: AppConfig,
        on_back: Optional[Callable] = None,
        on_save: Optional[Callable[[AppConfig], None]] = None,
        on_test_connection: Optional[Callable] = None,
        on_test_discord: Optional[Callable] = None,
    ):
        self.config = config
        self.on_back = on_back
        self.on_save = on_save
        self.on_test_connection = on_test_connection
        self.on_test_discord = on_test_discord
        
        # Form fields
        self._server_url_field = ft.TextField(
            value=config.server_url,
            label="Server URL",
            hint_text=DEFAULT_SERVER_URL,
            border_color="#3d3d5c",
            focused_border_color="#7c3aed",
            bgcolor="#1e1e2e",
            color="#ffffff",
            label_style=ft.TextStyle(color="#888888"),
        )
        
        self._submit_invalid_switch = ft.Switch(
            value=config.submit_invalid_laps,
            active_color="#7c3aed",
        )
        
        # Discord fields
        self._discord_webhook_field = ft.TextField(
            value=config.discord_webhook_url or "",
            label="Discord Webhook URL",
            hint_text="https://discord.com/api/webhooks/...",
            border_color="#3d3d5c",
            focused_border_color="#7c3aed",
            bgcolor="#1e1e2e",
            color="#ffffff",
            label_style=ft.TextStyle(color="#888888"),
        )
        
        self._discord_enabled_switch = ft.Switch(
            value=config.discord_enabled,
            active_color="#7c3aed",
            on_change=self._discord_enabled_changed,
        )
        
        self._discord_pb_only_switch = ft.Switch(
            value=config.discord_pb_only,
            active_color="#7c3aed",
            disabled=not config.discord_enabled,
        )
        
        self._discord_test_status = ft.Text(
            "",
            size=12,
            color="#888888",
        )
        
        # Telemetry fields
        self._telemetry_enabled_switch = ft.Switch(
            value=config.telemetry_enabled,
            active_color="#7c3aed",
        )
        
        self._telemetry_output_path_field = ft.TextField(
            value=config.telemetry_output_path,
            label="Output Directory",
            hint_text=r"C:\Users\...\Documents\SimLaps\Telemetry",
            border_color="#3d3d5c",
            focused_border_color="#7c3aed",
            bgcolor="#1e1e2e",
            color="#ffffff",
            label_style=ft.TextStyle(color="#888888"),
        )

        self._telemetry_debug_logs_switch = ft.Switch(
            value=config.telemetry_debug_logs,
            active_color="#7c3aed",
        )
        
        self._connection_status = ft.Text(
            "",
            size=12,
            color="#888888",
        )
        
        super().__init__(
            content=self._build_content(),
            expand=True,
        )
    
    def _build_content(self) -> ft.Control:
        """Build the settings page content."""
        # Header with back button
        header = ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    icon_color="#ffffff",
                    on_click=lambda _: self.on_back() if self.on_back else None,
                ),
                ft.Text(
                    "Settings",
                    size=24,
                    weight=ft.FontWeight.W_700,
                    color="#ffffff",
                ),
            ],
            spacing=8,
        )
        
        # Server settings section
        server_section = self._build_section(
            "Server",
            [
                self._server_url_field,
                ft.Row(
                    controls=[
                        ft.OutlinedButton(
                            "Test Connection",
                            icon=ft.Icons.WIFI,
                            on_click=self._test_connection,
                            style=ft.ButtonStyle(
                                color="#888888",
                                side=ft.BorderSide(1, "#3d3d5c"),
                            ),
                        ),
                        self._connection_status,
                    ],
                    spacing=16,
                ),
            ],
        )
        
        # Behavior settings section
        behavior_section = self._build_section(
            "Behavior",
            [
                self._build_switch_row(
                    "Submit invalid laps",
                    "Also submit laps with penalties or off-track",
                    self._submit_invalid_switch,
                ),
            ],
        )
        
        # Discord settings section
        discord_section = self._build_section(
            "Discord Integration",
            [
                self._discord_webhook_field,
                ft.Row(
                    controls=[
                        ft.OutlinedButton(
                            "Test Webhook",
                            icon=ft.Icons.SEND,
                            on_click=self._test_discord_webhook,
                            style=ft.ButtonStyle(
                                color="#888888",
                                side=ft.BorderSide(1, "#3d3d5c"),
                            ),
                        ),
                        self._discord_test_status,
                    ],
                    spacing=16,
                ),
                self._build_switch_row(
                    "Enable Discord posting",
                    "Post lap times to Discord webhook",
                    self._discord_enabled_switch,
                ),
                self._build_switch_row(
                    "Post personal bests only",
                    "Only post new personal best laps to Discord",
                    self._discord_pb_only_switch,
                ),
            ],
        )
        
        # Telemetry settings section
        telemetry_section = self._build_section(
            "📊 Telemetry",
            [
                self._build_switch_row(
                    "Enable Telemetry Capture",
                    "Record high-frequency telemetry during sessions",
                    self._telemetry_enabled_switch,
                ),
                self._telemetry_output_path_field,
                self._build_switch_row(
                    "Save Debug Logs",
                    "Write raw SHM dump, capture JSONL, and diagnostics log to disk (only needed for troubleshooting)",
                    self._telemetry_debug_logs_switch,
                ),
            ],
        )
        
        # Save button
        save_button = ft.Button(
            "Save Settings",
            icon=ft.Icons.SAVE,
            on_click=self._save_settings,
            style=ft.ButtonStyle(
                bgcolor="#7c3aed",
                color="#ffffff",
                padding=16,
            ),
            width=200,
        )
        
        # Reset button
        reset_button = ft.TextButton(
            "Reset to Defaults",
            on_click=self._reset_settings,
            style=ft.ButtonStyle(color="#888888"),
        )
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Container(height=16),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                server_section,
                                behavior_section,
                                discord_section,
                                telemetry_section,
                                ft.Container(height=16),
                                ft.Row(
                                    controls=[save_button, reset_button],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    spacing=16,
                                ),
                            ],
                            scroll=ft.ScrollMode.AUTO,
                            spacing=16,
                        ),
                        expand=True,
                    ),
                ],
                expand=True,
            ),
            padding=20,
            bgcolor="#0f0f1a",
            expand=True,
        )
    
    def _build_section(self, title: str, controls: list) -> ft.Container:
        """Build a settings section."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        title,
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color="#888888",
                    ),
                    ft.Container(height=8),
                    *controls,
                ],
                spacing=12,
            ),
            padding=16,
            bgcolor="#1e1e2e",
            border_radius=12,
            border=ft.Border.all(1, "#3d3d5c"),
        )
    
    def _build_switch_row(
        self,
        title: str,
        subtitle: str,
        switch: ft.Switch,
    ) -> ft.Row:
        """Build a row with a switch control."""
        return ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        ft.Text(title, size=14, color="#ffffff"),
                        ft.Text(subtitle, size=12, color="#666666"),
                    ],
                    spacing=2,
                    expand=True,
                ),
                switch,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

    def _discord_enabled_changed(self, e) -> None:
        """Keep the Discord-only PB filter inactive when posting is off."""
        self._discord_pb_only_switch.disabled = not bool(
            self._discord_enabled_switch.value
        )
        safe_update(self._discord_pb_only_switch)
    
    async def _test_connection(self, e):
        """Test server connection."""
        self._connection_status.value = "Testing..."
        self._connection_status.color = "#ffd43b"
        safe_update(self._connection_status)
        
        if self.on_test_connection:
            success, message = await self.on_test_connection(
                self._server_url_field.value
            )
            if success:
                self._connection_status.value = "Connected"
                self._connection_status.color = "#51cf66"
            else:
                self._connection_status.value = f"{message}"
                self._connection_status.color = "#ff6b6b"
            safe_update(self._connection_status)
    
    async def _test_discord_webhook(self, e):
        """Test Discord webhook connection."""
        webhook_url = self._discord_webhook_field.value.strip()
        
        if not webhook_url:
            self._discord_test_status.value = "No webhook URL"
            self._discord_test_status.color = "#ff6b6b"
            safe_update(self._discord_test_status)
            return
        
        self._discord_test_status.value = "Testing..."
        self._discord_test_status.color = "#ffd43b"
        safe_update(self._discord_test_status)
        
        if self.on_test_discord:
            # Use the app's Discord test method
            success, message = await self.on_test_discord(webhook_url)
            if success:
                self._discord_test_status.value = "Connected"
                self._discord_test_status.color = "#51cf66"
            else:
                self._discord_test_status.value = f"Failed: {message}"
                self._discord_test_status.color = "#ff6b6b"
        else:
            # Fallback to basic URL validation
            if DiscordNotifier.validate_webhook_url(webhook_url):
                self._discord_test_status.value = "URL valid"
                self._discord_test_status.color = "#51cf66"
            else:
                self._discord_test_status.value = "Invalid URL format"
                self._discord_test_status.color = "#ff6b6b"
        
        safe_update(self._discord_test_status)
    
    def _save_settings(self, e):
        """Save current settings."""
        # Build a new value so Settings edits do not mutate the application's
        # active configuration before runtime reconciliation succeeds.
        updated_config = replace(
            self.config,
            server_url=self._server_url_field.value or DEFAULT_SERVER_URL,
            submit_invalid_laps=self._submit_invalid_switch.value,
            discord_webhook_url=self._discord_webhook_field.value.strip() or None,
            discord_enabled=self._discord_enabled_switch.value,
            discord_pb_only=self._discord_pb_only_switch.value,
            telemetry_enabled=self._telemetry_enabled_switch.value,
            telemetry_output_path=self._telemetry_output_path_field.value or "",
            telemetry_debug_logs=self._telemetry_debug_logs_switch.value,
        )
        
        try:
            if self.on_save:
                self.on_save(updated_config)
        except Exception as exc:
            log_exception(Component.UI, "Could not save settings", exc)
            page = mounted_page(self)
            if page is not None:
                show_snackbar(page, f"Could not save settings: {exc}", "#ff6b6b")
            return
        self.config = updated_config
        
        # Show success feedback
        page = mounted_page(self)
        if page is not None:
            show_snackbar(page, "Settings saved!", "#51cf66")
    
    def _reset_settings(self, e):
        """Reset settings to defaults and persist immediately."""
        default_config = AppConfig()
        try:
            if self.on_save:
                self.on_save(default_config)
        except Exception as exc:
            log_exception(Component.UI, "Could not reset settings", exc)
            page = mounted_page(self)
            if page is not None:
                show_snackbar(page, f"Could not reset settings: {exc}", "#ff6b6b")
            return

        # Update the page only after the defaults were applied successfully.
        self.config = default_config

        # Update UI fields from the new default config
        self._server_url_field.value = self.config.server_url
        self._submit_invalid_switch.value = self.config.submit_invalid_laps

        # Reset Discord fields
        self._discord_webhook_field.value = self.config.discord_webhook_url or ""
        self._discord_enabled_switch.value = self.config.discord_enabled
        self._discord_pb_only_switch.value = self.config.discord_pb_only
        self._discord_pb_only_switch.disabled = not self.config.discord_enabled
        self._discord_test_status.value = ""

        # Reset Telemetry fields
        self._telemetry_enabled_switch.value = self.config.telemetry_enabled
        self._telemetry_output_path_field.value = self.config.telemetry_output_path
        self._telemetry_debug_logs_switch.value = self.config.telemetry_debug_logs

        safe_update(self._server_url_field)
        safe_update(self._submit_invalid_switch)
        safe_update(self._discord_webhook_field)
        safe_update(self._discord_enabled_switch)
        safe_update(self._discord_pb_only_switch)
        safe_update(self._discord_test_status)
        safe_update(self._telemetry_enabled_switch)
        safe_update(self._telemetry_output_path_field)
        safe_update(self._telemetry_debug_logs_switch)

        # Show feedback
        page = mounted_page(self)
        if page is not None:
            show_snackbar(page, "Settings reset to defaults", "#7c3aed")
    
    def update_config(self, config: AppConfig):
        """Reload the form from the application's active configuration."""
        self.config = config
        self._server_url_field.value = config.server_url
        self._submit_invalid_switch.value = config.submit_invalid_laps
        
        # Update Discord fields
        self._discord_webhook_field.value = config.discord_webhook_url or ""
        self._discord_enabled_switch.value = config.discord_enabled
        self._discord_pb_only_switch.value = config.discord_pb_only
        self._discord_pb_only_switch.disabled = not config.discord_enabled
        
        # Update Telemetry fields
        self._telemetry_enabled_switch.value = config.telemetry_enabled
        self._telemetry_output_path_field.value = config.telemetry_output_path
        self._telemetry_debug_logs_switch.value = config.telemetry_debug_logs

        # Connection-test feedback belongs to the abandoned form state too.
        self._connection_status.value = ""
        self._discord_test_status.value = ""

        # Settings may be refreshed immediately before it is mounted. The
        # subsequent page.add() will render these values without an update.
        safe_update(self)

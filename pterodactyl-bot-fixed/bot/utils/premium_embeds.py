"""
utils/premium_embeds.py — Premium hosting provider embed system.

Features:
- Unified color scheme and styling
- Reusable embed builders
- Section-based layout with clean spacing
- Progress bars with percentage text
- Status indicators (online/offline/maintenance)
- Button integration
- Timestamp footers
- Error/success/warning/maintenance styles
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import discord

log = logging.getLogger("bot.utils.premium_embeds")


# ────────────────────────────────────────────────────────────────────
# Premium Color Palette
# ────────────────────────────────────────────────────────────────────

class PremiumColors:
    """Professional hosting provider color scheme."""
    # Primary brand colors
    PRIMARY = 0x1E3A5F      # Deep blue (Pterodactyl-inspired)
    PRIMARY_ACCENT = 0x2563EB   # Bright blue
    
    # Status colors
    SUCCESS = 0x10B981     # Emerald green (professional)
    WARNING = 0xF59E0B     # Amber (caution)
    ERROR = 0xEF4444       # Red (critical)
    MAINTENANCE = 0x8B5CF6  # Purple (maintenance mode)
    
    # Neutral colors
    NEUTRAL = 0x6B7280     # Slate gray
    BACKGROUND = 0x111827  # Dark background
    SURFACE = 0x1F2937     # Lighter surface
    
    # Uptime gradient
    UPTIME_EXCELLENT = 0x059669  # Dark green (99%+)
    UPTIME_GOOD = 0x10B981       # Green (95-99%)
    UPTIME_WARNING = 0xF59E0B    # Amber (90-95%)
    UPTIME_POOR = 0xEF4444       # Red (<90%)


class StatusIndicator:
    """Status badges and icons."""
    ONLINE = "🟢"
    OFFLINE = "🔴"
    MAINTENANCE = "🟣"
    PARTIAL = "🟡"
    UNKNOWN = "⚪"
    
    CHECKMARK = "✅"
    CROSS = "❌"
    WARNING = "⚠️"
    CLOCK = "🕐"
    SHIELD = "🛡️"
    ZEPHYR = "💨"
    FLAME = "🔥"
    GAUGE = "📊"
    SIGNAL = "📡"
    WRENCH = "🔧"
    TRENDING = "📈"


@dataclass
class EmbedSection:
    """A reusable embed section with title and fields."""
    title: Optional[str] = None
    fields: list[tuple[str, str, bool]] = None  # (name, value, inline)
    
    def __post_init__(self):
        if self.fields is None:
            self.fields = []


class PremiumEmbedBuilder:
    """
    Main embed builder with premium hosting provider styling.
    
    Usage:
        embed = PremiumEmbedBuilder(title="Node Status")
        embed.add_section("Resources", [
            ("CPU", "45%", True),
            ("RAM", "2.1 GB / 4 GB", True),
        ])
        embed.set_footer_timestamp()
        msg = embed.build()
    """
    
    def __init__(
        self,
        title: str,
        description: str = "",
        color: int = PremiumColors.PRIMARY,
        icon: str = "",
    ):
        """
        Initialize the builder.
        
        Args:
            title: Embed title
            description: Optional subtitle/description
            color: Color code (defaults to primary blue)
            icon: Optional emoji prefix for title
        """
        self.title = f"{icon} {title}" if icon else title
        self.description = description
        self.color = color
        self.sections: list[EmbedSection] = []
        self._footer_text: Optional[str] = None
        self._footer_icon: Optional[str] = None
        self._thumbnail: Optional[str] = None
        self._timestamp: bool = False
        
    def add_section(
        self,
        title: str,
        fields: list[tuple[str, str, bool]] = None,
    ) -> PremiumEmbedBuilder:
        """
        Add a titled section with fields.
        
        Args:
            title: Section title (e.g., "Resources", "Status")
            fields: List of (name, value, inline) tuples
        
        Returns:
            Self for chaining
        """
        if fields is None:
            fields = []
        self.sections.append(EmbedSection(title=title, fields=fields))
        return self
    
    def add_field(
        self,
        name: str,
        value: str,
        inline: bool = True,
    ) -> PremiumEmbedBuilder:
        """
        Add a field to the last section (or create a section if none exist).
        
        Args:
            name: Field name
            value: Field value
            inline: Whether to display inline
        
        Returns:
            Self for chaining
        """
        if not self.sections:
            self.sections.append(EmbedSection(title=None, fields=[]))
        self.sections[-1].fields.append((name, value, inline))
        return self
    
    def add_blank_line(self) -> PremiumEmbedBuilder:
        """Add a blank line for spacing."""
        self.add_field("\u200b", "\u200b", inline=False)
        return self
    
    def set_footer(self, text: str, icon: Optional[str] = None) -> PremiumEmbedBuilder:
        """
        Set custom footer text.
        
        Args:
            text: Footer text
            icon: Optional icon emoji
        
        Returns:
            Self for chaining
        """
        self._footer_text = f"{icon} {text}" if icon else text
        return self
    
    def set_footer_timestamp(self) -> PremiumEmbedBuilder:
        """Enable automatic timestamp footer."""
        self._timestamp = True
        return self
    
    def set_thumbnail(self, url: str) -> PremiumEmbedBuilder:
        """
        Set embed thumbnail.
        
        Args:
            url: Image URL
        
        Returns:
            Self for chaining
        """
        self._thumbnail = url
        return self
    
    def build(self) -> discord.Embed:
        """Build the final Discord embed."""
        embed = discord.Embed(
            title=self.title,
            description=self.description if self.description else None,
            color=self.color,
        )
        
        # Add sections and fields
        for section in self.sections:
            if section.title:
                # Add blank line before section for spacing
                if embed.fields:
                    embed.add_field(name="\u200b", value="\u200b", inline=False)
                # Section header (can't be bolded in field name directly, but we can use emoji)
                embed.add_field(name=f"━━ {section.title} ━━", value="\u200b", inline=False)
            
            for name, value, inline in section.fields:
                embed.add_field(name=name, value=value, inline=inline)
        
        # Set footer
        if self._timestamp:
            now = datetime.now(timezone.utc)
            footer_text = f"Last updated: {now.strftime('%H:%M:%S UTC')}"
            if self._footer_text:
                footer_text = f"{self._footer_text}  •  {footer_text}"
            embed.set_footer(text=footer_text)
        elif self._footer_text:
            embed.set_footer(text=self._footer_text)
        
        # Set thumbnail
        if self._thumbnail:
            embed.set_thumbnail(url=self._thumbnail)
        
        return embed


class StatusEmbedBuilder(PremiumEmbedBuilder):
    """Builder specialized for node status embeds."""
    
    def __init__(self, node_name: str, is_online: bool):
        icon = StatusIndicator.ONLINE if is_online else StatusIndicator.OFFLINE
        color = PremiumColors.SUCCESS if is_online else PremiumColors.ERROR
        status_text = "ONLINE" if is_online else "OFFLINE"
        
        super().__init__(
            title=f"Node: {node_name}",
            color=color,
            icon=icon,
        )
        
        self.is_online = is_online
        self.node_name = node_name
        self._status_value = f"**{status_text}**"
    
    def set_status_detail(self, detail: str) -> StatusEmbedBuilder:
        """Add detail text to status (e.g., 'Recovering', 'Maintenance')."""
        self._status_value = f"**{self._status_value}** — {detail}"
        return self
    
    def add_health_checks(
        self,
        tcp_healthy: bool,
        tcp_response_ms: float,
        http_healthy: bool,
        http_response_ms: float,
        http_status: Optional[int] = None,
    ) -> StatusEmbedBuilder:
        """
        Add health check results section.
        
        Args:
            tcp_healthy: TCP connectivity result
            tcp_response_ms: TCP response time
            http_healthy: HTTP endpoint result
            http_response_ms: HTTP response time
            http_status: Optional HTTP status code
        """
        tcp_icon = StatusIndicator.CHECKMARK if tcp_healthy else StatusIndicator.CROSS
        http_icon = StatusIndicator.CHECKMARK if http_healthy else StatusIndicator.CROSS
        
        tcp_text = f"{tcp_icon} Reachable ({tcp_response_ms:.0f}ms)"
        http_status_text = f"({http_response_ms:.0f}ms)" if http_healthy else f"{http_status or '?'} ({http_response_ms:.0f}ms)"
        http_text = f"{http_icon} {http_status_text}"
        
        self.add_section(
            "Health Checks",
            [
                (f"{StatusIndicator.SIGNAL} TCP", tcp_text, True),
                (f"{StatusIndicator.SIGNAL} HTTP", http_text, True),
            ],
        )
        return self
    
    def add_resources(
        self,
        memory_used: int,
        memory_total: int,
        disk_used: int,
        disk_total: int,
        cpu_cores: int,
    ) -> StatusEmbedBuilder:
        """
        Add resource usage section with progress bars.
        
        Args:
            memory_used: RAM used in MB
            memory_total: Total RAM in MB
            disk_used: Disk used in MB
            disk_total: Total disk in MB
            cpu_cores: CPU core count
        """
        from utils.helpers import fmt_bytes, progress_bar as create_progress_bar
        
        ram_bar = create_progress_bar(memory_used, memory_total)
        disk_bar = create_progress_bar(disk_used, disk_total)
        
        self.add_section(
            "Resource Usage",
            [
                (
                    f"{StatusIndicator.GAUGE} Memory",
                    f"{fmt_bytes(memory_used)} / {fmt_bytes(memory_total)}\n`{ram_bar}`",
                    False,
                ),
                (
                    f"{StatusIndicator.GAUGE} Disk",
                    f"{fmt_bytes(disk_used)} / {fmt_bytes(disk_total)}\n`{disk_bar}`",
                    False,
                ),
                (f"{StatusIndicator.ZEPHYR} CPU Cores", str(cpu_cores), True),
            ],
        )
        return self
    
    def add_error_state(self, reason: str) -> StatusEmbedBuilder:
        """Add error state information."""
        self.add_section(
            "Error Details",
            [
                (
                    f"{StatusIndicator.WARNING} Wings Unreachable",
                    f"Both TCP and HTTP health checks failed.\n\n**Reason:** {reason}",
                    False,
                ),
            ],
        )
        return self
    
    def add_uptime_info(
        self,
        online_since_iso: Optional[str] = None,
        offline_since_iso: Optional[str] = None,
        monthly_uptime_pct: float = 100.0,
        total_downtime_s: int = 0,
    ) -> StatusEmbedBuilder:
        """
        Add uptime tracking section.
        
        Args:
            online_since_iso: ISO-8601 timestamp when node came online
            offline_since_iso: ISO-8601 timestamp when node went offline
            monthly_uptime_pct: Monthly uptime percentage
            total_downtime_s: Total downtime in seconds
        """
        from utils.helpers import fmt_duration, fmt_ago
        from datetime import datetime
        
        fields = []
        
        if self.is_online and online_since_iso:
            try:
                dt = datetime.fromisoformat(online_since_iso.replace("Z", "+00:00"))
                online_for = fmt_duration(int((datetime.now(timezone.utc) - dt).total_seconds()))
                fields.append((f"{StatusIndicator.CLOCK} Online For", online_for, True))
            except Exception:
                pass
        
        if not self.is_online and offline_since_iso:
            try:
                dt = datetime.fromisoformat(offline_since_iso.replace("Z", "+00:00"))
                offline_for = fmt_duration(int((datetime.now(timezone.utc) - dt).total_seconds()))
                fields.append((f"{StatusIndicator.CLOCK} Offline For", f"{offline_for} ago", True))
            except Exception:
                pass
        
        # Uptime percentage with color-coded badge
        uptime_color = self._get_uptime_color(monthly_uptime_pct)
        uptime_badge = f"{monthly_uptime_pct:.2f}% {uptime_color}"
        fields.append((f"{StatusIndicator.TRENDING} Monthly Uptime", uptime_badge, True))
        
        if total_downtime_s > 0:
            fields.append((
                f"{StatusIndicator.FLAME} Total Downtime",
                fmt_duration(total_downtime_s),
                True,
            ))
        
        if fields:
            self.add_section("Uptime", fields)
        
        return self
    
    @staticmethod
    def _get_uptime_color(pct: float) -> str:
        """Return color badge text based on uptime percentage."""
        if pct >= 99.9:
            return "🟢 Excellent"
        elif pct >= 99.0:
            return "🟢 Good"
        elif pct >= 95.0:
            return "🟡 Fair"
        else:
            return "🔴 Poor"


class UptimeOverviewBuilder(PremiumEmbedBuilder):
    """Builder for the comprehensive uptime overview embed."""
    
    def __init__(self):
        super().__init__(
            title="Node Uptime Overview",
            color=PremiumColors.PRIMARY,
            icon=StatusIndicator.GAUGE,
        )
    
    def add_node_status(
        self,
        node_name: str,
        is_online: bool,
        online_since_iso: Optional[str] = None,
        offline_since_iso: Optional[str] = None,
        monthly_uptime_pct: float = 100.0,
        last_online_session_s: int = 0,
        last_downtime_s: int = 0,
        total_downtime_s: int = 0,
    ) -> UptimeOverviewBuilder:
        """
        Add a node status line to the overview.
        
        Args:
            node_name: Node display name
            is_online: Current online status
            online_since_iso: When it came online (ISO-8601)
            offline_since_iso: When it went offline (ISO-8601)
            monthly_uptime_pct: Monthly uptime percentage
            last_online_session_s: Last session duration
            last_downtime_s: Last downtime duration
            total_downtime_s: Total cumulative downtime
        """
        from utils.helpers import fmt_duration
        from datetime import datetime
        
        icon = StatusIndicator.ONLINE if is_online else StatusIndicator.OFFLINE
        
        # Build status line
        lines = [f"{icon} **{node_name}**"]
        
        if is_online and online_since_iso:
            try:
                dt = datetime.fromisoformat(online_since_iso.replace("Z", "+00:00"))
                online_for = fmt_duration(int((datetime.now(timezone.utc) - dt).total_seconds()))
                lines.append(f"  ↳ Online For: {online_for}")
            except Exception:
                pass
        
        if not is_online and offline_since_iso:
            try:
                dt = datetime.fromisoformat(offline_since_iso.replace("Z", "+00:00"))
                offline_for = fmt_duration(int((datetime.now(timezone.utc) - dt).total_seconds()))
                lines.append(f"  ↳ Offline Since: {offline_for} ago")
            except Exception:
                pass
        
        if not is_online and last_online_session_s > 0:
            lines.append(f"  ↳ Last Online Session: {fmt_duration(last_online_session_s)}")
        
        if not is_online and last_downtime_s > 0:
            lines.append(f"  ↳ Last Downtime: {fmt_duration(last_downtime_s)}")
        
        # Uptime with color
        uptime_color = StatusEmbedBuilder._get_uptime_color(monthly_uptime_pct)
        lines.append(f"  ↳ Monthly Uptime: **{monthly_uptime_pct:.2f}%** {uptime_color}")
        
        if total_downtime_s > 0 and is_online:
            lines.append(f"  ↳ Total Downtime: {fmt_duration(total_downtime_s)}")
        
        value = "\n".join(lines)
        self.add_field(name="\u200b", value=value, inline=False)
        
        return self


class MaintenanceAlertBuilder(PremiumEmbedBuilder):
    """Builder for maintenance and downtime alerts."""
    
    def __init__(self, node_name: str, reason: str = "Unplanned maintenance"):
        super().__init__(
            title=f"Maintenance Alert — {node_name}",
            color=PremiumColors.MAINTENANCE,
            icon=StatusIndicator.MAINTENANCE,
        )
        
        self.add_section(
            "Alert",
            [
                (
                    f"{StatusIndicator.WARNING} Status",
                    f"Node **{node_name}** has gone offline.",
                    False,
                ),
                (
                    f"{StatusIndicator.SHIELD} Reason",
                    reason,
                    False,
                ),
            ],
        )
    
    def add_admin_notification(self, admin_mentions: str) -> MaintenanceAlertBuilder:
        """Add admin notification section."""
        self.add_section(
            "Notifications",
            [
                (
                    f"{StatusIndicator.CHECKMARK} Admins Notified",
                    admin_mentions,
                    False,
                ),
            ],
        )
        return self
    
    def add_eta(self, eta_text: str) -> MaintenanceAlertBuilder:
        """Add estimated time to resolution."""
        self.add_field(
            f"{StatusIndicator.CLOCK} ETA",
            eta_text,
            False,
        )
        return self


class RecoveryAlertBuilder(PremiumEmbedBuilder):
    """Builder for recovery/resolution alerts."""
    
    def __init__(self, node_name: str):
        super().__init__(
            title=f"Node Recovered — {node_name}",
            color=PremiumColors.SUCCESS,
            icon=StatusIndicator.ONLINE,
        )
        
        self.add_section(
            "Resolution",
            [
                (
                    f"{StatusIndicator.CHECKMARK} Status",
                    f"Node **{node_name}** is back online!",
                    False,
                ),
            ],
        )
    
    def add_downtime_summary(self, downtime_s: int) -> RecoveryAlertBuilder:
        """Add downtime summary."""
        from utils.helpers import fmt_duration
        
        self.add_field(
            f"{StatusIndicator.CLOCK} Total Downtime",
            f"**{fmt_duration(downtime_s)}**",
            False,
        )
        return self
    
    def add_incident_number(self, incident_id: str) -> RecoveryAlertBuilder:
        """Add incident tracking number."""
        self.add_field(
            f"{StatusIndicator.SHIELD} Incident ID",
            f"`{incident_id}`",
            True,
        )
        return self


# ────────────────────────────────────────────────────────────────────
# Convenience factories for common embed types
# ────────────────────────────────────────────────────────────────────

def create_error_embed(title: str, description: str) -> discord.Embed:
    """Quick error embed builder."""
    return PremiumEmbedBuilder(
        title=title,
        description=description,
        color=PremiumColors.ERROR,
        icon=StatusIndicator.CROSS,
    ).set_footer_timestamp().build()


def create_success_embed(title: str, description: str) -> discord.Embed:
    """Quick success embed builder."""
    return PremiumEmbedBuilder(
        title=title,
        description=description,
        color=PremiumColors.SUCCESS,
        icon=StatusIndicator.CHECKMARK,
    ).set_footer_timestamp().build()


def create_warning_embed(title: str, description: str) -> discord.Embed:
    """Quick warning embed builder."""
    return PremiumEmbedBuilder(
        title=title,
        description=description,
        color=PremiumColors.WARNING,
        icon=StatusIndicator.WARNING,
    ).set_footer_timestamp().build()


def create_info_embed(title: str, description: str) -> discord.Embed:
    """Quick info embed builder."""
    return PremiumEmbedBuilder(
        title=title,
        description=description,
        color=PremiumColors.PRIMARY,
        icon=StatusIndicator.GAUGE,
    ).set_footer_timestamp().build()

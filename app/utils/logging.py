"""Rich-backed logging for the Quran video generator.

Everything the application prints goes through here so that the terminal
output stays consistent and a full transcript is always written to logs/.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

QVG_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warn": "yellow",
        "error": "bold red",
        "accent": "gold1",
        "muted": "grey62",
    }
)

console = Console(theme=QVG_THEME, highlight=False, soft_wrap=False)

_LOGGER_NAME = "qvg"
_configured = False


def setup_logging(logs_dir: Path, level: Optional[str] = None) -> logging.Logger:
    """Configure the shared logger. Safe to call more than once."""
    global _configured

    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    level_name = (level or os.environ.get("QVG_LOG_LEVEL") or "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False

    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        omit_repeated_times=False,
        rich_tracebacks=True,
        markup=False,
        log_time_format="[%H:%M:%S]",
    )
    rich_handler.setLevel(logger.level)
    logger.addHandler(rich_handler)

    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_handler = logging.FileHandler(
            logs_dir / f"render_{stamp}.log", encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s")
        )
        logger.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - only on unwritable filesystems
        console.print(f"[warn]Could not open a log file: {exc}[/warn]")

    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def banner(title: str, subtitle: str = "") -> None:
    body = f"[accent]{title}[/accent]"
    if subtitle:
        body += f"\n[muted]{subtitle}[/muted]"
    console.print(Panel.fit(body, border_style="accent", padding=(0, 3)))


def success(message: str) -> None:
    get_logger().info(message)
    console.print(f"[success]SUCCESS[/success]  {message}")


def kv_table(title: str, rows: list[tuple[str, str]]) -> None:
    table = Table(title=title, title_style="accent", box=None, pad_edge=False)
    table.add_column("", style="muted", no_wrap=True)
    table.add_column("", style="white")
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)


class QVGError(Exception):
    """A user-facing error: message is meant to be read by a human."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> None:
        body = f"[error]{self.message}[/error]"
        if self.hint:
            body += f"\n\n[muted]{self.hint}[/muted]"
        console.print(Panel(body, title="Error", border_style="red", padding=(1, 2)))

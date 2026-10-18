"""StoryPool local dashboard.

A minimal stdlib-only Python web server serving real-time cluster status.
"""

from .server import DashboardServer, main

__all__ = ["DashboardServer", "main"]

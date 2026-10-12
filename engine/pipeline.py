"""Public editing API used by the desktop interface and command line."""
from .editing import Config
from .render import extract_frame, run_montage

__all__ = ['Config', 'extract_frame', 'run_montage']

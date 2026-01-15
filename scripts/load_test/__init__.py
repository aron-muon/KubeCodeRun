"""Load testing tool for VM sizing.

A comprehensive load testing package to help determine optimal VM sizing
for production deployment of the Code Interpreter API.
"""

try:
    from src._version import __version__
except ImportError:
    __version__ = "0.0.0-dev"

"""#4 Scan / #5 Patch result: run Semgrep through the Scout Agent from ../proto_semgrep."""
import sys

import config

sys.path.insert(0, str(config.ROOT / "proto_semgrep"))


def scan(path):
    """Returns list of Finding objects, or raises SystemExit if semgrep is not installed."""
    from scout_agent import ScoutAgent
    return ScoutAgent().scan(path)

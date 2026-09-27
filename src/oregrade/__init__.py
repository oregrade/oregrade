"""oregrade — a point-in-time fact sheet for one open source repository.

Not a score. See experiment/ for why: a composite score was built,
pre-registered, and tested, and it did not predict. What survives is the
retrieval half — facts about a repository at a date, with provenance on every
line.
"""
from .dossier import Options, build
from .render import RENDERERS, to_json, to_markdown, to_yaml

__version__ = "1.0.0"
__all__ = ["build", "Options", "to_yaml", "to_json", "to_markdown", "RENDERERS"]

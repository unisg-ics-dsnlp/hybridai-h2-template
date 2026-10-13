"""A FrameX ontology of the Swiss rail passenger-transport system.

Datasets from data.sbb.ch are mapped onto one frame-logic knowledge base whose
spine is the national stop-point registry. The meta model lives in
`ontology/*.fx` and is machine-checked on its own; datasets attach to it one at
a time through a short declarative spec per dataset.
"""

from .builder import BuildError, add, init
from .registry import INIT_DATASETS, LAYERS, SPINE, available

__all__ = [
    "BuildError",
    "INIT_DATASETS",
    "LAYERS",
    "SPINE",
    "add",
    "available",
    "init",
]

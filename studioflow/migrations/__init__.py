"""Ordered, additive schema migrations. False leaves a migration pending."""

from .v001_workspace import apply as v1
from .v002_catalog_uniqueness import apply as v2
from .v003_aplus import apply as v3
from .v004_usage import apply as v4

MIGRATIONS = ((1, v1), (2, v2), (3, v3), (4, v4))

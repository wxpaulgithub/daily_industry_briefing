"""Independent opportunity intelligence pipeline."""

from .fetcher import fetch_opportunities
from .models import ProjectOpportunity

__all__ = ["ProjectOpportunity", "fetch_opportunities"]

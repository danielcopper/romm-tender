"""Cover art: Tender's per-ROM cover cache, the grid copy Steam shows, and its cleanup.

The package's public API is the :class:`ArtworkService` façade; its modules
share the reading of whether a cover in Steam's grid directory is Tender's.
"""

from services.artwork.service import ArtworkService, ArtworkServiceConfig

__all__ = ["ArtworkService", "ArtworkServiceConfig"]

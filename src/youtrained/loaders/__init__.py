"""Dataset loaders. Add a new dataset by registering its loader in LOADERS."""

from .audioset import AudioSetLoader
from .common import Loader, download_to_cache, run_loader
from .laion_disco import LaionDisco12MLoader
from .musiccaps import MusicCapsLoader

LOADERS: dict[str, Loader] = {
    MusicCapsLoader.name: MusicCapsLoader(),
    AudioSetLoader.name: AudioSetLoader(),
    LaionDisco12MLoader.name: LaionDisco12MLoader(),
}

__all__ = ["LOADERS", "Loader", "download_to_cache", "run_loader"]

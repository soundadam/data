"""Kind `listen`: a listening pack as `listen zip` (soundadam/listen) writes it, unzipped.

The release holds the zip's top folder as it is: `index.html`, `listen.css`,
`listen.js`, `design/`, `manifest.js` (the pack as a script), the audio and
figures the manifest names, and the spectrograms and waveform outlines under
`spec/` and `wave/`. The page is listen's own, whole, outside the console
frame: `/c/<id>/` is its index.html and every other file of the release is
served beside it, to the same members, so its relative links work as they do
from disk. There is no JSON; data.api lists and links the files like any
release's.
"""

from __future__ import annotations

from dataservice.kinds import NotFound

INDEX = "index.html"
NEEDS = (INDEX, "listen.js", "manifest.js")


class View:
    page = INDEX

    def __init__(self, release):
        missing = [name for name in NEEDS if name not in release.files]
        if missing:
            raise ValueError(f"{release.competition.id}: not a listen bundle, no {', '.join(missing)}")
        self.release = release

    def answer(self, path: list[str], query: dict) -> dict:
        raise NotFound("/".join(path))

"""Override upstream data collection: this application ships no sample DICOMs.

The application reads and writes pixel bytes directly and does not need palette,
charset, example, or decoder test datasets. Ordinary Python imports remain
available to PyInstaller's module analysis.
"""

from PyInstaller.utils.hooks import collect_submodules

datas = []
# pydicom registers these wrapper modules with importlib during package import.
# The optional native codecs themselves are not installed or bundled.
hiddenimports = collect_submodules("pydicom.pixels.decoders") + collect_submodules(
    "pydicom.pixels.encoders"
)

"""Override upstream data collection: this application ships no sample DICOMs.

The application reads and writes pixel bytes directly and does not need palette,
charset, example, or decoder test datasets. Ordinary Python imports remain
available to PyInstaller's module analysis.
"""

datas = []
hiddenimports = []

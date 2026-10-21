"""Disable pydicom's documentation-only example loader in frozen applications.

pydicom 3.0 imports its examples on package import. Those examples are not part
of this application, and can attempt downloads if sample files are absent.
Installing an empty documentation module before import avoids both network
access and bundling any upstream DICOM sample files. The data processing API
and the installed library are unchanged.
"""

import sys
import types

sys.modules["pydicom.examples"] = types.ModuleType("pydicom.examples")

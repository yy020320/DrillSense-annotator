# Third-party notices

DrillSense Annotator depends on the following open-source packages. Their licenses and notices are distributed by the packages themselves and must be retained in any binary redistribution.

| Package | Purpose | License to verify at release time |
|---|---|---|
| NumPy | Numerical arrays and feature calculations | BSD-3-Clause |
| PyQtGraph | Interactive plotting | MIT |
| PySide6 | Qt desktop user interface and multimedia | LGPL-3.0-only / LGPL-3.0-or-later components |
| SoundFile | WAV/audio I/O | BSD-3-Clause; depends on libsndfile |

Before publishing a Windows executable, include the corresponding Qt, libsndfile and other transitive notices in the release archive. Do not describe the binary as fully self-contained from a licensing perspective without checking the generated bundle.

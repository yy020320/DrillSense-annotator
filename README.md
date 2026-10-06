# DrillSense Annotator

DrillSense Annotator is a desktop workstation for manually reviewing and annotating drilling-acoustic recordings from a single-channel field recording. It provides synchronized audio playback, waveform and acoustic views, interval annotation, confidence and reference-quality fields, project persistence, CSV import/export, and an audit-friendly annotation table.

本仓库只发布标注工具的通用程序代码，不包含现场原始录音、人工标注结果或训练模型。现场数据应在获得授权并完成脱敏后单独分发。

## Features

- Review WAV recordings with a synchronized time cursor.
- Inspect waveform, energy, spectral flux, high/low-band ratio and clipping rate.
- Create, edit, filter, import and export interval annotations.
- Record confidence, transition type, source device and reference quality.
- Save and reopen annotation projects as JSON.
- Use the left navigation rail to focus the annotation desk, recordings, annotation sets or quality review.

## Installation from source

Python 3.11 or newer is recommended. Create a virtual environment and install the pinned major dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the application:

```powershell
python src\drillsense_annotator.py
```

Open an audio file directly:

```powershell
python src\drillsense_annotator.py --audio path\to\recording.wav
```

Import one or more CSV annotation files:

```powershell
python src\drillsense_annotator.py --csv path\to\annotations.csv
```

The application stores autosave data in a local `data` directory beside the source checkout or beside the packaged executable. Do not place confidential recordings in the repository.

## Windows executable

The repository contains the source needed to reproduce the executable. A maintained release should be built in a clean virtual environment with PyInstaller. The research workspace also contains a tested one-file executable, but binary releases should be attached to a tagged release rather than committed to the source tree.

## Data and privacy

The current field recordings contain identifiable project context and are not included here. Before publishing example data, remove project names, exact site coordinates, personnel information and any metadata that could identify the construction site. A small synthetic or fully de-identified WAV/CSV example is recommended for smoke testing.

## Citation

If you use the software, cite the associated paper and the release DOI when available. The repository includes a draft `CITATION.cff`; update the authors, repository URL and DOI before public release.

## License

The code is released under the MIT License. Third-party packages remain under their own licenses; see `docs/THIRD_PARTY_NOTICES.md`.

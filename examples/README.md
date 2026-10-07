# DrillSense Demo data

This folder contains a synthetic demonstration recording for testing DrillSense Annotator.
It is not a real construction-site recording and contains no field or personal information.

## Files

- `demo_recording.wav`: 24 s, mono, 48 kHz, 16-bit synthetic audio.
- `demo_annotations.csv`: interval labels matching the recording.

## Test

From the repository root, run:

```powershell
python src\drillsense_annotator.py --audio examples\demo_recording.wav --csv examples\demo_annotations.csv
```

The intervals are 4-9 s left-boom drilling, 9-14 s right-boom drilling, 14-19 s overlapping drilling, and 19-22 s non-operational interference.

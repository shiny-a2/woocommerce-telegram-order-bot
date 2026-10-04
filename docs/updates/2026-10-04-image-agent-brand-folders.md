# Image agent 1.3.2: finished images in brand folders

The Windows image agent now finds Photoshop output in brand and raw subfolders when the owner runs the existing send action. This fixes a case where prepared images remained unsent because only the top-level output folder was scanned.

The update keeps product matching and duplicate safeguards in place and reduces repeated folder scans during large batches. Integration tests covered nested output folders and ambiguous duplicate filenames. Installation and live delivery on the owner's PC are still pending verification.

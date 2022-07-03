VFR
===
Video frame replacer

# Description
Replaced video frames with replacements (e.g. super-resolution frames) while keeping original timestamps in variable frame rate videos.

# Usage
```
python3 frame_replace.py -i original.mp4 -r super_resolution/%08d.png -o out.mp4 --vscale 2
```

# LICENSE
GPL-3.0
# Wake-word files (not committed)

Put these here; everything in this folder except this README is gitignored:

- `PicovoiceAccessKey.txt`: your Picovoice AccessKey (one line).
- `hey-coco_ios.ppn`: the "hey coco" keyword trained for iOS.
- `hey-coco_mac.ppn` (optional): the macOS keyword, used when the app runs on a Mac.

Then `make display-build` (or regenerate the project with `make display-gen`). See
`docs/wake-word.md`.

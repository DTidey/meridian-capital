# 2026-10-21: Pillow and certifi advisories (packet 36)

- **CI broke without a code change.** New pip-audit advisories against pillow 12.3.0 and certifi
  2026.6.17 failed `make security` on every PR, so the Dependabot batch (#83-#88) could not
  merge; `main` would fail the same way on its next run.
- **certifi fixed, pillow ignored by name.** Pillow's only fix is 13.0.0, and streamlit (pinned
  and latest) requires `pillow<13`. Owner chose named, time-boxed ignores over forcing pillow 13
  under streamlit.
- **Why the ignores are safe.** Every advisory needs Pillow to open or re-save an
  attacker-supplied image (PDF, GIF, PPM, TIFF, EPS). Our code never imports PIL or decodes
  images (charts are plotly); pillow is only a matplotlib and streamlit dependency. A test fails
  if a PIL import appears.
- **Time box is a test, not a reminder.** Once pillow 13 is pinned, a test fails until the
  ignores are removed, so they cannot outlive the cap.

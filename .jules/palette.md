## 2026-09-30 - Micro-feedback state and non-form control keyboard handling
**Learning:** Inputs placed outside standard `<form>` elements (such as auth tokens in modal/panels) lose implicit Enter key form submission, disrupting keyboard-only workflows. Additionally, visual feedback states on copy buttons can become stuck if click-debounce timer handles are not tracked across rapid clicks.
**Action:** Always add an explicit `Enter` keydown handler to non-form inputs that trigger actions, and use explicit timer handles for transient UI state toggles.

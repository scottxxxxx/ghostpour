# Vendored form blocks

`form_block.py` and `form_definition_*.json` are copied from the I-765 app repo
(scottxxxxx/i765-helper) and are never edited here:

* `I765Helper/Scripts/form_block.py`
* `I765Helper/Sources/Resources/MockFixtures/form_definition_<form>.json`

Vendored at app commit: 9961f94da557e8f15bf2afb49d894275f92a8070 (the lead's final pin, 2026-10-29).

To move a form: copy the three files at the app's new commit, update the commit
above and `PINS` in tests/test_form_interviewer_lane.py to the app's sha256,
then run `python3 qa/form_blocks/build.py`. The test pins served block ==
generator(definition) and sha256(definition) == the app's pin.

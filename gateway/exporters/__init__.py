"""Document/deck/etc. export helpers (Phase C of the OpenSwarm import).

Pure-function exporters that take structured input + an output directory
and return the on-disk path of the produced artifact. No LLM calls — these
are mechanical writers driven by the synthesizer through `[generate_doc]`
and `[generate_deck]` action verbs.
"""

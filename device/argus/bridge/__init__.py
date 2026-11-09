"""Cloud bridge: the teammates' proven hat code (``hat.main``) -> our pairing, on-disk queue and uploader.

The bridge never opens I2C, GPIO or the camera. It
  * starts their ``python -m hat.main`` as a child process (own process group) and reads its console output,
  * tails their ``events.jsonl`` (from a saved byte offset), their ``clips/`` folder and the SCD41 air log,
  * maps each event to ``contracts/event.schema.json`` and writes it into OUR queue (``EventQueue.commit``),
    where the normal uploader thread sends it.

Spec: .plans/hardware-cloud-bridge-spec.md (sections 4 and 6). Run it with ``argus-cli bridge run``.

Modules: ``mapper`` (their event -> ours, pure), ``tail`` (offset-persisting tailer), ``clips`` (wait/remux),
``launcher`` (child process), ``air`` (latest air sample), ``service`` (the orchestrator), ``cli``.
"""

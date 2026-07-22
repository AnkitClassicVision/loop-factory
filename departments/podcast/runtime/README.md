# podcast runtime

This department uses the FACTORY-STANDARD components (no per-department copies):
- department manager loop: factory/manager.py (run with --department podcast)
- self-heal ladder: factory/heal_ladder.py
- human-in-the-loop bridge: factory/human_in_the_loop.py
- estate watchdog: factory/estate_manager.py
- runtime enforcement kernel: kernel/ (wire via a thin kernel bridge)
- release-pinning: factory/release.py

F1 (human, owner): run the intent interview (interview/INTERVIEW.md), lock the
intent, then author the charter setpoints + funnel subgraphs. F2-F4 then govern
and hand-author the runtime nodes from the procedural graph, shadow, and pin a
release. Department-SPECIFIC node code lives here; factory machinery does not.

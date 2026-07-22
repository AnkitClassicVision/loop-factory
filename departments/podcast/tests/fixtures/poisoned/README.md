These are poisoned test fixtures for negative-testing a podcast-pipeline watchdog. Each file contains exactly one defect that a QA gate must catch to validate the watchdog's failure detection capability.

unit_missing.json: Encodes a silent unit gap poison where an inventory-listed unit (podcast-prep-sweep.timer) has no corresponding observation rows. The watchdog gate must detect this missing unit and fail, proving it can catch inventory gaps in monitoring.

stale_receipt.json: Contains a stale receipt poison where the loop's timestamp is older than 26 hours compared to "now". The gate must identify this aged timestamp and fail, ensuring the watchdog detects outdated receipts in the pipeline.

forged_receipt.json: Encodes a signature forgery poison where the receipt's signature doesn't match its expected value. The gate must detect this mismatch and fail, proving the watchdog can verify authenticity and catch tampered receipts.

resolved_fingerprint_recurs.json: Contains a fingerprint recurrence poison where a resolved incident exists with matching new_observation fingerprint. The gate must catch this recurrence and fail, ensuring the watchdog detects patterns that reappear after being resolved.

non_allowlisted_playbook.json: Encodes an unauthorized playbook poison where the heal request specifies a playbook id not in the allowlist. The gate must detect this unauthorized access and fail, proving the watchdog can enforce proper playbook authorization.

These fixtures prove a QA gate can actually fail when encountering their specific defects.

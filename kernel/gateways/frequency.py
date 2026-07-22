import json
from pathlib import Path


class FrequencyDenied(RuntimeError):
    pass


class FrequencyService:
    def __init__(self, ledger_path):
        self.ledger_path = Path(ledger_path)
        if self.ledger_path.exists():
            self._rows = [
                json.loads(line)
                for line in self.ledger_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            self._rows = []

    def _person_count(self, person, now, window=30.0):
        return len(
            [
                row
                for row in self._rows
                if row["person"] == person and now - row["now"] < window
            ]
        )

    def _org_recent(self, org, now, window=7.0):
        return {
            row["person"]
            for row in self._rows
            if row["org"] == org and now - row["now"] < window
        }

    def reserve_slot(self, person, org, now):
        if self._person_count(person, now, 30.0) >= 3:
            raise FrequencyDenied("person cap")

        org_people = self._org_recent(org, now, 7.0)
        if org_people and person not in org_people:
            raise FrequencyDenied("org cap")

        row = {"person": person, "org": org, "now": now}
        self._rows.append(row)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(row) + "\n")

        return f"slot-{person}-{now}"

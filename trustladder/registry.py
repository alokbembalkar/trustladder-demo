"""
The issuer registry: the directory of participating issuers and their signed,
append-only code files.

Three roles, matching the architecture in the proposal:

    Publisher  runs INSIDE the hospital. Holds the signing key. Turns each bill
               it issues into two codes and appends them to the hospital's file.
    Registry   a neutral host. Stores each issuer's signed files and the issuer
               directory (name -> salt -> public key -> file). Holds no patient
               data, only codes.
    Verifier   runs INSIDE the insurer. Mirrors the file, checks every signature,
               recomputes the codes from the bill in hand and looks them up
               locally, so the registry never learns which bill was checked.

On disk (under the demo's data folder):

    directory.json               public: one entry per participating issuer
    registry/<issuer_id>.json    public: that issuer's signed batches of codes
    issuer_keys/<issuer_id>.json PRIVATE to the hospital: its signing key
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from . import crypto
from .models import BillFields, RegistryAnswer
from .reader import canonical_record, canonical_ticket


class RegistryStore:
    """Read/write access to the directory and the published files on disk."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.registry_dir = self.data_dir / "registry"
        self.keys_dir = self.data_dir / "issuer_keys"
        self.directory_path = self.data_dir / "directory.json"

    # ------------------------------------------------------------------ directory

    def load_directory(self) -> dict:
        """Return {issuer_id: {name, city, salt, public_key, ticket_prefix}}."""
        if not self.directory_path.exists():
            return {}
        return json.loads(self.directory_path.read_text())

    def _save_directory(self, directory: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.directory_path.write_text(json.dumps(directory, indent=2, sort_keys=True))

    def enrol_issuer(self, issuer_id: str, name: str, city: str, ticket_prefix: str,
                     when: str | None = None) -> None:
        """Sign an issuer up: create its key pair and salt, list it publicly.

        This is the whole onboarding step for a hospital: no integration project
        and no data sharing, just a key pair and a line in the directory.
        """
        directory = self.load_directory()
        private_b64, public_b64 = crypto.new_signing_key()
        # The coverage window is the issuer's public undertaking: "I publish a code for
        # every bill I issue, from this date onwards, and I have published up to this
        # date." It is what turns a missing ticket into a statement. Without it, silence
        # from the registry could only ever mean "we do not know".
        # It holds dates, not documents: no patient, no amount, no bill.
        covers_from = (when or _dt.date.today().isoformat())[:10]
        directory[issuer_id] = {
            "name": name,
            "city": city,
            "salt": crypto.new_salt(),
            "public_key": public_b64,
            "ticket_prefix": ticket_prefix,
            "covers_from": covers_from,
            "covers_to": covers_from,
        }
        self._save_directory(directory)
        # The private key is written to the hospital's own folder only.
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        (self.keys_dir / f"{issuer_id}.json").write_text(
            json.dumps({"issuer_id": issuer_id, "private_key": private_b64}, indent=2)
        )
        # Start an empty published file.
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        path = self.registry_dir / f"{issuer_id}.json"
        if not path.exists():
            path.write_text(json.dumps({"issuer": issuer_id, "batches": []}, indent=2))

    # ------------------------------------------------------------------ files

    def file_path(self, issuer_id: str) -> Path:
        return self.registry_dir / f"{issuer_id}.json"

    def load_file(self, issuer_id: str) -> dict:
        path = self.file_path(issuer_id)
        if not path.exists():
            return {"issuer": issuer_id, "batches": []}
        return json.loads(path.read_text())


class Publisher:
    """The hospital-side component. One instance per issuer.

    It is the only code that ever sees a bill's details on the issuing side, and
    the only code that holds the signing key.
    """

    def __init__(self, store: RegistryStore, issuer_id: str):
        self.store = store
        self.issuer_id = issuer_id
        entry = store.load_directory()[issuer_id]
        self.salt = entry["salt"]
        self.ticket_prefix = entry["ticket_prefix"]
        key_file = store.keys_dir / f"{issuer_id}.json"
        self._private_key = json.loads(key_file.read_text())["private_key"]

    def new_ticket(self, rng=None) -> str:
        """The random reference that gets printed on the bill (seeded only when the demo
        world is built, so the sample files survive a rebuild; see `crypto.new_ticket`)."""
        return crypto.new_ticket(self.ticket_prefix, rng)

    def entry_for(self, bill) -> list[str]:
        """Return the [key, value] pair for one document (a bill or a discharge
        summary): two codes, nothing else."""
        key = crypto.code(canonical_ticket(bill.ticket), self.salt)
        value = crypto.code(canonical_record(bill), self.salt)
        return [key, value]

    def publish(self, bills: list, when: _dt.datetime | None = None) -> dict:
        """Append one signed batch covering `bills` (bills and/or discharge summaries)
        to the issuer's file.

        Append-only: earlier batches are never re-written or re-signed. A
        correction would be a later entry with the same key, which supersedes the
        earlier one while both stay in the record for audit.
        """
        when = when or _dt.datetime.now().astimezone()
        batch = {
            "issuer": self.issuer_id,
            "published": when.isoformat(timespec="seconds"),
            "count": len(bills),
            "entries": [self.entry_for(b) for b in bills],
        }
        batch["signature"] = crypto.sign(
            {k: v for k, v in batch.items() if k != "signature"}, self._private_key
        )
        published = self.store.load_file(self.issuer_id)
        published["batches"].append(batch)
        self.store.file_path(self.issuer_id).write_text(json.dumps(published, indent=2))
        # Publishing extends the undertaking: everything issued up to this date is now
        # in the file. A verifier can rely on a missing ticket only up to here.
        directory = self.store.load_directory()
        entry = directory.get(self.issuer_id)
        if entry is not None:
            day = when.date().isoformat()
            if day > entry.get("covers_to", ""):
                entry["covers_to"] = day
                self.store._save_directory(directory)
        return batch


def _within_coverage(bill_date: str, entry: dict) -> bool:
    """Is this bill inside the period the issuer has publicly undertaken to cover?

    Only inside that window does a missing ticket mean anything. Outside it, the
    issuer simply has not spoken, and the rule must not treat silence as evidence:
    that is how a genuine bill issued before a hospital joined, or in the gap before
    its next publication, would be called a forgery.
    """
    start, end = entry.get("covers_from"), entry.get("covers_to")
    if not (bill_date and start and end):
        return False
    return start <= bill_date[:10] <= end


class Verifier:
    """The insurer-side lookup. Works on a local mirror of the published files."""

    def __init__(self, store: RegistryStore):
        self.store = store
        self.directory = store.load_directory()
        # issuer_id -> ({key: value}, note about the file's integrity)
        self._mirror: dict[str, tuple[dict, str]] = {}

    def refresh(self) -> None:
        """Re-read the directory and drop cached mirrors (new bills were published)."""
        self.directory = self.store.load_directory()
        self._mirror.clear()

    def _mirror_for(self, issuer_id: str) -> tuple[dict, str]:
        """Load an issuer's file, keep only batches whose signature checks out."""
        if issuer_id in self._mirror:
            return self._mirror[issuer_id]
        public_key = self.directory[issuer_id]["public_key"]
        table: dict[str, str] = {}
        rejected = 0
        for batch in self.store.load_file(issuer_id)["batches"]:
            unsigned = {k: v for k, v in batch.items() if k != "signature"}
            if not crypto.verify_signature(unsigned, batch.get("signature", ""), public_key):
                # A batch that fails its signature was not published by the
                # hospital (or was altered afterwards). It is ignored entirely.
                rejected += 1
                continue
            for key, value in batch["entries"]:
                table[key] = value      # later entries supersede earlier ones
        note = "" if rejected == 0 else (
            f"{rejected} batch(es) in the issuer's file failed the signature check and were ignored."
        )
        self._mirror[issuer_id] = (table, note)
        return self._mirror[issuer_id]

    def lookup(self, bill) -> tuple[RegistryAnswer, str]:
        """Answer: is this document, as read, exactly what the issuer issued?

        `bill` is a BillFields or a DischargeFields: the mechanism is the same, two
        codes per document, and only the wording of the note changes. Returns the
        registry answer and a one-sentence note explaining it.
        """
        from .models import kind_of
        word = kind_of(bill)
        entry = self.directory.get(bill.issuer_id)
        if entry is None:
            return (RegistryAnswer.NOT_COVERED,
                    f"{bill.issuer_name or 'The issuer'} has not joined the registry, "
                    "so there is nothing to check against.")
        covered = _within_coverage(bill.bill_date, entry)
        window = f"{entry.get('covers_from', '?')} to {entry.get('covers_to', '?')}"

        if not bill.ticket:
            # A joined issuer prints a ticket on every bill it issues. Inside the period
            # it has published for, a bill with no ticket cannot be one of theirs.
            if covered:
                return (RegistryAnswer.NOT_ISSUED,
                        f"{entry['name']} prints a ticket on every {word} it issues, and has published "
                        f"every one from {window}. This {word} carries no ticket, so it is not one of "
                        "theirs.")
            return (RegistryAnswer.NO_RECORD,
                    f"{entry['name']} prints a ticket on every {word}, but this one carries none, and "
                    f"its date falls outside what they have published ({window}).")

        table, integrity_note = self._mirror_for(bill.issuer_id)
        salt = entry["salt"]
        key = crypto.code(canonical_ticket(bill.ticket), salt)
        if key not in table:
            if covered:
                # The issuer has published everything it issued for this period and this
                # ticket is not among it. That is the issuer saying so, not an absence.
                note = (f"{entry['name']} has published a code for every {word} it issued from "
                        f"{window}, and ticket {bill.ticket} is not among them. The hospital did "
                        f"not issue this {word}.")
                return RegistryAnswer.NOT_ISSUED, (note + " " + integrity_note).strip()
            note = (f"{entry['name']} has no record of ticket {bill.ticket}, but this {word} is dated "
                    f"outside the period they have published ({window}), so nothing follows from that.")
            return RegistryAnswer.NO_RECORD, (note + " " + integrity_note).strip()
        value = crypto.code(canonical_record(bill), salt)
        if value == table[key]:
            note = f"{entry['name']} confirms this ticket and every field on the {word}."
            return RegistryAnswer.VERIFIED, (note + " " + integrity_note).strip()
        note = (f"{entry['name']} issued ticket {bill.ticket}, but the details on this copy of the "
                f"{word} differ from what was issued.")
        return RegistryAnswer.MISMATCH, (note + " " + integrity_note).strip()

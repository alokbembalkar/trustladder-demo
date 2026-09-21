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

    def enrol_issuer(self, issuer_id: str, name: str, city: str, ticket_prefix: str) -> None:
        """Sign an issuer up: create its key pair and salt, list it publicly.

        This is the whole onboarding step for a hospital: no integration project
        and no data sharing, just a key pair and a line in the directory.
        """
        directory = self.load_directory()
        private_b64, public_b64 = crypto.new_signing_key()
        directory[issuer_id] = {
            "name": name,
            "city": city,
            "salt": crypto.new_salt(),
            "public_key": public_b64,
            "ticket_prefix": ticket_prefix,
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

    def new_ticket(self) -> str:
        """The random reference that gets printed on the bill."""
        return crypto.new_ticket(self.ticket_prefix)

    def entry_for(self, bill: BillFields) -> list[str]:
        """Return the [key, value] pair for one bill: two codes, nothing else."""
        key = crypto.code(canonical_ticket(bill.ticket), self.salt)
        value = crypto.code(canonical_record(bill), self.salt)
        return [key, value]

    def publish(self, bills: list[BillFields], when: _dt.datetime | None = None) -> dict:
        """Append one signed batch covering `bills` to the issuer's file.

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
        return batch


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

    def lookup(self, bill: BillFields) -> tuple[RegistryAnswer, str]:
        """Answer: is this bill, as read, exactly what the issuer issued?

        Returns the registry answer and a one-sentence note explaining it.
        """
        entry = self.directory.get(bill.issuer_id)
        if entry is None:
            return (RegistryAnswer.NOT_COVERED,
                    f"{bill.issuer_name or 'The issuer'} has not joined the registry, "
                    "so there is nothing to check against.")
        if not bill.ticket:
            # A joined hospital always prints a ticket; a missing one is itself
            # a contradiction of how that issuer's bills look.
            return (RegistryAnswer.NO_RECORD,
                    f"{entry['name']} prints a ticket on every bill, but this bill carries none.")

        table, integrity_note = self._mirror_for(bill.issuer_id)
        salt = entry["salt"]
        key = crypto.code(canonical_ticket(bill.ticket), salt)
        if key not in table:
            note = f"{entry['name']} has never issued ticket {bill.ticket}."
            return RegistryAnswer.NO_RECORD, (note + " " + integrity_note).strip()
        value = crypto.code(canonical_record(bill), salt)
        if value == table[key]:
            note = f"{entry['name']} confirms this ticket and every field on the bill."
            return RegistryAnswer.VERIFIED, (note + " " + integrity_note).strip()
        note = (f"{entry['name']} issued ticket {bill.ticket}, but the details on this "
                "copy differ from what was issued.")
        return RegistryAnswer.MISMATCH, (note + " " + integrity_note).strip()

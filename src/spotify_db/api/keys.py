"""Lokale CLI zur Verwaltung der API-Keys — bewusst KEIN HTTP-Endpunkt.

Key-Erstellung und -Widerruf passieren nur auf dem Gerät selbst:

  python -m spotify_db.api.keys create --name handy
  python -m spotify_db.api.keys list
  python -m spotify_db.api.keys revoke --id 3
  python -m spotify_db.api.keys revoke --name handy

Der Klartext-Key wird genau einmal beim Erstellen angezeigt; in der Datenbank
liegt nur sein sha256-Hash (siehe `spotify_db.db.keys`).
"""

import argparse

from spotify_db.db import keys
from spotify_db.db.localdb import initialize_local_db

# SECTION: - Unterbefehle -


# DEF: create
def _cmd_create(args) -> None:
    """Erzeugt einen Key und zeigt den Klartext genau einmal an."""
    plaintext = keys.create_key(args.name)
    print(f"Key '{args.name}' angelegt:\n\n  {plaintext}\n")
    print("Dieser Key wird nur einmal angezeigt — jetzt im Client hinterlegen.")


# DEF: list
def _cmd_list(_args) -> None:
    """Listet alle Keys mit Status auf."""
    entries = keys.list_keys()
    if not entries:
        print("Keine Keys angelegt. Tipp: python -m spotify_db.api.keys create --name handy")
        return
    print(f"{'ID':>3}  {'Name':<16} {'Angelegt':<20} {'Status':<10} Hash")
    for e in entries:
        status = "widerrufen" if e["revoked"] else "aktiv"
        print(
            f"{e['id']:>3}  {e['name']:<16} {e['created_at']:<20} {status:<10} {e['hash_prefix']}…"
        )


# DEF: revoke
def _cmd_revoke(args) -> None:
    """Widerruft Keys per ID oder Name."""
    count = keys.revoke_key(key_id=args.id, name=args.name)
    if count:
        print(f"{count} Key(s) widerrufen — greift sofort, kein Neustart nötig.")
    else:
        print("Kein passender aktiver Key gefunden.")


# SECTION: - Einstiegspunkt -


def main() -> None:
    """Parst die Unterbefehle (create/list/revoke) und führt sie aus."""
    parser = argparse.ArgumentParser(
        prog="python -m spotify_db.api.keys",
        description="API-Keys verwalten (lokal, kein HTTP-Endpunkt).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Neuen Key erzeugen (Klartext wird einmal angezeigt)")
    p_create.add_argument("--name", required=True, help='Name des Aufrufers, z.B. "handy"')
    p_create.set_defaults(func=_cmd_create)

    p_list = sub.add_parser("list", help="Alle Keys auflisten")
    p_list.set_defaults(func=_cmd_list)

    p_revoke = sub.add_parser("revoke", help="Key widerrufen (per --id oder --name)")
    group = p_revoke.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", type=int, help="ID des Keys (siehe list)")
    group.add_argument("--name", help="Name des Keys (widerruft alle aktiven mit diesem Namen)")
    p_revoke.set_defaults(func=_cmd_revoke)

    args = parser.parse_args()
    initialize_local_db()  # Schema sicherstellen, falls die CLI vor dem ersten API-Lauf benutzt wird
    args.func(args)


if __name__ == "__main__":
    main()

"""Verwaltung der API-Keys in der Tabelle `api_keys`.

Gespeichert wird ausschließlich der sha256-Hash eines Keys — der Klartext existiert
nur im Moment der Erzeugung (`create_key`) und wird genau einmal an den Aufrufer
zurückgegeben. Ungesalzenes sha256 ist hier bewusst gewählt: die Keys sind zufällige
Hochentropie-Token (kein Passwort), Brute-Force/Rainbow-Tables laufen ins Leere und
der indizierte Gleichheits-Lookup ist timing-unkritisch.

Zurückziehen ist ein Soft-Delete (revoked-Flag), damit der Audit-Trail erhalten bleibt.
"""

import hashlib
import secrets

from spotify_db.db.localdb import local_transaction, read_local_lock

# SECTION: - Hilfsfunktionen -


def _hash_key(plaintext: str) -> str:
    """Gibt den sha256-Hex-Hash eines Klartext-Keys zurück."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


# SECTION: - Key-Lebenszyklus -


# DEF: Neuen Key erzeugen
def create_key(name: str) -> str:
    """Erzeugt einen neuen API-Key und speichert seinen Hash.

    Args:
        name: Sprechender Name des Aufrufers (z.B. "handy", "widget").

    Returns:
        str: Der Klartext-Key — wird nur dieses eine Mal herausgegeben.
    """
    plaintext = secrets.token_urlsafe(32)  # 256 Bit Entropie
    with local_transaction() as conn:
        conn.execute(
            "INSERT INTO api_keys (key_hash, name) VALUES (?, ?)",
            (_hash_key(plaintext), name),
        )
    return plaintext


# DEF: Key prüfen (Hot-Path der Auth)
def verify_key(presented: str) -> bool:
    """Prüft, ob ein präsentierter Klartext-Key gültig (vorhanden + nicht widerrufen) ist.

    Args:
        presented: Der vom Client mitgeschickte Klartext-Key.

    Returns:
        bool: True wenn der Key aktiv ist.
    """
    if not presented:
        return False
    with read_local_lock() as conn:
        row = conn.execute(
            "SELECT 1 FROM api_keys WHERE key_hash = ? AND revoked = 0",
            (_hash_key(presented),),
        ).fetchone()
    return row is not None


# DEF: Gibt es überhaupt aktive Keys?
def has_active_keys() -> bool:
    """Prüft, ob mindestens ein nicht-widerrufener Key existiert (Fail-closed-Check)."""
    with read_local_lock() as conn:
        row = conn.execute("SELECT EXISTS(SELECT 1 FROM api_keys WHERE revoked = 0)").fetchone()
    return bool(row[0])


# DEF: Alle Keys auflisten
def list_keys() -> list[dict]:
    """Listet alle Keys mit Metadaten auf (Hash nur als 8-Zeichen-Präfix zur Anzeige).

    Returns:
        list[dict]: Einträge mit id, name, created_at, revoked, hash_prefix.
    """
    with read_local_lock() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, revoked, key_hash FROM api_keys ORDER BY id"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "revoked": bool(r["revoked"]),
            "hash_prefix": r["key_hash"][:8],
        }
        for r in rows
    ]


# DEF: Key zurückziehen (Soft-Delete)
def revoke_key(key_id: int | None = None, name: str | None = None) -> int:
    """Widerruft Keys per ID oder Name (Soft-Delete, greift sofort).

    Args:
        key_id: ID des Keys (hat Vorrang, eindeutig).
        name: Name des Keys — widerruft ALLE aktiven Keys mit diesem Namen.

    Returns:
        int: Anzahl der widerrufenen Keys.

    Raises:
        ValueError: Wenn weder key_id noch name angegeben ist.
    """
    if key_id is None and name is None:
        raise ValueError("Entweder key_id oder name angeben.")
    with local_transaction() as conn:
        if key_id is not None:
            cur = conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE id = ? AND revoked = 0", (key_id,)
            )
        else:
            cur = conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE name = ? AND revoked = 0", (name,)
            )
    return cur.rowcount

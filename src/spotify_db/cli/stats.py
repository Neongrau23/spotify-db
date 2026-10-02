"""Auswertungs-Befehle der CLI: `stats`, `top` und `history`.

Liest `spotify.db` direkt (WAL erlaubt das parallel zum laufenden Tracker) — die API muss
dafür nicht laufen, ein API-Key ist nicht nötig. Ohne Zeitraum rufen die Befehle dieselben
Queries wie die API-Endpunkte (`/stats`, `/top10/…`, `/top/…`, `/history`), damit CLI und
API dieselben Zahlen zeigen. Mit Zeitraum werten sie `history` aus (siehe
`spotify_db.db.queries`, Abschnitt Zeitraum-Queries).

  spotify-db stats [--period today|week|month|year|all] [--from DATUM] [--to DATUM] [--json]
  spotify-db top [tracks|plays|artists|genres] [-n 10] [Zeitraum] [--json]
  spotify-db history [-n 20] [Zeitraum] [--json]

Zeiträume gelten in lokaler Zeit (Zeitzone des Systems); die DB speichert UTC, daher werden
die Grenzen vor der Abfrage umgerechnet.
"""

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from spotify_db.common.config import get_db_path
from spotify_db.db import queries

# SECTION: - Konstanten -

# CONFIG: Zeitstempel-Format der DB (UTC), siehe database._now()
_DB_FORMAT = "%Y-%m-%d %H:%M:%S"

# CONFIG: Benannte Zeiträume (Kalender-Zeiträume, die Woche beginnt am Montag)
_PERIOD_LABELS = {
    "today": "Heute",
    "week": "Diese Woche",
    "month": "Dieser Monat",
    "year": "Dieses Jahr",
    "all": "Gesamt",
}

# CONFIG: Kategorien von `top` mit Überschrift
_TOP_TITLES = {
    "tracks": "Tracks nach Hörzeit",
    "plays": "Tracks nach Wiedergaben",
    "artists": "Artists nach Hörzeit",
    "genres": "Genres nach Hörzeit",
}


# SECTION: - Zeitraum -


# DEF: Aufgelöster Zeitraum
@dataclass(frozen=True)
class Period:
    """Ein Zeitraum mit Anzeigename und UTC-Grenzen für die DB-Abfrage.

    Attributes:
        label: Anzeigename inkl. Datumsbereich, z.B. "Heute (02.10.2026)".
        start: Untergrenze (inklusiv) als UTC-Zeitstempel im DB-Format, None = offen.
        end: Obergrenze (exklusiv) als UTC-Zeitstempel im DB-Format, None = offen.
    """

    label: str
    start: str | None = None
    end: str | None = None

    @property
    def is_all_time(self) -> bool:
        """True ohne jede Grenze — dann greifen die Gesamt-Queries der API."""
        return self.start is None and self.end is None


# DEF: Lokale Mitternacht als UTC-Zeitstempel
def _local_midnight_utc(day: date) -> str:
    """Wandelt 00:00 Uhr lokaler Zeit eines Tages in einen UTC-Zeitstempel im DB-Format um.

    Ein naives `datetime` interpretiert `astimezone()` als Systemzeit — so landet z.B.
    eine Session um 23:30 UTC im lokalen Folgetag, wenn sie dort nach Mitternacht lag.
    """
    return datetime.combine(day, time.min).astimezone(UTC).strftime(_DB_FORMAT)


# DEF: Datum anzeigen
def _format_day(day: date) -> str:
    """Formatiert ein Datum als 'DD.MM.YYYY'."""
    return day.strftime("%d.%m.%Y")


# DEF: Zeitraum auflösen
def resolve_period(name=None, from_date=None, to_date=None, today=None) -> Period:
    """Löst einen benannten Zeitraum oder --from/--to in UTC-Grenzen auf.

    Args:
        name: "today", "week", "month", "year", "all" oder None (= gesamt).
        from_date: Erster Tag (inklusiv, lokale Zeit); hat Vorrang vor `name`.
        to_date: Letzter Tag (inklusiv, lokale Zeit); hat Vorrang vor `name`.
        today: Bezugstag (Default: heute) — nur zum Nachrechnen gedacht.

    Returns:
        Period: Anzeigename und halboffene UTC-Grenzen [start, end).
    """
    if from_date or to_date:
        start = _local_midnight_utc(from_date) if from_date else None
        # +1 Tag, damit der gesamte to_date-Tag inklusive ist
        end = _local_midnight_utc(to_date + timedelta(days=1)) if to_date else None
        if from_date and to_date:
            label = f"{_format_day(from_date)} - {_format_day(to_date)}"
        elif from_date:
            label = f"ab {_format_day(from_date)}"
        else:
            label = f"bis {_format_day(to_date)}"
        return Period(label, start, end)

    if name in (None, "all"):
        return Period(_PERIOD_LABELS["all"])

    today = today or date.today()
    if name == "today":
        first = last = today
    elif name == "week":
        first = today - timedelta(days=today.weekday())
        last = first + timedelta(days=6)
    elif name == "month":
        first = today.replace(day=1)
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    elif name == "year":
        first, last = date(today.year, 1, 1), date(today.year, 12, 31)
    else:
        raise ValueError(f"Unbekannter Zeitraum: {name}")

    days = _format_day(first) if first == last else f"{_format_day(first)} - {_format_day(last)}"
    return Period(
        f"{_PERIOD_LABELS[name]} ({days})",
        _local_midnight_utc(first),
        _local_midnight_utc(last + timedelta(days=1)),
    )


# SECTION: - Ausgabe-Helfer -


# DEF: UTC-Zeitstempel lokal anzeigen
def _format_local(raw) -> str:
    """Wandelt einen UTC-Zeitstempel der DB in lokale Zeit 'DD.MM.YYYY HH:MM' um."""
    if not raw:
        return ""
    try:
        utc = datetime.strptime(str(raw)[:19], _DB_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return str(raw)
    return utc.astimezone().strftime("%d.%m.%Y %H:%M")


# DEF: Ganzzahl mit Tausenderpunkt
def _format_int(value) -> str:
    """Formatiert eine Ganzzahl deutsch mit Tausenderpunkt (12.345)."""
    return f"{int(value or 0):,}".replace(",", ".")


# DEF: Track als "Name — Artists"
def _track_label(item) -> str:
    """Baut die Anzeige "Trackname — Artists" aus einer Track- oder History-Zeile."""
    name = item.get("track_name") or "?"
    artists = item.get("artists")
    return f"{name} — {artists}" if artists else name


# DEF: Text auf Breite kürzen
def _truncate(text: str, width: int) -> str:
    """Kürzt `text` mit "…" auf höchstens `width` Zeichen."""
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


# DEF: Tabelle ausgeben
def _print_table(headers: list[str], rows: list[list[str]], align: str) -> None:
    """Gibt eine Tabelle mit zwei Leerzeichen Einzug aus.

    Die letzte Spalte (Track/Artist/Genre) wird im Terminal auf die Fensterbreite gekürzt,
    damit lange Namen auf schmalen Displays (Termux) keine Zeilenumbrüche erzeugen. Beim
    Umleiten in Datei/Pipe bleibt sie ungekürzt.

    Args:
        headers: Spaltenüberschriften.
        rows: Zeilen als Strings, je so lang wie `headers`.
        align: Ausrichtung je Spalte außer der letzten, "r" = rechts, "l" = links.
    """
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(align))]
    last_width = None
    if sys.stdout.isatty():
        used = 2 + sum(w + 2 for w in widths)
        last_width = max(shutil.get_terminal_size().columns - used, 20)

    def line(cells: list[str]) -> str:
        fixed = "  ".join(
            cell.rjust(w) if a == "r" else cell.ljust(w)
            for cell, w, a in zip(cells, widths, align, strict=False)
        )
        last = cells[-1] if last_width is None else _truncate(cells[-1], last_width)
        return f"  {fixed}  {last}"

    print(line(headers))
    for row in rows:
        print(line(row))


# DEF: JSON ausgeben
def _print_json(data) -> None:
    """Gibt Daten als JSON aus (Umlaute unescaped, für jq & Co.)."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


# DEF: Leere Auswertung melden
def _print_empty(period: Period) -> None:
    """Meldet, dass es (im Zeitraum) nichts auszuwerten gibt."""
    print("  Noch keine Daten." if period.is_all_time else "  Keine Wiedergaben im Zeitraum.")


# CONFIG: Felder der Statistik (Schlüssel, Label, Formatierung). Fehlende Schlüssel werden
# übersprungen — so deckt eine Tabelle die Gesamt-Shape (/stats) und die Zeitraum-Shape ab.
_STATS_FIELDS = (
    ("total_listen_time", "Hörzeit", str),
    ("total_plays", "Wiedergaben", _format_int),
    ("total_tracks", "Tracks", _format_int),
    ("unique_tracks", "Tracks", _format_int),
    ("new_tracks", "davon neu entdeckt", _format_int),
    ("unique_artists", "Artists", _format_int),
    ("unique_albums", "Alben", _format_int),
    ("unique_genres", "Genres", _format_int),
    ("avg_listen_ms_per_track", "Ø Hörzeit pro Track", queries.format_hh_mm_ss),
    ("first_seen", "Erstes Hördatum", str),
    ("last_seen", "Zuletzt gehört", str),
    ("first_played_at", "Erste Wiedergabe", _format_local),
    ("last_played_at", "Letzte Wiedergabe", _format_local),
)


# SECTION: - Befehle -


# DEF: stats
def _cmd_stats(args, period: Period) -> None:
    """Zeigt die Gesamt- oder Zeitraum-Statistik."""
    if period.is_all_time:
        data = queries.get_db_stats()
    else:
        data = queries.get_period_stats(period.start, period.end)
    if args.json:
        _print_json(data)
        return

    print(f"Statistik · {period.label}\n")
    if not data["total_plays"]:
        _print_empty(period)
        return
    fields = [(label, fmt(data[key])) for key, label, fmt in _STATS_FIELDS if key in data]
    width = max(len(label) for label, _ in fields)
    for label, value in fields:
        print(f"  {label:<{width}}  {value}")


# DEF: top-Daten laden
def _load_top(category: str, n: int, period: Period) -> list[dict]:
    """Holt die Top-Liste — gesamt über die API-Queries, sonst aus der History."""
    if period.is_all_time:
        loaders = {
            "tracks": queries.top_tracks_by_listen,
            "plays": queries.top_tracks_by_plays,
            "artists": queries.top_artists_by_listen,
            "genres": queries.top_genres_by_listen,
        }
        return loaders[category](n)

    start, end = period.start, period.end
    if category in ("tracks", "plays"):
        by = "plays" if category == "plays" else "listen"
        return queries.top_tracks_in_period(start, end, n, by=by)
    if category == "artists":
        return queries.top_artists_in_period(start, end, n)
    return queries.top_genres_in_period(start, end, n)


# DEF: top
def _cmd_top(args, period: Period) -> None:
    """Zeigt die Top-N-Tracks, -Artists oder -Genres."""
    category = args.category
    data = _load_top(category, args.limit, period)
    if args.json:
        _print_json(data)
        return

    print(f"Top {args.limit} {_TOP_TITLES[category]} · {period.label}\n")
    if not data:
        _print_empty(period)
        return

    rows = []
    for rank, item in enumerate(data, 1):
        listen = queries.format_hh_mm_ss(item.get("total_listen_ms") or 0)
        if category == "plays":
            rows.append([str(rank), _format_int(item["play_count"]), listen, _track_label(item)])
        elif category == "tracks":
            rows.append([str(rank), listen, _track_label(item)])
        else:
            rows.append([str(rank), listen, item["artist" if category == "artists" else "genre"]])

    if category == "plays":
        _print_table(["#", "Wiedergaben", "Hörzeit", "Track"], rows, align="rrr")
    else:
        name = {"tracks": "Track", "artists": "Artist", "genres": "Genre"}[category]
        _print_table(["#", "Hörzeit", name], rows, align="rr")


# DEF: history
def _cmd_history(args, period: Period) -> None:
    """Zeigt die letzten Wiedergaben (gesamt oder im Zeitraum)."""
    if period.is_all_time:
        data, total = queries.list_history(args.limit, 0)
    else:
        data, total = queries.list_history_in_period(period.start, period.end, args.limit)
    if args.json:
        # Shape wie GET /history
        _print_json({"data": data, "count": len(data), "total": total, "offset": 0})
        return

    count = f"{len(data)} von {_format_int(total)}" if len(data) < total else _format_int(total)
    print(f"Verlauf · {period.label} — {count} Wiedergaben\n")
    if not data:
        _print_empty(period)
        return

    # Älteste oben, neueste unten: im Terminal steht so der jüngste Eintrag direkt über
    # dem Prompt, ohne Scrollen (die JSON-Ausgabe behält die API-Reihenfolge).
    rows = [
        [
            _format_local(item["played_at"]),
            queries.format_hh_mm_ss(item["total_listen_ms"] or 0),
            _track_label(item),
        ]
        for item in reversed(data)
    ]
    _print_table(["Zeitpunkt", "Hörzeit", "Track"], rows, align="lr")


# SECTION: - Argumente & Einstieg -


# DEF: Datum parsen (argparse-Typ)
def _parse_date(value: str) -> date:
    """Parst ein Datum als JJJJ-MM-TT oder TT.MM.JJJJ (argparse-Typ)."""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"ungültiges Datum '{value}' (erwartet JJJJ-MM-TT oder TT.MM.JJJJ)"
    )


# DEF: Positive Ganzzahl parsen (argparse-Typ)
def _positive_int(value: str) -> int:
    """Parst eine Ganzzahl ≥ 1 (argparse-Typ)."""
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"erwartet eine Zahl ≥ 1, nicht '{value}'")
    return number


# DEF: Gemeinsame Optionen (Zeitraum + JSON)
def _common_options() -> argparse.ArgumentParser:
    """Baut den Eltern-Parser mit den Zeitraum-Optionen und --json für alle Befehle."""
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_argument_group("Zeitraum (lokale Zeit, Default: gesamt)")
    group.add_argument(
        "--period",
        choices=tuple(_PERIOD_LABELS),
        help="heute, diese Woche (ab Montag), dieser Monat, dieses Jahr oder gesamt",
    )
    group.add_argument(
        "--from",
        dest="from_date",
        type=_parse_date,
        metavar="DATUM",
        help="ab diesem Tag (inklusiv), JJJJ-MM-TT oder TT.MM.JJJJ",
    )
    group.add_argument(
        "--to",
        dest="to_date",
        type=_parse_date,
        metavar="DATUM",
        help="bis zu diesem Tag (inklusiv), JJJJ-MM-TT oder TT.MM.JJJJ",
    )
    parent.add_argument("--json", action="store_true", help="Rohdaten als JSON ausgeben")
    return parent


# DEF: Unterbefehle registrieren
def add_subcommands(subparsers) -> None:
    """Registriert `stats`, `top` und `history` am Subparser-Objekt von `main.py`.

    Args:
        subparsers: Rückgabe von `ArgumentParser.add_subparsers()`.
    """
    common = _common_options()

    p_stats = subparsers.add_parser(
        "stats", parents=[common], help="Statistik: Hörzeit, Wiedergaben, Tracks, Artists …"
    )
    p_stats.set_defaults(handler=_cmd_stats, cli_parser=p_stats)

    p_top = subparsers.add_parser(
        "top", parents=[common], help="Top-Listen nach Hörzeit oder Wiedergaben"
    )
    p_top.add_argument(
        "category",
        nargs="?",
        default="tracks",
        choices=tuple(_TOP_TITLES),
        help="tracks/artists/genres nach Hörzeit, plays = Tracks nach Wiedergaben (Default: tracks)",
    )
    p_top.add_argument(
        "-n", "--limit", type=_positive_int, default=10, help="Anzahl Einträge (Default: 10)"
    )
    p_top.set_defaults(handler=_cmd_top, cli_parser=p_top)

    p_history = subparsers.add_parser("history", parents=[common], help="Letzte Wiedergaben")
    p_history.add_argument(
        "-n", "--limit", type=_positive_int, default=20, help="Anzahl Einträge (Default: 20)"
    )
    p_history.set_defaults(handler=_cmd_history, cli_parser=p_history)


# DEF: Befehl ausführen
def run(args) -> None:
    """Führt den gewählten Auswertungs-Befehl aus.

    Prüft vorher, ob die DB existiert: `get_connection()` würde sonst eine leere Datei
    ohne Tabellen anlegen und der Befehl mit "no such table" scheitern.

    Args:
        args: Von `main.py` geparste Argumente (mit `handler` und `cli_parser`).
    """
    if args.period and (args.from_date or args.to_date):
        args.cli_parser.error("--period und --from/--to schließen sich aus")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        args.cli_parser.error("--from liegt nach --to")
    period = resolve_period(args.period, args.from_date, args.to_date)

    db_path = get_db_path()
    if db_path is None or not db_path.exists():
        print(
            f"Keine Datenbank gefunden ({db_path}) — sie entsteht beim ersten Tracker-Start.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        args.handler(args, period)
    except sqlite3.Error as e:
        print(f"Fehler beim Lesen der Datenbank: {e}", file=sys.stderr)
        raise SystemExit(1) from e

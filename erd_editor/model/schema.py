"""Neutrales, dialektunabhängiges Datenmodell eines Datenbankschemas.

Das Modell bildet ab, was ein Adapter aus der Datenbank liest – nicht mehr.
Abgeleitete Informationen (identifizierend, Kardinalitäten) berechnet
`erd_editor.analysis`, Darstellungsdaten (Positionen, Farben) liegen im Layout.

Keine Qt-Abhängigkeit, damit alles ohne Oberfläche und ohne Datenbank testbar ist.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum


class Dialect(str, Enum):
    """Unterstützte SQL-Dialekte."""

    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    MSSQL = "mssql"


class IndexKind(str, Enum):
    """Art eines Index, so wie die Datenbank ihn meldet."""

    PRIMARY = "PRIMARY"
    UNIQUE = "UNIQUE"
    INDEX = "INDEX"
    FULLTEXT = "FULLTEXT"
    SPATIAL = "SPATIAL"
    OTHER = "OTHER"


@dataclass(frozen=True, order=True)
class TableRef:
    """Eindeutiger, stabiler Verweis auf eine Tabelle im Diagramm.

    Dient als Schlüssel für Fremdschlüsselziele und für das Layout
    (Positionen überleben so ein erneutes Einlesen).
    `schema` ist bei MySQL `None`, weil dort Datenbank = Schema gilt.
    """

    database: str
    schema: str | None
    table: str

    @property
    def key(self) -> str:
        """Lesbarer Schlüssel, z. B. `shop.dbo.kunde` oder `shop.kunde`."""
        parts = [self.database, self.schema, self.table]
        return ".".join(p for p in parts if p is not None)

    def __str__(self) -> str:
        return self.key


@dataclass
class Column:
    """Spalte einer Tabelle mit nativem Datentyp."""

    name: str
    data_type: str
    nullable: bool = True
    default: str | None = None
    comment: str | None = None
    auto_increment: bool = False


@dataclass
class Index:
    """Index einer Tabelle; `columns` in Indexreihenfolge."""

    name: str
    kind: IndexKind
    columns: tuple[str, ...]

    @property
    def is_unique(self) -> bool:
        return self.kind in (IndexKind.PRIMARY, IndexKind.UNIQUE)


@dataclass
class ForeignKey:
    """Fremdschlüssel-Constraint, wie er in der Datenbank definiert ist.

    `columns[i]` verweist auf `referenced_columns[i]`.
    """

    name: str
    columns: tuple[str, ...]
    referenced_table: TableRef
    referenced_columns: tuple[str, ...]
    on_update: str | None = None
    on_delete: str | None = None

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError(f"Fremdschlüssel {self.name!r} hat keine Spalten")
        if len(self.columns) != len(self.referenced_columns):
            raise ValueError(
                f"Fremdschlüssel {self.name!r}: {len(self.columns)} Spalten, "
                f"aber {len(self.referenced_columns)} referenzierte Spalten"
            )


@dataclass
class Table:
    """Tabelle mit Spalten, Indexen und ausgehenden Fremdschlüsseln."""

    name: str
    columns: list[Column] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    comment: str | None = None

    def column(self, name: str) -> Column | None:
        """Spalte über ihren Namen suchen."""
        return next((c for c in self.columns if c.name == name), None)

    @property
    def primary_key(self) -> Index | None:
        return next((i for i in self.indexes if i.kind is IndexKind.PRIMARY), None)

    @property
    def primary_key_columns(self) -> tuple[str, ...]:
        pk = self.primary_key
        return pk.columns if pk else ()

    def is_primary_key_column(self, name: str) -> bool:
        return name in self.primary_key_columns

    def is_foreign_key_column(self, name: str) -> bool:
        return any(name in fk.columns for fk in self.foreign_keys)

    def is_indexed_column(self, name: str) -> bool:
        """True, wenn die Spalte in irgendeinem Index vorkommt (inkl. PK)."""
        return any(name in i.columns for i in self.indexes)

    def has_unique_index_on(self, columns: tuple[str, ...]) -> bool:
        """True, wenn ein PK/UNIQUE-Index genau diese Spaltenmenge abdeckt."""
        wanted = set(columns)
        return any(i.is_unique and set(i.columns) == wanted for i in self.indexes)


@dataclass
class Schema:
    """Schema innerhalb einer Datenbank (`public`, `dbo`, …).

    Bei MySQL gibt es genau ein Schema mit `name=None`.
    """

    name: str | None
    tables: list[Table] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)


@dataclass
class ConnectionProfile:
    """Verbindungsdaten ohne Passwort – das liegt im `keyring`."""

    name: str
    dialect: Dialect
    host: str
    port: int | None = None
    user: str | None = None
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class Database:
    """Eine eingelesene Datenbank, im Diagramm als Behälter dargestellt."""

    name: str
    dialect: Dialect
    schemas: list[Schema] = field(default_factory=list)
    connection_profile: str | None = None
    server_version: str | None = None
    charset: str | None = None
    collation: str | None = None

    def schema(self, name: str | None) -> Schema | None:
        return next((s for s in self.schemas if s.name == name), None)


@dataclass
class Diagram:
    """Wurzel des Modells: eine oder mehrere Datenbanken."""

    databases: list[Database] = field(default_factory=list)

    def database(self, name: str) -> Database | None:
        return next((d for d in self.databases if d.name == name), None)

    def tables(self) -> Iterator[tuple[TableRef, Table]]:
        """Alle Tabellen mit ihrem stabilen Verweis."""
        for db in self.databases:
            for schema in db.schemas:
                for table in schema.tables:
                    yield TableRef(db.name, schema.name, table.name), table

    def find_table(self, ref: TableRef) -> Table | None:
        db = self.database(ref.database)
        schema = db.schema(ref.schema) if db else None
        return schema.table(ref.table) if schema else None

    def validate(self) -> list[str]:
        """Prüft die innere Konsistenz und liefert eine Liste von Problemen.

        Eine leere Liste bedeutet: alle Verweise sind auflösbar.
        """
        problems: list[str] = []
        seen: set[TableRef] = set()
        for ref, table in self.tables():
            if ref in seen:
                problems.append(f"{ref}: Tabelle mehrfach vorhanden")
            seen.add(ref)

            names = [c.name for c in table.columns]
            if len(names) != len(set(names)):
                problems.append(f"{ref}: doppelte Spaltennamen")
            known = set(names)

            for index in table.indexes:
                for col in index.columns:
                    if col not in known:
                        problems.append(f"{ref}: Index {index.name!r} nennt unbekannte Spalte {col!r}")
            if sum(1 for i in table.indexes if i.kind is IndexKind.PRIMARY) > 1:
                problems.append(f"{ref}: mehr als ein Primärschlüssel")

            for fk in table.foreign_keys:
                for col in fk.columns:
                    if col not in known:
                        problems.append(f"{ref}: FK {fk.name!r} nennt unbekannte Spalte {col!r}")
                target = self.find_table(fk.referenced_table)
                if target is None:
                    problems.append(f"{ref}: FK {fk.name!r} verweist auf fehlende Tabelle {fk.referenced_table}")
                    continue
                for col in fk.referenced_columns:
                    if target.column(col) is None:
                        problems.append(
                            f"{ref}: FK {fk.name!r} verweist auf unbekannte Spalte "
                            f"{fk.referenced_table}.{col}"
                        )
        return problems

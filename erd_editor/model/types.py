"""Gängige Datentypen je Dialekt – Vorschläge für den Tabelleneditor.

Die Liste ist eine Auswahlhilfe; im Editor kann jeder native Typ frei
eingegeben werden (kein Typ-Mapping zwischen Dialekten).
"""

from __future__ import annotations

from erd_editor.model.schema import Dialect

_MYSQL_TYPES = [
    "int", "bigint", "smallint", "tinyint", "decimal(10,2)", "double",
    "varchar(255)", "char(10)", "text", "date", "datetime", "timestamp", "time",
    "tinyint(1)", "json", "blob",
]

COMMON_TYPES: dict[Dialect, list[str]] = {
    Dialect.MYSQL: _MYSQL_TYPES,
    # MariaDB: wie MySQL, dazu eigene Typen (uuid ab 10.7, inet4 ab 10.10);
    # json ist dort ein Alias für longtext mit JSON_VALID-Prüfung
    Dialect.MARIADB: _MYSQL_TYPES + ["uuid", "inet6", "inet4"],
    Dialect.POSTGRESQL: [
        "integer", "bigint", "smallint", "numeric(10,2)", "double precision",
        "character varying(255)", "character(10)", "text", "date", "timestamp without time zone",
        "timestamp with time zone", "time", "boolean", "jsonb", "uuid", "bytea",
    ],
    Dialect.MSSQL: [
        "int", "bigint", "smallint", "tinyint", "decimal(10,2)", "float",
        "nvarchar(255)", "nvarchar(max)", "varchar(255)", "nchar(10)", "date", "datetime2(3)",
        "time", "bit", "uniqueidentifier", "varbinary(max)",
    ],
}

# Typ für neue Schlüsselspalten
ID_TYPES: dict[Dialect, str] = {
    Dialect.MYSQL: "int", Dialect.MARIADB: "int", Dialect.POSTGRESQL: "integer", Dialect.MSSQL: "int",
}

# Standardschema für neue Datenbanken (MySQL/MariaDB: keine Schemaebene)
DEFAULT_SCHEMAS: dict[Dialect, str | None] = {
    Dialect.MYSQL: None, Dialect.MARIADB: None, Dialect.POSTGRESQL: "public", Dialect.MSSQL: "dbo",
}

"""Views als eigene Objektart: Modell, Speicherung, Bearbeiten, Adapter (offline)."""

import pytest

from erd_editor.adapters import mysql, postgres
from erd_editor.model import Column, Database, Diagram, Dialect, Schema, Table, TableKind, TableRef
from erd_editor.model.editing import EditError, add_relationship, delete_table, replace_table
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict


def shop(name: str) -> TableRef:
    return TableRef("shop", None, name)


def view(name: str, *deps: str, sql: str = "SELECT 1") -> Table:
    return Table(name, [Column("id", "int")], kind=TableKind.VIEW, definition=sql,
                 view_dependencies=[shop(d) for d in deps])


@pytest.fixture
def diagram() -> Diagram:
    kunde = Table("kunde", [Column("id", "int", nullable=False)])
    bestellung = Table("bestellung", [Column("id", "int", nullable=False)])
    return Diagram([Database("shop", Dialect.MYSQL, [Schema(None, [
        kunde, bestellung, view("v_kunde", "kunde"), view("v_alles", "v_kunde", "bestellung"),
    ])], modeled=True)])


# ------------------------------------------------------------------ Modell


def test_view_kind_and_validation(diagram):
    assert diagram.find_table(shop("v_kunde")).is_view
    assert not diagram.find_table(shop("kunde")).is_view
    assert diagram.validate() == []
    diagram.find_table(shop("v_kunde")).view_dependencies.append(shop("fehlt"))
    assert any("fehlendes Objekt" in p for p in diagram.validate())


def test_persistence_roundtrip(diagram):
    data = document_to_dict(DiagramDocument(diagram))
    tables = data["schema"]["databases"][0]["schemas"][0]["tables"]
    assert "kind" not in tables[0]  # Tabellen bleiben in der Datei wie bisher
    assert tables[2]["kind"] == "VIEW"
    loaded = document_from_dict(data).diagram
    v = loaded.find_table(shop("v_alles"))
    assert v.is_view and v.definition == "SELECT 1"
    assert v.view_dependencies == [shop("v_kunde"), shop("bestellung")]
    assert not loaded.find_table(shop("kunde")).is_view


def test_old_files_without_kind_load_as_tables(diagram):
    data = document_to_dict(DiagramDocument(diagram))
    loaded = document_from_dict(data).diagram
    assert [t.kind for _, t in loaded.tables()] == [TableKind.TABLE, TableKind.TABLE, TableKind.VIEW, TableKind.VIEW]


# ------------------------------------------------------------------ Bearbeiten


def test_rename_follows_into_view_dependencies(diagram):
    renamed = replace_table(diagram, shop("kunde"), Table("kundin", [Column("id", "int", nullable=False)]))
    assert renamed.find_table(shop("v_kunde")).view_dependencies == [shop("kundin")]
    assert renamed.validate() == []


def test_delete_removes_view_dependency(diagram):
    new = delete_table(diagram, shop("v_kunde"))
    assert new.find_table(shop("v_alles")).view_dependencies == [shop("bestellung")]
    assert new.validate() == []


def test_no_relationships_with_views(diagram):
    with pytest.raises(EditError, match="Views"):
        add_relationship(diagram, shop("v_kunde"), shop("kunde"))
    with pytest.raises(EditError, match="Views"):
        add_relationship(diagram, shop("bestellung"), shop("v_kunde"))


# ------------------------------------------------------------------ Adapter offline


def mysql_rows(table_type: str = "VIEW"):
    tables = [{"table_name": "kunde", "comment": "", "table_type": "BASE TABLE"},
              {"table_name": "v_kunde", "comment": "VIEW", "table_type": table_type}]
    columns = [{"table_name": t, "column_name": "id", "column_type": "int", "is_nullable": "NO",
                "column_default": None, "comment": "", "extra": ""} for t in ("kunde", "v_kunde")]
    views = [{"view_name": "v_kunde",
              "definition": "select `shop`.`kunde`.`id` AS `id` from `shop`.`kunde` join `stamm`.`land`"}]
    return dict(tables=tables, columns=columns, indexes=[], foreign_keys=[], views=views)


def test_mysql_view_with_usage_table():
    usage = [{"view_name": "v_kunde", "referenced_schema": "shop", "referenced_table": "kunde"}]
    db = mysql.build_database("shop", view_usage=usage, **mysql_rows())
    v = db.schemas[0].table("v_kunde")
    assert v.is_view and v.comment is None  # „VIEW“ ist kein echter Kommentar
    assert v.view_dependencies == [shop("kunde")]
    assert v.definition == "select `kunde`.`id` AS `id` from `kunde` join `stamm`.`land`"
    assert [c.name for c in v.columns] == ["id"]


def test_mysql_view_without_usage_table_parses_definition():
    db = mysql.build_database("shop", view_usage=None, **mysql_rows())
    deps = db.schemas[0].table("v_kunde").view_dependencies
    assert deps == [shop("kunde"), TableRef("stamm", None, "land")]


@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        ("select `v1`.`id` AS `id` from `d`.`v1`", [("d", "v1")]),  # MariaDB: Spalte ohne DB
        ("select 1 from (`d`.`a` join `d`.`b` on(1))", [("d", "a"), ("d", "b")]),
        ("select x from `d`.`t` where y in (select z from `o`.`u`)", [("d", "t"), ("o", "u")]),
        ("select 1 from `d`.`we``ird`", [("d", "we`ird")]),
    ],
)
def test_view_usage_from_definition(definition, expected):
    assert mysql.view_usage_from_definition(definition) == expected


def test_postgres_view():
    db = postgres.build_database(
        "erd",
        schemas=[{"schema_name": "shop"}],
        tables=[{"schema_name": "shop", "table_name": "kunde", "comment": None, "kind": "r", "definition": None},
                {"schema_name": "shop", "table_name": "v", "comment": "Sicht", "kind": "v",
                 "definition": " SELECT kunde.id\n   FROM shop.kunde;"}],
        columns=[], indexes=[], foreign_keys=[],
        view_usage=[{"schema_name": "shop", "view_name": "v", "referenced_schema": "shop",
                     "referenced_table": "kunde"}],
    )
    v = db.schema("shop").table("v")
    assert v.is_view and v.comment == "Sicht"
    assert v.definition == "SELECT kunde.id\n   FROM shop.kunde"
    assert v.view_dependencies == [TableRef("erd", "shop", "kunde")]


# ------------------------------------------------------------------ Darstellung


@pytest.fixture
def scene(qapp, diagram):
    from erd_editor.diagram.scene import DiagramScene

    s = DiagramScene()
    s.load(diagram)
    return s


def test_scene_draws_dependencies(scene):
    keys = [(v.table, t.table) for v, t in (d.key for d in scene.dependencies)]
    assert keys == [("v_kunde", "kunde"), ("v_alles", "v_kunde"), ("v_alles", "bestellung")]
    assert all(len(d.points) >= 2 for d in scene.dependencies)
    # Pfeilspitze endet am Rand der benutzten Box
    dep = scene.dependencies[0]
    target = dep.target_item.mapRectToScene(dep.target_item.rect)
    assert dep.points[-1].x() in (pytest.approx(target.left()), pytest.approx(target.right()))


def test_view_box_is_marked(scene):
    from erd_editor.diagram.entity_item import VIEW_TAG

    view, table = scene.entities[shop("v_kunde")], scene.entities[shop("kunde")]
    assert view.header_font.italic() and not table.header_font.italic()
    tip = view.tooltip_at(view.rect.topLeft())  # Kopfzeile
    assert "(View)" in tip and "SELECT 1" in tip and "kunde" in tip
    assert VIEW_TAG == "VIEW"


def test_dependencies_follow_moves(scene):
    dep = scene.dependencies[0]
    before = list(dep.points)
    scene.entities[shop("kunde")].moveBy(0, 300)
    assert dep.points != before


def test_subdiagram_hides_lines_to_missing_objects(qapp, diagram):
    from erd_editor.diagram.scene import DiagramScene
    from erd_editor.model import filter_diagram

    s = DiagramScene()
    s.load(filter_diagram(diagram, [shop("v_alles"), shop("bestellung")]))
    assert [(v.table, t.table) for v, t in (d.key for d in s.dependencies)] == [("v_alles", "bestellung")]


def test_update_keeps_dependency_items(scene, diagram):
    import copy

    first = list(scene.dependencies)
    new = copy.deepcopy(diagram)
    new.find_table(shop("v_alles")).view_dependencies.remove(shop("bestellung"))
    assert scene.update_diagram(new)
    assert scene.dependencies == first[:2]  # dieselben Objekte, nur eine Linie weniger
    assert len([i for i in scene.items() if i.__class__.__name__ == "DependencyItem"]) == 2


# ------------------------------------------------------------------ SQL-Export


def test_ddl_views_after_tables_in_dependency_order(diagram):
    from erd_editor.export.ddl import generate_statements

    db = diagram.databases[0]
    # v_alles steht in der Liste vor v_kunde, benutzt sie aber
    schema = db.schemas[0]
    schema.tables = [t for t in schema.tables if t.name != "v_kunde"] + [schema.table("v_kunde")]
    stmts = generate_statements(db)
    creates = [s.split()[2] for s in stmts if s.startswith("CREATE VIEW")]
    assert creates == ["`v_kunde`", "`v_alles`"]
    last_table = max(i for i, s in enumerate(stmts) if s.startswith(("CREATE TABLE", "ALTER TABLE")))
    assert all(i > last_table for i, s in enumerate(stmts) if s.startswith("CREATE VIEW"))
    assert not any("CREATE TABLE `v_" in s for s in stmts)


@pytest.mark.parametrize("dialect", list(Dialect))
def test_ddl_never_drops_views(diagram, dialect):
    """Kein DROP mehr (Sicherheitsregel); Views kommen nach den Tabellen."""
    from erd_editor.export.ddl import generate_statements

    db = diagram.databases[0]
    db.dialect = dialect
    stmts = generate_statements(db)
    assert not any("DROP " in s.upper() for s in stmts)
    views = [i for i, s in enumerate(stmts) if s.startswith("CREATE VIEW")]
    assert views and min(views) > max(i for i, s in enumerate(stmts) if s.startswith("CREATE TABLE"))


def test_ddl_view_comments_and_missing_text():
    from erd_editor.export.ddl import generate_statements

    v = Table("v", kind=TableKind.VIEW, definition="SELECT 1;", comment="Sicht")
    empty = Table("leer", kind=TableKind.VIEW)
    for dialect, expected in ((Dialect.POSTGRESQL, "COMMENT ON VIEW"), (Dialect.MSSQL, "N'VIEW', N'v'")):
        db = Database("x", dialect, [Schema("s", [v, empty])])
        stmts = generate_statements(db)
        assert any(expected in s for s in stmts)
        create = next(s for s in stmts if s.startswith("CREATE VIEW"))
        assert create.endswith("AS\nSELECT 1")  # Semikolon entfernt
        assert "-- View leer: kein SQL-Text vorhanden" in stmts


def test_script_header_counts_views(diagram):
    from erd_editor.export.ddl import generate_script

    assert "2 Tabellen, 2 Views" in generate_script(diagram)


def test_neighbours_follow_view_dependencies(diagram):
    from erd_editor.model.subdiagram import neighbours

    assert neighbours(diagram, {shop("v_kunde")}) == {shop("kunde"), shop("v_alles")}
    assert neighbours(diagram, {shop("bestellung")}) == {shop("v_alles")}


# ------------------------------------------------------------------ Modellieren


def test_guess_dependencies():
    from erd_editor.analysis.view_sql import guess_dependencies

    pg = [TableRef("erd", "shop", "kunde"), TableRef("erd", "stammdaten", "land"), TableRef("erd", "shop", "land")]
    sql = 'SELECT * FROM shop.kunde k LEFT JOIN "stammdaten"."LAND" l ON l.code = k.land_code'
    assert guess_dependencies(sql, pg) == [pg[0], pg[1]]  # Schema entscheidet
    assert guess_dependencies("select * from land", pg) == [pg[1], pg[2]]  # ohne Schema: alle gleichnamigen
    assert guess_dependencies("select * from kunde k join x on 1 -- join land", pg) == [pg[0]]
    assert guess_dependencies("", pg) == []


def test_view_dialog_new_follows_sql_until_edited(qapp):
    from PySide6.QtCore import Qt

    from erd_editor.ui.view_dialog import ViewDialog

    candidates = [shop("kunde"), shop("bestellung"), shop("v_x")]
    d = ViewDialog(Dialect.MYSQL, None, {"kunde"}, "shop", candidates)
    assert not d.box.button(d.box.StandardButton.Ok).isEnabled()  # Name fehlt
    d.name.setText("kunde")
    assert "gibt es dort schon" in d.message.text()
    d.name.setText("v_neu")
    d.sql.setPlainText("SELECT k.id FROM kunde k JOIN bestellung b ON b.kunde_id = k.id")
    assert d.dependencies() == [shop("kunde"), shop("bestellung")]
    d.deps.item(1).setCheckState(Qt.CheckState.Unchecked)  # von Hand geändert …
    d.sql.setPlainText("SELECT 1 FROM kunde JOIN bestellung ON 1")
    assert d.dependencies() == [shop("kunde")]  # … bleibt so
    d.detect_dependencies()
    assert d.dependencies() == [shop("kunde"), shop("bestellung")]
    d.add_column(Column("id", "int"))
    view = d.view()
    assert view.is_view and view.name == "v_neu" and [c.name for c in view.columns] == ["id"]
    assert view.definition.startswith("SELECT 1")


def test_view_dialog_edit_keeps_saved_selection(qapp, diagram):
    from erd_editor.ui.view_dialog import ViewDialog

    v = diagram.find_table(shop("v_alles"))
    candidates = [r for r, _ in diagram.tables()]
    d = ViewDialog(Dialect.MYSQL, v, {"kunde", "v_alles"}, "shop", candidates)
    assert shop("v_alles") not in [d.deps.item(i).data(0x0100) for i in range(d.deps.count())]  # nicht sich selbst
    assert d.dependencies() == [shop("bestellung"), shop("v_kunde")]
    assert d.box.button(d.box.StandardButton.Ok).isEnabled()  # eigener Name ist erlaubt
    assert d.view().comment is None


@pytest.fixture
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from erd_editor.main import MainWindow
    from erd_editor.model import Index, IndexKind

    def fail(*args):
        raise AssertionError(f"unerwartetes Hinweisfenster: {args[1:3]}")

    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, fail)
    w = MainWindow()
    w.create_database("shop", Dialect.MYSQL)
    w.create_table(shop("kunde"), Table("kunde", [Column("id", "int", nullable=False)],
                                        [Index("PRIMARY", IndexKind.PRIMARY, ("id",))]))
    yield w
    w.setWindowModified(False)
    w.close()


def test_create_edit_and_undo_view_in_window(window):
    window.create_table(shop("v_kunde"), view("v_kunde", "kunde", sql="SELECT id FROM kunde"))
    assert window.undo_stack.undoText() == "View „v_kunde“ anlegen"
    assert len(window.scene.dependencies) == 1
    box = window.scene.entities[shop("v_kunde")]
    window.update_table(shop("v_kunde"), view("v_kunden", "kunde"), {}, final_indexes=True)
    assert window.scene.entities[shop("v_kunden")] is box  # gezielt aktualisiert
    assert window.undo_stack.undoText() == "View „v_kunden“ bearbeiten"
    window.undo_stack.undo()
    window.undo_stack.undo()
    assert shop("v_kunde") not in window.scene.entities and not window.scene.dependencies


def test_double_click_on_view_opens_view_dialog(window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from erd_editor.ui import modeling

    window.create_table(shop("v_kunde"), view("v_kunde", "kunde"))
    opened = []

    class FakeDialog:
        def __init__(self, dialect, table, taken, location, candidates, parent):
            opened.append((table.name, sorted(c.table for c in candidates)))

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(modeling, "ViewDialog", FakeDialog)
    window.edit_table(shop("v_kunde"))
    assert opened == [("v_kunde", ["kunde", "v_kunde"])]


def test_relationship_tool_refuses_views(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    window.create_table(shop("v_kunde"), view("v_kunde", "kunde"))
    window.create_relationship(shop("v_kunde"), shop("kunde"))
    assert warnings and "Views" in warnings[0]

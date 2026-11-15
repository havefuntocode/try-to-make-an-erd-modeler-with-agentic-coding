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

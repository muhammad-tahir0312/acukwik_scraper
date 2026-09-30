from pathlib import Path
import ast


REPO = Path(__file__).resolve().parents[2]


def test_schema_has_airport_scoped_listing_and_role_tables():
    schema = (REPO / "aero-data-etl-final/db/etl_schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE public.organization_airport_listings" in schema
    assert "CREATE TABLE public.organization_airport_listing_roles" in schema
    assert "CREATE TABLE public.organization_airport_roles" in schema
    assert "organizations_name_norm_uniq" not in schema


def test_etl_does_not_fall_back_to_name_only_identity():
    etl = (REPO / "aero-data-etl-final/etl/services/entity_etl.py").read_text(encoding="utf-8")
    forbidden = 'SELECT * FROM organizations WHERE lower(trim(name)) = lower(trim(%s)) LIMIT 1'
    assert forbidden not in etl
    assert "upsert_airport_listing" in etl


def test_listing_upsert_parameter_count_matches_sql():
    source = (REPO / "aero-data-etl-final/etl/services/entity_etl.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upsert_airport_listing"
    )
    execute = next(
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    )
    sql = ast.literal_eval(execute.args[0])
    assert sql.count("%s") == len(execute.args[1].elts)

"""Tests for shared.openapi_tool_factory."""

from __future__ import annotations

import pytest

from shared.openapi_tool_factory import (
    OpenAPIOperation,
    OpenAPIParameter,
    from_openapi,
)


PETSTORE_MIN: dict = {
    "openapi": "3.0.3",
    "info": {"title": "petstore", "version": "1.0.0"},
    "servers": [{"url": "https://api.petstore.test"}],
    "paths": {
        "/pets/{petId}": {
            "get": {
                "operationId": "getPet",
                "summary": "Fetch one pet",
                "parameters": [
                    {"name": "petId", "in": "path", "required": True,
                     "schema": {"type": "integer"}},
                    {"name": "verbose", "in": "query",
                     "schema": {"type": "boolean"}},
                ],
            },
        },
        "/pets": {
            "post": {
                "operationId": "createPet",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "tag": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def test_parse_returns_operations():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    assert f.namespace == "petstore"
    assert sorted(f.list_ids()) == ["createPet", "getPet"]
    assert f.server_url == "https://api.petstore.test"


def test_get_operation_has_path_and_query_params():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("getPet")
    assert op.method == "GET"
    assert op.path == "/pets/{petId}"
    by_name = {p.name: p for p in op.parameters}
    assert by_name["petId"].location == "path"
    assert by_name["petId"].required is True
    assert by_name["petId"].schema_type == "integer"
    assert by_name["verbose"].location == "query"
    assert by_name["verbose"].required is False


def test_post_operation_has_body_keys():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("createPet")
    assert op.request_body_required is True
    assert set(op.request_body_keys) == {"name", "tag"}


def test_validate_missing_required_path_param():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("getPet")
    errs = op.validate({})
    assert any("petId" in e for e in errs)


def test_validate_wrong_type():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("getPet")
    errs = op.validate({"petId": "not-an-int"})
    assert any("integer" in e for e in errs)


def test_validate_post_missing_body():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("createPet")
    errs = op.validate({})
    assert any("body" in e for e in errs)


def test_build_request_substitutes_path_param():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("getPet")
    req = op.build_request({"petId": 42, "verbose": True})
    assert req.method == "GET"
    assert "/pets/42" in str(req.url)
    assert "verbose=true" in str(req.url).lower()


def test_build_request_post_body():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("createPet")
    req = op.build_request({"body": {"name": "Rex", "tag": "dog"}})
    assert req.method == "POST"
    assert b'"name"' in req.content
    assert b"Rex" in req.content


def test_build_request_raises_on_invalid():
    f = from_openapi(PETSTORE_MIN, namespace="petstore")
    op = f.operation("getPet")
    with pytest.raises(ValueError):
        op.build_request({})


def test_duplicate_operation_id_rejected():
    spec = {
        "paths": {
            "/a": {"get": {"operationId": "dup"}},
            "/b": {"get": {"operationId": "dup"}},
        },
    }
    with pytest.raises(ValueError, match="duplicate"):
        from_openapi(spec, namespace="x")


def test_missing_paths_rejected():
    with pytest.raises(ValueError):
        from_openapi({}, namespace="x")


def test_non_dict_spec_rejected():
    with pytest.raises(TypeError):
        from_openapi("not-a-dict", namespace="x")  # type: ignore[arg-type]


def test_skips_op_without_operation_id():
    spec = {
        "paths": {
            "/x": {"get": {"summary": "no id"}},
            "/y": {"get": {"operationId": "hasId"}},
        },
    }
    f = from_openapi(spec, namespace="x")
    assert f.list_ids() == ["hasId"]


def test_server_url_override():
    f = from_openapi(
        PETSTORE_MIN, namespace="petstore",
        server_url_override="https://override.test",
    )
    op = f.operation("getPet")
    req = op.build_request({"petId": 1})
    assert "override.test" in str(req.url)


def test_path_level_parameters_inherited():
    spec = {
        "paths": {
            "/widgets/{wid}": {
                "parameters": [
                    {"name": "wid", "in": "path", "required": True,
                     "schema": {"type": "string"}},
                ],
                "get": {"operationId": "getWidget"},
            },
        },
    }
    f = from_openapi(spec, namespace="w")
    op = f.operation("getWidget")
    assert any(p.name == "wid" for p in op.parameters)

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trade_extract.api.app import create_app
from trade_extract.config import Settings

DOCS_OPENAPI = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
IMPLEMENTED = {
    "/v1/extract": {"post"},
    "/v1/results/{result_id}": {"get"},
    "/v1/health": {"get"},
    "/v1/health/ready": {"get"},
    "/v1/jobs/{job_id}": {"get"},
    "/v1/jobs/{job_id}/documents": {"get"},
    "/v1/jobs/{job_id}/conflicts": {"get"},
    "/v1/documents/{document_id}": {"get"},
}

pytestmark = pytest.mark.contract


def generated_schema(tmp_path: Path) -> dict:
    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql+psycopg://trade:trade@localhost:5432/unused",
            secret_key="contract-secret",
            api_key_pepper="contract-pepper",
            storage_dir=tmp_path / "storage",
        )
    )
    try:
        return app.openapi()
    finally:
        app.state.engine.dispose()


def documented_schema() -> dict:
    return yaml.safe_load(DOCS_OPENAPI.read_text(encoding="utf-8"))


def test_implemented_routes_match_the_committed_contract(tmp_path: Path) -> None:
    generated = generated_schema(tmp_path)
    documented = documented_schema()
    for path, methods in IMPLEMENTED.items():
        assert path in documented["paths"], f"{path} is missing from docs/openapi.yaml"
        assert path in generated["paths"], f"{path} is missing from generated OpenAPI"
        for method in methods:
            assert method in documented["paths"][path]
            assert method in generated["paths"][path]


def test_extract_request_body_and_headers_conform(tmp_path: Path) -> None:
    operation = generated_schema(tmp_path)["paths"]["/v1/extract"]["post"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["Idempotency-Key"]["in"] == "header"
    assert parameters["Idempotency-Key"]["schema"]["minLength"] == 8
    assert parameters["Idempotency-Key"]["schema"]["maxLength"] == 128

    body = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["required"] == ["documents"]
    assert body["properties"]["documents"]["minItems"] == 1
    assert body["properties"]["documents"]["maxItems"] == 20
    assert body["properties"]["documents"]["items"]["format"] == "binary"
    assert body["properties"]["job_ref"]["maxLength"] == 128
    assert body["properties"]["webhook_url"]["maxLength"] == 2048
    assert "202" in operation["responses"]


def test_response_schemas_include_documented_required_fields(tmp_path: Path) -> None:
    generated = generated_schema(tmp_path)
    components = generated["components"]["schemas"]
    documented = documented_schema()["components"]["schemas"]

    accepted = components["ExtractAccepted"]
    for field in documented["ExtractAccepted"]["required"]:
        assert field in accepted["properties"], field
        assert field in accepted.get("required", []), field

    result = components["Result"]
    for field in documented["Result"]["required"]:
        assert field in result["properties"], field
        assert field in result.get("required", []), field

    error_schema = components["Error"]
    for field in documented["Error"]["required"]:
        assert field in error_schema["properties"], field

    assert generated["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"


def test_health_is_unauthenticated(tmp_path: Path) -> None:
    generated = generated_schema(tmp_path)
    assert generated["paths"]["/v1/health"]["get"]["security"] == []
    assert generated["paths"]["/v1/health/ready"]["get"]["security"] == []


def test_error_responses_use_the_envelope(tmp_path: Path) -> None:
    generated = generated_schema(tmp_path)
    for path, methods in IMPLEMENTED.items():
        for method in methods:
            operation = generated["paths"][path][method]
            for status_code, response in operation["responses"].items():
                if status_code.isdigit() and int(status_code) >= 400:
                    schema = response["content"]["application/json"]["schema"]
                    assert schema["$ref"].endswith("/ErrorEnvelope"), (path, method, status_code)


def test_generated_properties_exist_in_the_committed_contract(tmp_path: Path) -> None:
    generated = generated_schema(tmp_path)["components"]["schemas"]
    documented = documented_schema()["components"]["schemas"]
    for name in ("Error", "ErrorEnvelope", "ExtractAccepted", "Result"):
        missing = set(generated[name]["properties"]) - set(documented[name]["properties"])
        assert missing == set(), f"{name} advertises undocumented properties: {sorted(missing)}"


def test_bounds_are_derived_from_settings(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql+psycopg://trade:trade@localhost:5432/unused",
            secret_key="contract-secret",
            api_key_pepper="contract-pepper",
            storage_dir=tmp_path / "storage",
            max_documents_per_group=7,
            max_job_ref_length=64,
            min_idempotency_key_length=12,
            max_idempotency_key_length=99,
            max_webhook_url_length=1000,
        )
    )
    try:
        operation = app.openapi()["paths"]["/v1/extract"]["post"]
    finally:
        app.state.engine.dispose()
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["Idempotency-Key"]["schema"]["minLength"] == 12
    assert parameters["Idempotency-Key"]["schema"]["maxLength"] == 99
    body = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["properties"]["documents"]["maxItems"] == 7
    assert body["properties"]["job_ref"]["maxLength"] == 64
    assert body["properties"]["webhook_url"]["maxLength"] == 1000


def test_include_parameter_publishes_the_documented_enum(tmp_path: Path) -> None:
    operation = generated_schema(tmp_path)["paths"]["/v1/results/{result_id}"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    schema = parameters["include"]["schema"]
    assert schema["type"] == "string"
    assert schema["default"] == "documents,conflicts,decision"
    assert schema["pattern"]
    for value in ("documents", "conflicts", "checks", "provenance", "review", "decision"):
        assert value in schema["pattern"]

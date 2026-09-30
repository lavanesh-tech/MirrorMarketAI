"""Product catalog API against real PostgreSQL."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

BASE = "/api/v1/products"


async def _product(
    api: httpx.AsyncClient, user: ApiUser, brand: str = "Apple", name: str = "MacBook Air 13"
) -> dict[str, Any]:
    response = await api.post(
        BASE,
        json={"brand": brand, "name": name, "category": "laptop", "description": "M-series"},
        headers=user.headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_create_and_get_product(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    created = await _product(api, user)
    assert created["variants"] == created["identifiers"] == created["specifications"] == []

    fetched = await api.get(f"{BASE}/{created['id']}", headers=user.headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "MacBook Air 13"


async def test_catalog_requires_authentication(api: httpx.AsyncClient) -> None:
    assert (await api.get(BASE)).status_code == 401
    assert (await api.post(BASE, json={})).status_code == 401


async def test_duplicate_product_is_case_and_space_insensitive(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    await _product(api, user, "Apple", "MacBook Air 13")
    response = await api.post(
        BASE,
        json={"brand": "APPLE", "name": "macbook  air 13", "category": "laptop"},
        headers=user.headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "product_already_exists"


@pytest.mark.parametrize(
    "payload",
    [
        {"brand": "", "name": "X", "category": "laptop"},
        {"brand": "B", "name": "X", "category": "spaceship"},
        {"brand": "B", "name": "X", "category": "laptop", "price": 999},
    ],
)
async def test_create_validates(api: httpx.AsyncClient, payload: dict[str, Any]) -> None:
    user = await register_user(api)
    assert (await api.post(BASE, json=payload, headers=user.headers)).status_code == 422


async def test_unknown_product_is_404(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    response = await api.get(f"{BASE}/{uuid.uuid4()}", headers=user.headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"


async def test_search_by_text_and_category_with_pagination(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    await _product(api, user, "Apple", "MacBook Air 13")
    await _product(api, user, "Apple", "MacBook Pro 14")
    await _product(api, user, "Dell", "XPS 13")
    monitor = await api.post(
        BASE, json={"brand": "Dell", "name": "U2723QE", "category": "monitor"}, headers=user.headers
    )
    assert monitor.status_code == 201

    macbooks = (await api.get(f"{BASE}?q=macbook", headers=user.headers)).json()
    assert [p["name"] for p in macbooks["items"]] == ["MacBook Air 13", "MacBook Pro 14"]

    dell_laptops = (await api.get(f"{BASE}?q=dell&category=laptop", headers=user.headers)).json()
    assert [p["name"] for p in dell_laptops["items"]] == ["XPS 13"]

    paged = (await api.get(f"{BASE}?limit=1&offset=1", headers=user.headers)).json()
    assert paged["page"]["total"] >= 4
    assert len(paged["items"]) == 1


async def test_search_treats_like_wildcards_literally(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    await _product(api, user, "Acme", "Widget")
    response = await api.get(f"{BASE}?q=%25", headers=user.headers)  # "%"
    assert response.status_code == 200
    assert all("%" in p["brand"] + p["name"] for p in response.json()["items"])


async def test_variants_identifiers_and_lookup(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    product = await _product(api, user)
    pid = product["id"]

    with_variant = await api.post(
        f"{BASE}/{pid}/variants",
        json={"name": "16GB / 512GB", "attributes": {"ram_gb": 16, "storage_gb": 512}},
        headers=user.headers,
    )
    assert with_variant.status_code == 201
    variant_id = with_variant.json()["variants"][0]["id"]

    dup_variant = await api.post(
        f"{BASE}/{pid}/variants", json={"name": "16gb / 512gb"}, headers=user.headers
    )
    assert dup_variant.status_code == 409

    added = await api.post(
        f"{BASE}/{pid}/identifiers",
        json={"scheme": "GTIN", "value": "0 36000 29145 2", "variant_id": variant_id},
        headers=user.headers,
    )
    assert added.status_code == 201
    assert added.json()["identifiers"] == [
        {"scheme": "GTIN", "value": "00036000291452", "variant_id": variant_id}
    ]

    found = await api.get(
        f"{BASE}/by-identifier?scheme=GTIN&value=0036000291452", headers=user.headers
    )
    assert found.status_code == 200
    assert found.json()["id"] == pid

    missing = await api.get(
        f"{BASE}/by-identifier?scheme=ASIN&value=B000000000", headers=user.headers
    )
    assert missing.status_code == 404


async def test_identifier_must_be_valid_and_globally_unique(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    first = await _product(api, user, "Brand", "One")
    second = await _product(api, user, "Brand", "Two")

    bad = await api.post(
        f"{BASE}/{first['id']}/identifiers",
        json={"scheme": "GTIN", "value": "4006381333932"},
        headers=user.headers,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_identifier"

    ok = await api.post(
        f"{BASE}/{first['id']}/identifiers",
        json={"scheme": "GTIN", "value": "4006381333931"},
        headers=user.headers,
    )
    assert ok.status_code == 201
    taken = await api.post(
        f"{BASE}/{second['id']}/identifiers",
        json={"scheme": "GTIN", "value": "04006381333931"},
        headers=user.headers,
    )
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "identifier_already_exists"


async def test_identifier_variant_must_belong_to_product(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    one = await _product(api, user, "B", "One")
    two = await _product(api, user, "B", "Two")
    other_variant = (
        await api.post(f"{BASE}/{two['id']}/variants", json={"name": "V"}, headers=user.headers)
    ).json()["variants"][0]["id"]

    response = await api.post(
        f"{BASE}/{one['id']}/identifiers",
        json={"scheme": "SKU", "value": "ABC-1", "variant_id": other_variant},
        headers=user.headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "variant_not_found"


async def test_specifications_upsert(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    pid = (await _product(api, user))["id"]

    first = await api.put(
        f"{BASE}/{pid}/specifications",
        json={
            "specifications": [
                {"key": "ram_gb", "value_number": "16", "unit": "GB"},
                {"key": "chip", "value_text": "Apple M3"},
            ]
        },
        headers=user.headers,
    )
    assert first.status_code == 200
    specs = {s["key"]: s for s in first.json()["specifications"]}
    assert specs["ram_gb"]["value_number"] == "16.000000"
    assert specs["chip"]["value_text"] == "Apple M3"

    replaced = await api.put(
        f"{BASE}/{pid}/specifications",
        json={"specifications": [{"key": "ram_gb", "value_number": "24", "unit": "GB"}]},
        headers=user.headers,
    )
    specs = {s["key"]: s for s in replaced.json()["specifications"]}
    assert specs["ram_gb"]["value_number"] == "24.000000"
    assert set(specs) == {"ram_gb", "chip"}


@pytest.mark.parametrize(
    "spec",
    [
        {"key": "RAM", "value_number": "16"},
        {"key": "ram_gb"},
        {"key": "ram_gb", "value_number": "16", "value_text": "sixteen"},
        {"key": "ram_gb", "value_number": "NaN"},
        {"key": "ram_gb", "value_number": "Infinity"},
    ],
)
async def test_specifications_validate(api: httpx.AsyncClient, spec: dict[str, Any]) -> None:
    user = await register_user(api)
    pid = (await _product(api, user))["id"]
    response = await api.put(
        f"{BASE}/{pid}/specifications", json={"specifications": [spec]}, headers=user.headers
    )
    assert response.status_code == 422


async def test_duplicate_spec_keys_in_one_request_rejected(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    pid = (await _product(api, user))["id"]
    response = await api.put(
        f"{BASE}/{pid}/specifications",
        json={
            "specifications": [
                {"key": "ram_gb", "value_number": "16"},
                {"key": "ram_gb", "value_number": "32"},
            ]
        },
        headers=user.headers,
    )
    assert response.status_code == 422


async def test_only_creator_can_edit_product(api: httpx.AsyncClient) -> None:
    creator, other = await register_user(api), await register_user(api)
    pid = (await _product(api, creator))["id"]
    for method, path, body in [
        ("POST", f"{BASE}/{pid}/variants", {"name": "V"}),
        ("POST", f"{BASE}/{pid}/identifiers", {"scheme": "SKU", "value": "X1"}),
        (
            "PUT",
            f"{BASE}/{pid}/specifications",
            {"specifications": [{"key": "a", "value_text": "b"}]},
        ),
    ]:
        response = await api.request(method, path, json=body, headers=other.headers)
        assert response.status_code == 403, path
    # Anyone signed in can still read it.
    assert (await api.get(f"{BASE}/{pid}", headers=other.headers)).status_code == 200

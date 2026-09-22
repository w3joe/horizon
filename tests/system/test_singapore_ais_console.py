from __future__ import annotations

import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "apps" / "console" / "src"
TRAFFIC_FIXTURE = CONSOLE / "fixtures" / "singapore-traffic-demo-v1.json"
GEOGRAPHY_FIXTURE = CONSOLE / "fixtures" / "singapore-area-demo-v1.geojson"
COMPONENT = CONSOLE / "components" / "SingaporeTrafficConsole.tsx"


def load_traffic() -> dict:
    return json.loads(TRAFFIC_FIXTURE.read_text(encoding="utf-8"))


def test_recorded_fallback_and_geography_are_self_contained() -> None:
    traffic = load_traffic()
    geography = json.loads(GEOGRAPHY_FIXTURE.read_text(encoding="utf-8"))

    assert traffic["map"]["bundle_id"] == "singapore-area-demo-v1"
    assert geography["type"] == "FeatureCollection"
    assert geography["bbox"] == traffic["map"]["bbox"]
    assert any(feature["properties"]["safety_use"] for feature in geography["features"])
    assert "SIMULATION ONLY" in traffic["map"]["notice"]

    implementation = "\n".join(
        (CONSOLE / relative).read_text(encoding="utf-8")
        for relative in (
            "components/SingaporeTrafficConsole.tsx",
            "lib/singaporeTraffic.ts",
            "styles/singapore.css",
        )
    )
    assert 'fetch("/api/collector/v1/traffic/snapshot"' in implementation
    assert "WebSocket" not in implementation
    assert "tile.openstreetmap" not in implementation


def test_source_state_transition_and_stale_conflict_are_explicit() -> None:
    fixture = load_traffic()
    assert fixture["source_lifecycle"] == [
        "connecting",
        "healthy",
        "degraded",
        "stale",
        "unavailable",
    ]
    assert fixture["states"]["overview"]["source_state"] == "healthy"
    assert fixture["states"]["conflict"]["source_state"] == "degraded"

    conflict_contacts = fixture["states"]["conflict"]["contacts"]
    primary = next(contact for contact in conflict_contacts if contact["id"] == "mirror-031")
    shadow = next(contact for contact in conflict_contacts if contact["role"] == "shadow_only")
    assert primary["age_s"] > fixture["states"]["overview"]["source_age_s"]
    assert {"RADAR_AIS_DISAGREEMENT", "STALE_REPORT"} <= set(primary["conflict"])
    assert shadow["health"] == "unavailable"


def test_console_source_contains_no_provider_connection_or_secret_shape() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in CONSOLE.rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx", ".css", ".json", ".geojson"}
    )
    forbidden = (
        "wss://stream.aisstream.io",
        "new WebSocket",
        "AISSTREAM_API_KEY",
        '"APIKey"',
        '"MetaData"',
        '"mmsi"',
    )
    for token in forbidden:
        assert token not in source

    fixture_text = TRAFFIC_FIXTURE.read_text(encoding="utf-8")
    assert "MMSI" not in fixture_text
    assert "provider_payload" not in fixture_text


def test_mirrored_encounter_is_paired_and_geospatially_aligned() -> None:
    fixture = load_traffic()
    west, south, east, north = fixture["map"]["bbox"]
    origin = fixture["origin"]

    for state in fixture["states"].values():
        protected = state["branches"]["protected"]
        counterfactual = state["branches"]["counterfactual"]
        assert protected["path"][0] == counterfactual["path"][0]
        for contact in state["contacts"]:
            latitude = origin["latitude_deg"] + contact["north_m"] / 111_320
            longitude = origin["longitude_deg"] + contact["east_m"] / (
                111_320 * math.cos(math.radians(origin["latitude_deg"]))
            )
            assert south <= latitude <= north
            assert west <= longitude <= east

    conflict = fixture["states"]["conflict"]["branches"]
    assert conflict["protected"]["cpa_m"] > conflict["counterfactual"]["cpa_m"]
    assert conflict["protected"]["path"] != conflict["counterfactual"]["path"]
    assert "INTERVENTION" in conflict["protected"]["status"]
    assert "VIOLATION" in conflict["counterfactual"]["status"]


def test_ui_exposes_stable_screenshot_states_and_required_labels() -> None:
    component = COMPONENT.read_text(encoding="utf-8")
    for label in (
        "Traffic overview",
        "RTA intervention",
        "AIS REPORTED · SHADOW",
        "RECORDED MIRROR",
        "SYNTHETIC / RADAR",
        "Report age",
        "Uncertainty",
        "CPA",
        "TCPA",
        "Browser boundary",
        "Paired mirrored encounter display",
    ):
        assert label in component
    assert 'data-demo-state={stateId}' in component
    assert 'url.searchParams.set("state", next)' in component
    assert "Synchronized Singapore 2D map and 3D NED scene" in component

"""
Seed data generator — creates deliberately redundant sample data
to make the consolidation problem visible during demos.

Categories:
  • sensor_readings  — IoT/robot sensor logs
  • incident_reports — safety/maintenance events
  • navigation_logs  — route/location data
  • maintenance      — equipment status
  • user_notes       — manual observations

Each category includes near-duplicates (same event logged multiple times
with slight wording variations) so DBSCAN can cluster them.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from activity_log import activity_log
from models import ActivityType


# ── Near-duplicate groups (variations of the same underlying event) ───────

SEED_GROUPS: list[dict] = [
    # ── Sensor Readings (redundant temperature alerts) ──
    {
        "category": "sensor_readings",
        "source": "temp-sensor-A3",
        "importance": 0.6,
        "tags": ["temperature", "alert", "zone-A"],
        "entries": [
            "Temperature sensor A3 reads 87.2°F in warehouse zone A — above normal threshold of 80°F",
            "Temp sensor A3: 87.5°F detected in zone A, exceeding 80°F limit",
            "Zone A temperature alert: sensor A3 reporting 87.1°F, threshold is 80°F",
            "WARNING: Sensor A3 zone-A temperature 87.3°F (limit 80°F)",
            "Temperature anomaly zone A — sensor A3 shows 87.4°F vs 80°F threshold",
        ],
    },
    # ── Incident Reports (same spill logged multiple times) ──
    {
        "category": "incident_reports",
        "source": "floor-robot-07",
        "importance": 0.9,
        "tags": ["spill", "hazard", "aisle-3"],
        "entries": [
            "Liquid spill detected in aisle 3 near shelf B-12. Approximately 500ml. Potential slip hazard.",
            "Spill in aisle 3 by shelf B-12, about half a liter of liquid on the floor, slip risk",
            "HAZARD: Aisle 3 / B-12 — liquid spill ~500ml, flagged as slip hazard by floor-robot-07",
            "Floor robot detected liquid spill aisle 3 shelf B-12, estimated 500ml, slip danger",
        ],
    },
    # ── Navigation Logs (repeated obstacle detection) ──
    {
        "category": "navigation_logs",
        "source": "nav-system",
        "importance": 0.5,
        "tags": ["obstacle", "reroute", "corridor-B"],
        "entries": [
            "Obstacle detected in corridor B at coordinates (34.2, 18.7). Rerouting via corridor C.",
            "Nav system: obstacle at corridor B (34.2, 18.7), taking alternate route through corridor C",
            "Corridor B blocked at (34.2, 18.7) — obstacle. Rerouting to corridor C.",
            "Route changed: obstacle found corridor B pos (34.2, 18.7), now using corridor C",
            "REROUTE: Corridor B obstacle at 34.2/18.7, fallback to corridor C path",
            "Navigation alert — corridor B impassable at (34.2, 18.7), diverting through C",
        ],
    },
    # ── Maintenance (battery status reports) ──
    {
        "category": "maintenance",
        "source": "power-mgmt",
        "importance": 0.7,
        "tags": ["battery", "charging", "status"],
        "entries": [
            "Battery level at 23%. Estimated 45 minutes remaining. Recommend returning to charging station.",
            "Power management: battery 23%, ~45 min remaining, suggest docking for charge",
            "Low battery alert: 23% charge, approximately 45 minutes of operation left, charging advised",
        ],
    },
    # ── User Notes with PII (should be tagged local-only) ──
    {
        "category": "user_notes",
        "source": "manual",
        "importance": 0.8,
        "tags": ["personnel", "contact"],
        "entries": [
            "Contact maintenance lead John at john.doe@warehouse.com or 555-123-4567 for the compressor issue",
            "Maintenance contact: John Doe, email john.doe@warehouse.com, phone 555-123-4567, re: compressor",
        ],
    },
    # ── Unique entries (should NOT be merged) ──
    {
        "category": "sensor_readings",
        "source": "humidity-sensor-B1",
        "importance": 0.4,
        "tags": ["humidity", "zone-B"],
        "entries": [
            "Humidity sensor B1: 78% relative humidity in zone B, within acceptable range (40-85%)",
        ],
    },
    {
        "category": "incident_reports",
        "source": "security-cam-12",
        "importance": 0.95,
        "tags": ["security", "unauthorized"],
        "entries": [
            "Unauthorized access attempt detected at loading dock 4. Badge scan failed 3 times. Security notified.",
        ],
    },
    {
        "category": "navigation_logs",
        "source": "delivery-bot-03",
        "importance": 0.6,
        "tags": ["delivery", "completed"],
        "entries": [
            "Delivery completed: package #WH-2024-0892 delivered to station 7 in 4 minutes 12 seconds",
        ],
    },
    {
        "category": "maintenance",
        "source": "hvac-controller",
        "importance": 0.5,
        "tags": ["hvac", "schedule"],
        "entries": [
            "HVAC system scheduled maintenance window: Saturday 02:00-06:00. All zones will cycle to minimum.",
        ],
    },
    {
        "category": "user_notes",
        "source": "manual",
        "importance": 0.3,
        "tags": ["inventory", "note"],
        "entries": [
            "Shelf C-7 inventory looks low on SKU #4421 (blue widgets). Might need restock by Thursday.",
        ],
    },
    # ── More duplicates for demo impact ──
    {
        "category": "sensor_readings",
        "source": "pressure-sensor-D2",
        "importance": 0.7,
        "tags": ["pressure", "alert", "zone-D"],
        "entries": [
            "Pressure sensor D2 reading 42 PSI in pneumatic line zone D — normal range is 30-40 PSI",
            "Zone D pressure alert: sensor D2 at 42 PSI, exceeds 40 PSI upper limit",
            "Pneumatic line pressure HIGH: 42 PSI on sensor D2 (zone D), max is 40 PSI",
            "Sensor D2 zone-D pneumatic pressure 42 PSI — over the 40 PSI threshold",
        ],
    },
    {
        "category": "incident_reports",
        "source": "safety-system",
        "importance": 0.85,
        "tags": ["noise", "safety", "zone-C"],
        "entries": [
            "Noise level in zone C measured at 92 dB for the past 15 minutes. OSHA limit is 90 dB for 8 hours.",
            "Zone C noise alert: 92 dB sustained for 15 min. Exceeds OSHA 90 dB threshold.",
            "Safety: zone C noise levels at 92 decibels, above OSHA 90 dB 8-hour limit, ongoing 15 minutes",
        ],
    },
    # ── Hardware Diagnostics & Part Codes (Hybrid Search Benchmark targets) ──
    {
        "category": "maintenance",
        "source": "can-bus-monitor",
        "importance": 0.95,
        "tags": ["error-code", "motor", "critical"],
        "entries": [
            "CRITICAL FAULT: Actuator motor 4 controller tripped with error code ERR-4021 during heavy payload transit",
            "Diagnostic log: Motor controller #4 reported ERR-4021 thermal overload shutdown on auxiliary CAN bus",
            "Hardware alert ERR-4021: Actuator motor 4 inverter failure in robot arm joint",
        ],
    },
    {
        "category": "sensor_readings",
        "source": "bms-telemetry",
        "importance": 0.9,
        "tags": ["battery", "hardware", "serial-number"],
        "entries": [
            "Battery pack module SN-A3-7781 reported cell bank 2 voltage degradation below 3.1V threshold",
            "BMS diagnostic: Hardware unit SN-A3-7781 cell resistance anomaly detected during fast charge cycle",
        ],
    },
    {
        "category": "maintenance",
        "source": "lidar-diagnostics",
        "importance": 0.85,
        "tags": ["lidar", "calibration", "part-number"],
        "entries": [
            "Optical navigation lidar component PART-XYZ-990 calibration offset drifted by 1.8 degrees",
            "Maintenance required for sensor assembly PART-XYZ-990: angular alignment check needed",
        ],
    },
]


def generate_seed_data(store) -> dict:
    """
    Load all seed groups into the store.  Returns stats about what was loaded.
    """
    total = 0
    groups = 0
    duplicate_sets = 0

    for group in SEED_GROUPS:
        groups += 1
        if len(group["entries"]) > 1:
            duplicate_sets += 1

        for text in group["entries"]:
            store.add_memory(
                text=text,
                source=group["source"],
                importance=group["importance"],
                tags=group["tags"],
                category=group["category"],
                point_id=str(uuid.uuid4()),
                check_for_anomaly=False,
            )
            total += 1

    activity_log.log(
        ActivityType.SEED_DATA_LOADED,
        "Sample data loaded",
        f"Loaded {total} memories across {groups} groups ({duplicate_sets} sets contain near-duplicates).",
        {"total": total, "groups": groups, "duplicate_sets": duplicate_sets},
    )

    return {
        "total_records": total,
        "groups": groups,
        "duplicate_sets": duplicate_sets,
        "message": (
            f"Seeded {total} records. {duplicate_sets} groups contain deliberate "
            f"near-duplicates — run consolidation to merge them."
        ),
    }

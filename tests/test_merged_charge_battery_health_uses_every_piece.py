"""Battery-health telemetry must span every piece of a merged charge session."""
from datetime import datetime, timedelta, timezone

import pytest

import db as D
import db_reader


def test_a_merged_charge_health_uses_energy_from_every_piece(tmp_path, monkeypatch):
    path = str(tmp_path / "merged-soh.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type, capacity_kwh) VALUES (1,'SOHTEST','C10',60.0)")
    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('battery_capacity_nominal_kwh','60')")

    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    split = start + timedelta(minutes=30)
    merged_end = start + timedelta(minutes=120)
    separate_start = merged_end + timedelta(minutes=5)
    separate_end = separate_start + timedelta(minutes=60)
    c.execute(
        "INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type) "
        "VALUES (1,1,?,?,20,25,'AC')", (start.isoformat(), split.isoformat()))
    c.execute(
        "INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type,merged_into_id) "
        "VALUES (2,1,?,?,25,40,'AC',1)", (split.isoformat(), merged_end.isoformat()))
    c.execute(
        "INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type) "
        "VALUES (3,1,?,?,40,50,'AC')", (separate_start.isoformat(), separate_end.isoformat()))

    # 6 kW throughout: the merged session stores 12 kWh over 20 SoC points.
    # Its first 5-point piece alone must not pass the 12-point minimum.
    for minute in range(0, 186, 5):
        t = start + timedelta(minutes=minute)
        if minute <= 30:
            soc = 20 + 5 * minute / 30
        elif minute <= 120:
            soc = 25 + 15 * (minute - 30) / 90
        elif minute < 125:
            soc = 40
        else:
            soc = 40 + 10 * (minute - 125) / 60
        c.execute(
            "INSERT INTO positions (vehicle_id,recorded_at,soc,charging,charge_voltage_v,"
            "charge_current_a,battery_min_temp) VALUES (1,?,?,1,240,25,20)",
            (t.isoformat(), soc))
    c.commit()
    c.close()

    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    health = db_reader.get_battery_health()

    assert health["sample_count"] == 1, health["points"]
    point = health["points"][0]
    assert point["charge_id"] == 1
    assert point["soc_delta_used"] == 20.0
    assert point["energy_kwh"] == pytest.approx(12.0, abs=0.1)
    assert point["capacity_kwh"] == pytest.approx(60.0, abs=0.5)
    assert health["latest_soh_pct"] == pytest.approx(100.0, abs=1.0)

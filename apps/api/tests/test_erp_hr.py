"""RR.HH. sobre una base de tenant REAL: calculo de horas, asistencia, extras
con aprobacion, adelantos, ausencias, bloqueados, traslados, planilla con IPS
y cierre inmutable; permisos (supervisor sin montos)."""

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole as R, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import hr
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.tenant_db_manager import tenant_db_manager

PASSWORD = "ClaveDePruebaSegura123"
D = Decimal
# Quincena cerrada del pasado: 1 al 15 de agosto de 2026.
P_START, P_END = date(2026, 8, 1), date(2026, 8, 15)


def _site(**kw):
    base = dict(start_time=time(7, 0), end_time=time(15, 0), workdays=[0, 1, 2, 3, 4], tolerance_minutes=10, day_schedules=[])
    return SimpleNamespace(**(base | kw))


CFG = SimpleNamespace(workday_hours=D("8"), rounding_minutes=15)
MON = date(2026, 8, 3)
SAT = date(2026, 8, 8)


@pytest.mark.parametrize("tin,tout,exp", [
    (time(7, 0), time(15, 0), (D("8"), D("0"))),
    (time(7, 10), time(15, 0), (D("8"), D("0"))),        # dentro de la tolerancia
    (time(7, 20), time(15, 0), (D("7.75"), D("0"))),     # 10 min de tardanza -> 1 bloque
    (time(7, 0), time(14, 30), (D("7.75"), D("0"))),     # sale 30 min antes -> 20 min fuera de tolerancia -> 1 bloque
    (time(6, 0), time(16, 10), (D("8"), D("1.25"))),     # llegar temprano no suma; 70 min extra -> 5 bloques
    (time(7, 0), None, (D("0"), D("0"))),                # sin salida no vale
])
def test_calc_hours(tin, tout, exp):
    assert hr.calc_hours(tin, tout, _site(), MON, CFG) == exp


def test_calc_hours_day_exception_and_custom_schedule():
    sat = SimpleNamespace(weekday=5, start_time=time(7, 0), end_time=time(12, 0))
    site = _site(day_schedules=[sat])
    assert hr.calc_hours(time(7, 0), time(12, 0), site, SAT, CFG) == (D("5"), D("0"))
    emp = SimpleNamespace(custom_start=time(8, 0), custom_end=time(16, 0))
    # Horario individual: llegar 08:00 esta a horario; no aplica el sabado.
    assert hr.calc_hours(time(8, 0), time(16, 0), site, MON, CFG, emp) == (D("8"), D("0"))
    assert hr.calc_hours(time(7, 0), time(12, 0), site, SAT, CFG, emp) == (D("5"), D("0"))


@pytest.fixture(scope="module")
def t():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-hr-{sfx}", name="HR", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"hr-{sfx}", legal_name="HR SRL", display_name="HR", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.PRODUCTION,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(access)
    db.commit()
    tdb = provision_tenant_database(db, tenant.id, system.id, Environment.PRODUCTION)
    users, clients = [], {}
    for role in (R.CLIENT_ADMIN, R.HR, R.SITE_SUPERVISOR, R.SALES):
        u = User(email=f"hr-{role.value.lower()}-{sfx}@example.com", password_hash=hash_password(PASSWORD),
                 role=Role.CLIENT_USER, tenant_id=tenant.id)
        db.add(u)
        db.commit()
        db.add(TenantUser(tenant_id=tenant.id, user_id=u.id, role=role, status=TenantMemberStatus.ACTIVE))
        db.commit()
        users.append(u)
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": u.email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
        clients[role] = c
    engine = tenant_db_manager.get_engine(tdb, tdb.credential)
    yield {"c": clients, "base": f"/api/erp/{access.id}/hr", "engine": engine}
    force_drop_tenant_database_for_tests(tdb.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    uids = [u.id for u in users]
    db.query(UserSession).filter(UserSession.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def _site_body(code, saturday=False):
    sat = [{"weekday": 5, "start_time": "07:00", "end_time": "12:00"}] if saturday else []
    return {"code": code, "name": f"Obra {code}", "start_time": "07:00", "end_time": "15:00", "workdays": [0, 1, 2, 3, 4],
            "day_schedules": sat}


def _emp(c, base, site_id, ci, **kw):
    body = {"national_id": ci, "last_names": "Ficticio", "first_names": f"Empleado {ci}", "hourly_rate": "10000",
            "site_id": site_id, "start_date": "2026-07-01", "ips_entry": "2026-07-01"} | kw
    r = c.post(f"{base}/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_full_payroll_cycle(t):
    c, base = t["c"][R.HR], t["base"]
    site = c.post(f"{base}/sites", json=_site_body("OB1", saturday=True)).json()
    emp = _emp(c, base, site["id"], "9990001")
    assert emp["current_site_id"] == site["id"] and emp["hourly_rate"] == "10000.00"

    days = [P_START + timedelta(days=i) for i in range(15)]
    workdays = [d for d in days if d.weekday() < 5]
    saturdays = [d for d in days if d.weekday() == 5]
    absent = workdays[2]
    for d in saturdays:  # sabado medio dia: vale 5 h
        assert c.post(f"{base}/attendance", json={"employee_id": emp["id"], "site_id": site["id"], "work_date": d.isoformat(),
                                                "time_in": "07:00", "time_out": "12:00"}).json()["paid_hours"] == "5.00"
    for d in workdays:
        if d == absent:
            continue
        out = "16:10" if d == workdays[0] else "15:00"
        r = c.post(f"{base}/attendance", json={"employee_id": emp["id"], "site_id": site["id"], "work_date": d.isoformat(),
                                             "time_in": "07:00", "time_out": out})
        assert r.status_code == 200, r.text
    first = c.get(f"{base}/attendance", params={"date_from": workdays[0].isoformat(), "date_to": workdays[0].isoformat()}).json()[0]
    assert first["overtime_hours"] == "1.25" and first["overtime_status"] == "PENDING" and first["paid_hours"] == "8.00"
    # Con extras pendientes no se cierra.
    p = c.post(f"{base}/payrolls", json={"period_start": P_START.isoformat(), "period_end": P_END.isoformat()}).json()
    assert p["number"].startswith("PL-")
    assert c.post(f"{base}/payrolls/{p['id']}/close").status_code == 409
    ok = c.post(f"{base}/attendance/{first['id']}/overtime", json={"approve": True}).json()
    assert ok["paid_hours"] == "9.25"

    # Ausencia justificada: no cuenta como falta.
    assert c.post(f"{base}/absences", json={"employee_id": emp["id"], "start_date": absent.isoformat(),
                                           "end_date": absent.isoformat(), "kind": "MEDICAL"}).status_code == 201
    adv = c.post(f"{base}/advances", json={"employee_id": emp["id"], "advance_date": "2026-08-05", "amount": "100000"}).json()
    assert adv["number"].startswith("AD-")

    p = c.post(f"{base}/payrolls/{p['id']}/recalculate").json()
    line = p["lines"][0]
    n, ns = len(workdays) - 1, len(saturdays)
    assert D(line["regular_hours"]) == 8 * n + 5 * ns and D(line["overtime_hours"]) == D("1.25")
    assert line["unexcused_absences"] == 0 and line["days_worked"] == n + ns
    assert D(line["base_amount"]) == 80000 * n + 50000 * ns
    assert D(line["overtime_amount"]) == D("18750")        # 1,25 h x 10.000 x 1,5
    gross = D(80000 * n + 50000 * ns + 18750)
    assert D(line["gross"]) == gross
    assert D(line["ips_employee"]) == (gross * D("0.09")).quantize(D("1"))
    assert D(line["ips_employer"]) == (gross * D("0.165")).quantize(D("1"))
    assert D(line["net"]) == gross - D(line["ips_employee"]) - D("100000")

    closed = c.post(f"{base}/payrolls/{p['id']}/close").json()
    assert closed["status"] == "CLOSED" and closed["total_net"] == line["net"]
    # Periodo liquidado: nada lo cambia.
    r = c.post(f"{base}/attendance", json={"employee_id": emp["id"], "site_id": site["id"], "work_date": absent.isoformat(),
                                         "time_in": "07:00", "time_out": "15:00"})
    assert r.status_code == 409 and "cerrada" in r.json()["detail"]
    assert c.post(f"{base}/advances", json={"employee_id": emp["id"], "advance_date": "2026-08-06", "amount": "1"}).status_code == 409
    assert c.post(f"{base}/advances/{adv['id']}/void", json={"reason": "error"}).status_code == 409
    assert c.post(f"{base}/payrolls/{p['id']}/recalculate").status_code == 409
    assert c.delete(f"{base}/payrolls/{p['id']}").status_code == 409
    # Y la base lo impide aunque alguien saltee la API.
    with pytest.raises(Exception, match="cerrada"):
        with t["engine"].begin() as conn:
            conn.execute(text("UPDATE hr_payroll_lines SET net = 0"))
    # No se superponen planillas.
    assert c.post(f"{base}/payrolls", json={"period_start": "2026-08-10", "period_end": "2026-08-20"}).status_code == 409


def test_unexcused_absence_and_bonus(t):
    c, base = t["c"][R.HR], t["base"]
    s = c.get(f"{base}/settings").json()
    c.put(f"{base}/settings", json=s | {"attendance_bonus_enabled": True})
    site = c.post(f"{base}/sites", json=_site_body("OB2")).json()
    emp = _emp(c, base, site["id"], "9990002", bonus_per_hour="1000", ips_entry=None)
    start, end = date(2026, 7, 1), date(2026, 7, 15)
    days = [start + timedelta(days=i) for i in range(15) if (start + timedelta(days=i)).weekday() < 5]
    for d in days[1:]:  # falta el primer dia sin justificar
        c.post(f"{base}/attendance", json={"employee_id": emp["id"], "site_id": site["id"], "work_date": d.isoformat(),
                                         "time_in": "07:00", "time_out": "15:00"})
    q = {"period_start": start.isoformat(), "period_end": end.isoformat(), "site_id": site["id"]}
    line = c.get(f"{base}/payroll-preview", params=q).json()[0]
    assert line["unexcused_absences"] == 1 and line["bonus_amount"] == "0.00"
    assert line["ips_employee"] == "0.00"  # sin IPS
    # Premio forzado y ajuste con motivo.
    assert c.put(f"{base}/payroll-overrides", json={"employee_id": emp["id"], "period_start": q["period_start"],
                                                    "period_end": q["period_end"], "adjustment": "5000"}).status_code == 422
    assert c.put(f"{base}/payroll-overrides", json={"employee_id": emp["id"], "period_start": q["period_start"],
                                                    "period_end": q["period_end"], "force_bonus": True,
                                                    "adjustment": "5000", "reason": "Viatico"}).status_code == 200
    line = c.get(f"{base}/payroll-preview", params=q).json()[0]
    hours = D(line["regular_hours"])
    assert D(line["bonus_amount"]) == hours * 1000 and line["bonus_forced"] and D(line["adjustment"]) == 5000
    c.put(f"{base}/settings", json=s)


def test_transfer_blocklist_and_deactivation(t):
    c, base = t["c"][R.HR], t["base"]
    a = c.post(f"{base}/sites", json=_site_body("OB3")).json()
    b = c.post(f"{base}/sites", json=_site_body("OB4")).json()
    emp = _emp(c, base, a["id"], "9990003")
    assert c.post(f"{base}/employees/{emp['id']}/assign", json={"site_id": a["id"], "start_date": "2026-08-01"}).status_code == 409
    r = c.post(f"{base}/employees/{emp['id']}/assign", json={"site_id": b["id"], "start_date": "2026-08-01"})
    assert r.status_code == 201
    hist = c.get(f"{base}/employees/{emp['id']}/assignments").json()
    assert [h["end_date"] for h in hist] == [None, "2026-07-31"]
    # Bloquear la C.I. da de baja y cierra IPS; no se puede reactivar ni volver a cargar.
    assert c.post(f"{base}/blocklist", json={"national_id": "999.0003", "reason": "Incidente de prueba"}).status_code == 201
    e = c.get(f"{base}/employees/{emp['id']}").json()
    assert e["is_active"] is False and e["ips_exit"]
    assert c.patch(f"{base}/employees/{emp['id']}", json={"is_active": True}).status_code == 409
    assert c.post(f"{base}/employees", json={"national_id": "9990003", "last_names": "Otro", "first_names": "Intento"}).status_code in (409, 422)
    assert c.post(f"{base}/employees", json={"national_id": "9990004", "last_names": "Otro", "first_names": "Ok"}).status_code == 201
    assert c.post(f"{base}/employees", json={"national_id": "9990004", "last_names": "Dup", "first_names": "Ci"}).status_code == 409


def test_validations(t):
    c, base = t["c"][R.HR], t["base"]
    site = c.post(f"{base}/sites", json=_site_body("OB5")).json()
    emp = _emp(c, base, site["id"], "9990005")
    future = (date.today() + timedelta(days=3)).isoformat()
    body = {"employee_id": emp["id"], "site_id": site["id"], "work_date": future, "time_in": "07:00"}
    assert c.post(f"{base}/attendance", json=body).status_code == 422
    body |= {"work_date": "2026-07-20", "time_in": None, "time_out": "15:00"}
    assert c.post(f"{base}/attendance", json=body).status_code == 422
    assert c.post(f"{base}/payrolls", json={"period_start": "2026-07-01", "period_end": "2026-09-01"}).status_code == 422
    bad = _site_body("OB6") | {"workdays": [0, 0]}
    assert c.post(f"{base}/sites", json=bad).status_code == 422
    assert c.post(f"{base}/sites", json=_site_body("OB5")).status_code == 409
    # Correccion manual de horas y restablecer.
    a = c.post(f"{base}/attendance", json={"employee_id": emp["id"], "site_id": site["id"], "work_date": "2026-07-20",
                                         "time_in": "07:00", "time_out": "15:00"}).json()
    o = c.post(f"{base}/attendance/{a['id']}/override", json={"paid_hours": "6", "reason": "Lluvia"}).json()
    assert o["paid_hours"] == "6.00" and o["manual_override"]
    assert c.post(f"{base}/attendance/{a['id']}/reset").json()["paid_hours"] == "8.00"


def test_permissions_supervisor_sees_no_money(t):
    base = t["base"]
    hr_c, sup, sales = t["c"][R.HR], t["c"][R.SITE_SUPERVISOR], t["c"][R.SALES]
    site = hr_c.post(f"{base}/sites", json=_site_body("OB7")).json()
    emp = _emp(hr_c, base, site["id"], "9990007")
    seen = sup.get(f"{base}/employees/{emp['id']}").json()
    assert seen["hourly_rate"] is None and seen["bank_account"] is None
    assert sup.post(f"{base}/attendance", json={"employee_id": emp["id"], "site_id": site["id"], "work_date": "2026-07-21",
                                              "time_in": "07:00", "time_out": "15:00"}).status_code == 200
    assert sup.get(f"{base}/payrolls").status_code == 403
    assert sup.get(f"{base}/advances", params={"date_from": "2026-07-01", "date_to": "2026-07-31"}).status_code == 403
    assert sup.post(f"{base}/employees", json={"national_id": "9990099", "last_names": "X", "first_names": "Y"}).status_code == 403
    assert sales.get(f"{base}/employees").status_code == 403
    # El admin si ve montos.
    assert t["c"][R.CLIENT_ADMIN].get(f"{base}/employees/{emp['id']}").json()["hourly_rate"] == "10000.00"


def test_device_marking(t):
    c, base = t["c"][R.HR], t["base"]
    access_id = base.split("/")[3]
    site = c.post(f"{base}/sites", json=_site_body("OB8") | {"latitude": "-25.300000", "longitude": "-57.600000",
                                                             "geofence_radius_m": 300}).json()
    emp = _emp(c, base, site["id"], "9990008")
    other = c.post(f"{base}/sites", json=_site_body("OB9")).json()
    outsider = _emp(c, base, other["id"], "9990009")
    dev = c.post(f"{base}/devices", json={"site_id": site["id"], "name": "Tablet porteria"}).json()
    token = dev["token"]
    assert token and c.get(f"{base}/devices").json()[0]["token"] is None  # el token se ve una sola vez

    pub = TestClient(app)
    url = f"/api/public/hr/{access_id}"
    h = {"X-Device-Token": token, "X-Device-Fp": "aparato-uno-123"}
    info = pub.post(f"{url}/device", headers=h).json()
    assert info["site"] == "Obra OB8" and info["requires_location"] is True
    # Otro aparato con el mismo link: rechazado.
    assert pub.post(f"{url}/device", headers=h | {"X-Device-Fp": "aparato-dos-456"}).status_code == 403
    assert pub.post(f"{url}/device", headers=h | {"X-Device-Token": "x" * 40}).status_code == 403

    near = {"latitude": -25.3005, "longitude": -57.6005}
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    body = {"national_id": "9990008", "client_id": "intento-0001", "occurred_at": five_min_ago}
    assert pub.post(f"{url}/mark", headers=h, json=body).status_code == 422          # sin ubicacion
    far = pub.post(f"{url}/mark", headers=h, json=body | {"latitude": -25.4, "longitude": -57.6})
    assert far.status_code == 422 and "m de la obra" in far.json()["detail"]
    # C.I. de otra obra o inexistente: misma respuesta generica.
    r1 = pub.post(f"{url}/mark", headers=h, json=body | near | {"national_id": "9990009"})
    r2 = pub.post(f"{url}/mark", headers=h, json=body | near | {"national_id": "1234567"})
    assert r1.status_code == r2.status_code == 422 and r1.json() == r2.json()

    first = pub.post(f"{url}/mark", headers=h, json=body | near)
    assert first.status_code == 200 and first.json()["result"] == "IN"
    again = pub.post(f"{url}/mark", headers=h, json=body | near)                     # reintento sin señal
    assert again.json()["result"] == "DUPLICATE" and again.json()["time"] == first.json()["time"]
    out = pub.post(f"{url}/mark", headers=h, json={"national_id": "9990008", "client_id": "intento-0002"} | near)
    assert out.status_code == 200 and out.json()["result"] == "OUT"
    assert pub.post(f"{url}/mark", headers=h, json={"national_id": "9990008", "client_id": "intento-0003"} | near).status_code == 409
    today = out.json()["work_date"]
    rec = c.get(f"{base}/attendance", params={"date_from": today, "date_to": today, "employee_id": emp["id"]}).json()[0]
    assert rec["source"] == "DEVICE" and rec["time_in"] and rec["time_out"]
    # Reiniciar el vinculo permite otro aparato; deshabilitar corta todo.
    c.post(f"{base}/devices/{dev['id']}/reset")
    assert pub.post(f"{url}/device", headers=h | {"X-Device-Fp": "aparato-dos-456"}).status_code == 200
    c.post(f"{base}/devices/{dev['id']}/disable")
    assert pub.post(f"{url}/device", headers=h | {"X-Device-Fp": "aparato-dos-456"}).status_code == 403
    assert outsider["id"]

"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime,date, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def shift_window(att_date: date, shift_start: str, shift_end: str) -> tuple:
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))

    start = datetime(
        att_date.year, att_date.month, att_date.day,
        sh, sm, tzinfo=IST
    )

    end_date = (
        att_date + timedelta(days=1)
        if (eh, em) <= (sh, sm)
        else att_date
    )

    end = datetime(
        end_date.year, end_date.month, end_date.day,
        eh, em, tzinfo=IST
    )

    return start, end


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    elapsed_seconds = int((punch_in - start).total_seconds())

    if elapsed_seconds <= 600:
        return 0

    return elapsed_seconds // 60

def work_hours_cents(punch_in: datetime, punch_out: datetime) -> int:
    elapsed_seconds = int((punch_out - punch_in).total_seconds())

    if elapsed_seconds < 0:
        raise ValueError("punch_out cannot be before punch_in")

    return (2 * elapsed_seconds * 100 + 3600) // (2 * 3600)


def ms_to_dt_trunc(ms: int) -> datetime:
    return EPOCH + timedelta(seconds=ms // 1000)

def dt_to_ms(dt: datetime) -> int:
    return (dt - EPOCH) // timedelta(milliseconds=1)

def attendance_date(punch_in: datetime, ss: str, se: str) -> date:
    local_time = punch_in.astimezone(IST)
    shift_start = tuple(map(int, ss.split(":")))
    shift_end = tuple(map(int, se.split(":")))

    punch_time = (local_time.hour, local_time.minute,local_time.second)

    att_date = local_time.date()

    if shift_end <= shift_start and punch_time < (shift_end[0], shift_end[1], 0):
        att_date = att_date - timedelta(days=1)

    return att_date


def compute_late_minutes(punch_in: datetime,att_date: date,shift_start: str,shift_end: str) -> int:
    start, _ = shift_window(att_date, shift_start, shift_end)

    punch_in_ist = punch_in.astimezone(IST)
    elapsed_seconds = int((punch_in_ist - start).total_seconds())

    if elapsed_seconds <= 600:
        return 0

    return elapsed_seconds // 60

def compute_overtime(punch_out: datetime,att_date: date,shift_start: str,shift_end: str) -> int:
    _, end = shift_window(att_date, shift_start, shift_end)

    elapsed_seconds = int((punch_out - end).total_seconds())

    if elapsed_seconds <= 0:
        return 0

    minutes = elapsed_seconds // 60

    if minutes < 30:
        return 0
    else:
        return minutes

def derive_fields(punch_in: datetime,punch_out: Optional[datetime],shift_start: str,shift_end: str) -> dict:
    att_date = attendance_date(punch_in, shift_start, shift_end)

    late_minutes = compute_late_minutes(punch_in, att_date, shift_start, shift_end)

    overtime_minutes = 0
    work_hours = 0
    half_day = False

    if punch_out is not None:
        overtime_minutes = compute_overtime(
            punch_out, att_date, shift_start, shift_end
        )
        work_hours = work_hours_cents(punch_in, punch_out)
        half_day = work_hours < 450

    return {
        "attendance_date": att_date,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "work_hours": work_hours,
        "half_day": half_day,
    }



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = page * page_size
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at else datetime.now()
    d = ts.date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

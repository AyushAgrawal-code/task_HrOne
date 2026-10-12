"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
import re
import logging
import json
from bson import json_util

load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    serverSelectionTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

log = logging.getLogger("attendance")


def ensure_indexes():
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uniq_employee_attendance_date",
    )
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="attendance_date_emp",
    )
    db.attendance_logs.create_index(
        [("status", 1), ("date", -1), ("emp_code", 1)],
        name="attendance_status_date_emp",
    )

    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uniq_employee_code",
    )
    db.employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="employee_department_code",
    )
    db.employees.create_index(
        [("department", 1), ("joined_on", 1)],
        name="employee_department_joined",
    )
    db.employees.create_index(
        [("joined_on", 1)],
        name="employee_joined",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_indexes()
    except PyMongoError:
        log.exception("Could not create MongoDB indexes")
        raise

    yield


app = FastAPI(title="Employee Attendance & Analytics API",version="2.0.0",lifespan=lifespan,)

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

def derive_fields(punch_in: datetime,punch_out: Optional[datetime],att_date: date,shift_start: str,shift_end: str,) -> dict:
    late_minutes = compute_late_minutes(
        punch_in, att_date, shift_start, shift_end
    )

    overtime_minutes = 0
    work_hours = None
    half_day = False

    if punch_out is not None:
        overtime_minutes = compute_overtime(
            punch_out, att_date, shift_start, shift_end
        )
        cents = work_hours_cents(punch_in, punch_out)
        work_hours = cents / 100
        half_day = cents < 450

    return {
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "work_hours": work_hours,
        "half_day": half_day,
    }


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

EpochMs = Annotated[
    int,
    Field(
        strict=True,
        ge=100_000_000_000,
        le=4_102_444_800_000
    )
]


class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP[0-9]{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
    min_length=1,
    pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    joined_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end cannot be equal")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None


class RegularizeIn(BaseModel):
    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None
    punch_in: Optional[EpochMs] = None
    punch_out: Optional[EpochMs] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


def to_ms(value):
    if isinstance(value, datetime):
        return dt_to_ms(value)
    return value


def serialize_record(doc: dict) -> dict:
    history = []

    for entry in doc.get("history") or []:
        changes = {}

        for field, change in (entry.get("changes") or {}).items():
            changes[field] = {
                "from": to_ms(change.get("from")),
                "to": to_ms(change.get("to")),
            }

        history.append({
            "at": to_ms(entry.get("at")),
            "by": entry.get("by"),
            "reason": entry.get("reason"),
            "changes": changes,
        })

    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_ms(doc.get("punch_in")),
        "punch_out": to_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": int(doc.get("late_minutes") or 0),
        "overtime_minutes": int(doc.get("overtime_minutes") or 0),
        "half_day": bool(doc.get("half_day", False)),
        "history": history,
    }


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    doc["created_at"] = dt_to_ms(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}
    if department is not None:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    for item in items:
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = dt_to_ms(item["created_at"])

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is None:
        ts = ms_to_dt_trunc(
            dt_to_ms(datetime.now(timezone.utc))
        )
    else:
        ts = ms_to_dt_trunc(body.punched_at)

    att_date = attendance_date(
        ts, emp["shift_start"], emp["shift_end"]
    )
    date_str = att_date.isoformat()

    fields = derive_fields(
        ts,
        None,
        att_date,
        emp["shift_start"],
        emp["shift_end"],
    )

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        **fields,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this date",
        )

    return serialize_record(doc)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    # Validate date filters
    for field, value in (
        ("date_from", date_from),
        ("date_to", date_to),
    ):
        if value is not None:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} must be a valid YYYY-MM-DD date",
                )

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )

    # Build the query using the shared builder
    query, sort, skip, limit, hint = build_attendance_find(
        emp_code=emp_code,
        date_from=date_from,
        date_to=date_to,
        status=status,
        page=page,
        page_size=page_size,
    )

    total = db.attendance_logs.count_documents(query)

    docs = list(
        db.attendance_logs.find(query, {"_id": 0})
        .hint(list(hint.items()))
        .sort(sort)
        .skip(skip)
        .limit(limit)
    )

    return {
        "items": [serialize_record(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }



def build_attendance_find(
    emp_code=None,
    date_from=None,
    date_to=None,
    status=None,
    page=1,
    page_size=20,
):
    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if date_from is not None or date_to is not None:
        query["date"] = {}

        if date_from is not None:
            query["date"]["$gte"] = date_from

        if date_to is not None:
            query["date"]["$lte"] = date_to

    if status is not None:
        query["status"] = status

    skip = (page - 1) * page_size

    sort = [("date", -1), ("emp_code", 1)]
    hint = {
        "date": -1,
        "emp_code": 1,
    }

    return query, sort, skip, page_size, hint


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    # 1. Check whether employee exists
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 2. Get punch-out timestamp in UTC
    if body.punched_at is None:
        ts = ms_to_dt_trunc(
            dt_to_ms(datetime.now(timezone.utc))
        )
    else:
        ts = ms_to_dt_trunc(body.punched_at)

    # 3. Find the latest attendance record started
    #    at or before the punch-out timestamp
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No matching punch-in record found",
        )

    # 4. Do not allow a second punch-out
    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance already punched out",
        )

    # 5. Validate punch-out time
    if ts <= record["punch_in"]:
        raise HTTPException(
            status_code=422,
            detail="punch_out must be after punch_in",
        )

    if ts > record["punch_in"] + timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="punch_out cannot be more than 24 hours after punch_in",
        )

    # 6. Recalculate attendance fields
    att_date = date.fromisoformat(record["date"])

    fields = derive_fields(
        record["punch_in"],
        ts,
        att_date,
        emp["shift_start"],
        emp["shift_end"],
    )

    # 7. Atomically close the attendance record
    updated = db.attendance_logs.find_one_and_update(
        {
            "emp_code": body.emp_code,
            "date": record["date"],
            "punch_in": record["punch_in"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                **fields,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        latest = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "date": record["date"],
            }
        )

        if latest is not None and latest.get("punch_out") is not None:
            raise HTTPException(
                status_code=409,
                detail="Attendance already punched out",
            )

        raise HTTPException(
            status_code=409,
            detail="Attendance record changed; please retry",
        )

    # 8. Return the serialized attendance record
    return serialize_record(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    # 1. Validate the date path parameter
    try:
        att_date = datetime.strptime(date, "%Y-%m-%d").date()
        if att_date.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must be a valid YYYY-MM-DD date",
        )

    # 2. Check employee and attendance record
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": att_date.isoformat(),
    })
    if record is None:
        raise HTTPException(404, detail="Attendance record not found")

    old_status = record.get("status")
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")
    old_history = record.get("history") or []

    new_status = body.status or old_status

    # 3. Resolve the final punch values
    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                422,
                detail="ABSENT/LEAVE cannot have punch times",
            )

        new_in = None
        new_out = None
        derived = {
            "late_minutes": 0,
            "overtime_minutes": 0,
            "work_hours": None,
            "half_day": False,
        }

    else:
        new_in = (
            ms_to_dt_trunc(body.punch_in)
            if body.punch_in is not None
            else old_in
        )
        new_out = (
            ms_to_dt_trunc(body.punch_out)
            if body.punch_out is not None
            else old_out
        )

        if new_in is None:
            raise HTTPException(
                422,
                detail="A presence status requires punch_in",
            )

        if (
            body.punch_in is not None
            and attendance_date(
                new_in, emp["shift_start"], emp["shift_end"]
            ) != att_date
        ):
            raise HTTPException(
                422,
                detail="punch_in must belong to the record's attendance date",
            )

        if new_out is not None:
            if new_out <= new_in:
                raise HTTPException(
                    422,
                    detail="punch_out must be after punch_in",
                )

            if new_out - new_in > timedelta(hours=24):
                raise HTTPException(
                    422,
                    detail="punch_out must be within 24 hours of punch_in",
                )

        derived = derive_fields(
            new_in,
            new_out,
            att_date,
            emp["shift_start"],
            emp["shift_end"],
        )

    # 4. Record only actual changes
    changes = {}

    if old_status != new_status:
        changes["status"] = {
            "from": old_status,
            "to": new_status,
        }

    if old_in != new_in:
        changes["punch_in"] = {
            "from": old_in,
            "to": new_in,
        }

    if old_out != new_out:
        changes["punch_out"] = {
            "from": old_out,
            "to": new_out,
        }

    if not changes:
        raise HTTPException(
            422,
            detail="Request changes nothing",
        )

    # 5. Create an audit-history entry
    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # 6. Update atomically only if the record has not changed
    query = {
        "emp_code": emp_code,
        "date": att_date.isoformat(),
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "$expr": {
            "$eq": [
                {
                    "$size": {
                        "$ifNull": ["$history", []]
                    }
                },
                len(old_history),
            ]
        },
    }

    updated = db.attendance_logs.find_one_and_update(
        query,
        {
            "$set": {
                "status": new_status,
                "punch_in": new_in,
                "punch_out": new_out,
                **derived,
            },
            "$push": {
                "history": history_entry,
            },
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            409,
            detail="Attendance changed concurrently; please retry",
        )

    # 7. Return the standard response format
    return serialize_record(updated)

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}

# ---------------------------------------------------------------------------
# STEP 9.1 - Monthly Employee Analytics
# ---------------------------------------------------------------------------

def analytics_month_bounds(month: str):
    try:
        if not isinstance(month, str) or len(month) != 7 or month[4] != "-":
            raise ValueError
        year, mon = map(int, month.split("-"))
        if mon < 1 or mon > 12:
            raise ValueError
        start = date(year, mon, 1)
        if mon == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, mon + 1, 1) - timedelta(days=1)
        return start, end
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )


def analytics_half_up(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return (2 * numerator + denominator) // (2 * denominator)


def clean_half_days(present_weight: int) -> int | float:
    return present_weight // 2 if present_weight % 2 == 0 else present_weight / 2.0


def build_monthly_employee_pipeline(emp_code, start, end, weekdays):
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": start.isoformat(),
                    "$lte": end.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_weight": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$in": ["$date", weekdays]},
                                ]
                            },
                            {
                                "$cond": [
                                    {
                                        "$eq": [
                                            {"$ifNull": ["$half_day", False]},
                                            True,
                                        ]
                                    },
                                    1,
                                    2,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def monthly_employee_analytics(
    emp_code: str,
    month: str = Query(..., description="Month in YYYY-MM format"),
):
    start, end = analytics_month_bounds(month)

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    joined_on = date.fromisoformat(employee["joined_on"])
    first_workday = max(start, joined_on)

    working_days = 0
    if first_workday <= end:
        current = first_workday
        while current <= end:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)

    weekdays = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            weekdays.append(current.isoformat())
        current += timedelta(days=1)

    pipeline = build_monthly_employee_pipeline(emp_code, start, end, weekdays)
    result = next(db.attendance_logs.aggregate(pipeline), {})

    p_weight = int(result.get("present_weight", 0))
    present_days = clean_half_days(p_weight)

    attendance_pct = None
    if working_days > 0:
        attendance_pct = (
            analytics_half_up(p_weight * 10000, working_days * 2) / 100.0
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": int(result.get("leave_days", 0)),
        "late_count": int(result.get("late_count", 0)),
        "total_late_minutes": int(result.get("total_late_minutes", 0)),
        "total_overtime_minutes": int(result.get("total_overtime_minutes", 0)),
        "attendance_pct": attendance_pct,
    }


# ---------------------------------------------------------------------------
# STEP 9.2 - Department Summary Analytics
# ---------------------------------------------------------------------------

def build_department_summary_pipeline(start, end, department=None):
    weekdays = []
    curr = start
    while curr <= end:
        if curr.weekday() < 5:
            weekdays.append(curr.isoformat())
        curr += timedelta(days=1)

    return [
        {
            "$match": {
                "joined_on": {"$lte": end.isoformat()},
                **({"department": department} if department else {}),
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", start.isoformat()]},
                                    {"$lte": ["$date", end.isoformat()]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code",
                },
                "present_weight": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$in": ["$logs.date", weekdays]},
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]},
                                    1,
                                    2,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_cents": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]},
                                ]
                            },
                            {"$round": [{"$multiply": ["$logs.work_hours", 100]}, 0]},
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "present_weight": {"$sum": "$present_weight"},
                "work_hours_cents": {"$sum": "$work_hours_cents"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
            }
        },
        {"$sort": {"_id": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary_analytics(
    month: str = Query(..., description="Month in YYYY-MM format"),
    department: Optional[str] = Query(None, description="Optional department filter"),
):
    start, end = analytics_month_bounds(month)
    pipeline = build_department_summary_pipeline(start, end, department)
    raw_results = list(db.employees.aggregate(pipeline))

    items = []
    for row in raw_results:
        wh_count = int(row.get("work_hours_count", 0))
        wh_cents = int(round(row.get("work_hours_cents", 0)))
        avg_wh = (
            analytics_half_up(wh_cents, wh_count) / 100.0
            if wh_count > 0
            else None
        )
        items.append({
            "department": row["_id"],
            "headcount": int(row["headcount"]),
            "present_days": clean_half_days(int(row.get("present_weight", 0))),
            "avg_work_hours": avg_wh,
            "late_count": int(row.get("late_count", 0)),
            "total_late_minutes": int(row.get("total_late_minutes", 0)),
            "leave_count": int(row.get("leave_count", 0)),
            "on_duty_count": int(row.get("on_duty_count", 0)),
        })

    return {
        "month": month,
        "items": items,
    }


# ---------------------------------------------------------------------------
# STEP 9.3 - Late Attendance Leaderboard
# ---------------------------------------------------------------------------

def build_late_leaderboard_pipeline(start, end, limit=10, department=None):
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start.isoformat(),
                    "$lte": end.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append({"$match": {"employee.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    return pipeline


@app.get("/analytics/leaderboard/late")
def late_attendance_leaderboard(
    month: str = Query(..., description="Month in YYYY-MM format"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = Query(None),
):
    start, end = analytics_month_bounds(month)
    pipeline = build_late_leaderboard_pipeline(start, end, limit, department)
    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": results,
    }


# ---------------------------------------------------------------------------
# STEP 9.4 - Department Attendance Trend
# ---------------------------------------------------------------------------

def build_department_trend_pipeline(
    employee_codes, joined_dates, from_date, to_date
):
    start_dt = datetime.combine(
        from_date, datetime.min.time(), tzinfo=timezone.utc
    )
    end_dt = datetime.combine(
        to_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )

    return [
        {
            "$match": {
                "emp_code": {"$in": employee_codes},
                "date": {
                    "$gte": from_date.isoformat(),
                    "$lte": to_date.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present_weight": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 1, 2]},
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$set": {
                "d": {
                    "$dateFromString": {
                        "dateString": "$_id",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$densify": {
                "field": "d",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt],
                },
            }
        },
        {
            "$set": {
                "present_weight": {"$ifNull": ["$present_weight", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "is_working_day": {"$lte": [{"$isoDayOfWeek": "$d"}, 5]},
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": {"$literal": joined_dates},
                            "as": "joined",
                            "cond": {
                                "$lte": [
                                    "$$joined",
                                    {
                                        "$dateToString": {
                                            "format": "%Y-%m-%d",
                                            "date": "$d",
                                        }
                                    },
                                ]
                            },
                        }
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$d",
                    }
                },
                "is_working_day": 1,
                "headcount": 1,
                "present_weight": 1,
                "late_count": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
):
    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (to_date - from_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    employees = list(
        db.employees.find(
            {"department": department},
            {"_id": 0, "emp_code": 1, "joined_on": 1},
        )
    )

    if not employees:
        raise HTTPException(status_code=404, detail="Department not found")

    employee_codes = [e["emp_code"] for e in employees]
    joined_dates = [e["joined_on"] for e in employees]

    pipeline = build_department_trend_pipeline(
        employee_codes, joined_dates, from_date, to_date
    )
    trend = list(db.attendance_logs.aggregate(pipeline))

    by_date = {}
    for row in trend:
        d_str = row["date"]
        p_weight = int(row.get("present_weight", 0))
        headcount = int(row["headcount"])
        working_day = bool(row["is_working_day"])
        
        if working_day and headcount > 0:
            rate_bp = analytics_half_up(p_weight * 10000, headcount * 2)
            att_rate: Optional[float] = rate_bp / 10000.0
        else:
            rate_bp = None
            att_rate = None

        by_date[d_str] = {
            "date": d_str,
            "is_working_day": working_day,
            "headcount": headcount,
            "present_count": clean_half_days(p_weight),
            "late_count": int(row.get("late_count", 0)),
            "attendance_rate": att_rate,
            "_rate_bp": rate_bp,
            "moving_avg_7d": None,
        }

    complete_trend = []
    current = from_date

    while current <= to_date:
        day = current.isoformat()
        if day in by_date:
            complete_trend.append(by_date[day])
        else:
            working_day = current.weekday() < 5
            headcount = sum(1 for joined in joined_dates if joined <= day)
            complete_trend.append({
                "date": day,
                "is_working_day": working_day,
                "headcount": headcount,
                "present_count": 0.0,
                "late_count": 0,
                "attendance_rate": 0.0 if working_day and headcount > 0 else None,
                "_rate_bp": 0 if working_day and headcount > 0 else None,
                "moving_avg_7d": None,
            })
        current += timedelta(days=1)

    
    for index, row in enumerate(complete_trend):
        window = complete_trend[max(0, index - 6):index + 1]
        rates = [
            item["_rate_bp"]
            for item in window
            if item.get("_rate_bp") is not None
        ]

        if rates:
            avg_bp = analytics_half_up(sum(rates), len(rates))
            row["moving_avg_7d"] = avg_bp / 10000.0
        else:
            row["moving_avg_7d"] = None

    for row in complete_trend:
        row.pop("_rate_bp", None)

    return {
        "department": department,
        "items": complete_trend,
    }

# ---------------------------------------------------------------------------
# STEP 10 - Admin Explain Endpoint
# ---------------------------------------------------------------------------

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    from_q: Optional[str] = Query(default=None, alias="from"),
    to_q: Optional[str] = Query(default=None, alias="to"),
    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    alias_map = {
        "attendance": "attendance_list",
        "attendance_list": "attendance_list",
        "monthly": "employee_monthly",
        "employee_monthly": "employee_monthly",
        "department-summary": "department_summary",
        "department_summary": "department_summary",
        "late-leaderboard": "late_leaderboard",
        "late_leaderboard": "late_leaderboard",
        "department-trend": "department_trend",
        "department_trend": "department_trend",
    }
    canonical = alias_map.get(endpoint)
    if not canonical:
        raise HTTPException(
            status_code=422,
            detail="Unknown explain endpoint",
        )

    try:
        if canonical == "attendance_list":
            for field, value in (
                ("date_from", date_from),
                ("date_to", date_to),
            ):
                if value is not None:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError(field)

            if date_from and date_to and date_from > date_to:
                raise HTTPException(
                    status_code=422,
                    detail="date_from must be on or before date_to",
                )

            query, sort, skip, page_limit, hint = build_attendance_find(
                emp_code=emp_code,
                date_from=date_from,
                date_to=date_to,
                status=status,
                page=page,
                page_size=page_size,
            )

            command = {
                "explain": {
                    "find": "attendance_logs",
                    "filter": query,
                    "sort": dict(sort),
                    "skip": skip,
                    "limit": page_limit,
                    "hint": hint,
                },
                "verbosity": "executionStats",
            }
            collection = "attendance_logs"

        elif canonical == "employee_monthly":
            if not month or not emp_code:
                raise HTTPException(
                    status_code=422,
                    detail="emp_code and month are required for employee_monthly explain",
                )
            start, end = analytics_month_bounds(month)
            weekdays = []
            current = start
            while current <= end:
                if current.weekday() < 5:
                    weekdays.append(current.isoformat())
                current += timedelta(days=1)

            pipeline = build_monthly_employee_pipeline(
                emp_code, start, end, weekdays
            )
            collection = "attendance_logs"
            command = {
                "explain": {
                    "aggregate": collection,
                    "pipeline": pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }

        elif canonical == "department_summary":
            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required for department_summary explain",
                )
            start, end = analytics_month_bounds(month)
            pipeline = build_department_summary_pipeline(
                start, end, department
            )
            collection = "employees"
            command = {
                "explain": {
                    "aggregate": collection,
                    "pipeline": pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }

        elif canonical == "late_leaderboard":
            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required for late_leaderboard explain",
                )
            start, end = analytics_month_bounds(month)
            pipeline = build_late_leaderboard_pipeline(
                start, end, limit, department
            )
            collection = "attendance_logs"
            command = {
                "explain": {
                    "aggregate": collection,
                    "pipeline": pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }

        else:  # department_trend
            eff_from = from_q or date_from
            eff_to = to_q or date_to
            if not department or not eff_from or not eff_to:
                raise HTTPException(
                    status_code=422,
                    detail="department, from, and to are required",
                )

            start_date = date.fromisoformat(eff_from)
            end_date = date.fromisoformat(eff_to)

            if end_date < start_date or (end_date - start_date).days + 1 > 92:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid date range",
                )

            employees = list(
                db.employees.find(
                    {"department": department},
                    {"_id": 0, "emp_code": 1, "joined_on": 1},
                )
            )
            pipeline = build_department_trend_pipeline(
                [e["emp_code"] for e in employees],
                [e["joined_on"] for e in employees],
                start_date,
                end_date,
            )
            collection = "attendance_logs"
            command = {
                "explain": {
                    "aggregate": collection,
                    "pipeline": pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }

        result = db.command(command)

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid date or month format",
        )
    except PyMongoError:
        log.exception("Could not explain database query")
        raise HTTPException(
            status_code=503,
            detail="Could not explain database query",
        )

    return {
        "endpoint": canonical,
        "collection": collection,
        "explain": json.loads(
            json_util.dumps(
                result,
                json_options=json_util.RELAXED_JSON_OPTIONS,
            )
        ),
    }
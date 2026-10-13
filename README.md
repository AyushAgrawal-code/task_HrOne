# Employee Attendance & Analytics API

A FastAPI and MongoDB application for employee attendance management, punch-in/punch-out, attendance regularization, and analytics.

## Prerequisites

- Python 3.11+
- MongoDB 6.0+

## Setup

Open PowerShell in the project directory and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configure the MongoDB connection using environment variables:

```powershell
$env:MONGO_URI="mongodb://localhost:27017"
$env:MONGO_DB="attendance_db"
```

Ensure MongoDB is running, then start the API:

```powershell
python -m uvicorn app.main:app --port 8000
```

## API Documentation

Once the server is running:

- **Swagger UI:** http://127.0.0.1:8000/docs
- **Health Check:** http://127.0.0.1:8000/health

## Notes

- MongoDB must be running before starting the application.
- Configure `MONGO_URI` and `MONGO_DB` for your environment.
- Do not commit `.env` files, credentials, or other secrets to the repository.

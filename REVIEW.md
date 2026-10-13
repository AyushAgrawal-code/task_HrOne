# REVIEW.md

## Defects found in the starter application

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | MongoDB configuration / `/health` | Health check can report success without verifying database connectivity. | Stop MongoDB and call `/health`. | Added a MongoDB ping and return an appropriate service-unavailable response on failure. |

| 2 | Employee creation / validation | Invalid employee fields and formats may be accepted. | Submit an invalid shift time such as `09:5` or a malformed date such as `banana`. | Added input validation for employee fields and their formats. |

| 3 | Employee creation / duplicate handling | Checking for an existing employee before insertion is vulnerable to concurrent requests. | Send simultaneous requests with the same `emp_code`. | Enforced uniqueness with a MongoDB unique index and handled duplicate-key errors. |

| 4 | Employee listing / pagination | The pagination offset is incorrect if calculated as `page * page_size`. | Request page 1 and compare the returned records with the first page. | Corrected the offset to `(page - 1) * page_size`. |

| 5 | Employee listing / department filter | The reported total may count all employees instead of only matching employees. | Filter by a department and compare `total` with the matching employee count. | Applied the same filters when calculating the total and fetching the page. |

| 6 | Punch-in endpoint | An unknown employee can cause an unhandled error when employee data is accessed. | Punch in using an unknown employee code. | Check employee existence and return `404` when the employee is missing. |

| 7 | Punch-in validation | Invalid attendance statuses and malformed or out-of-range timestamps may be accepted or cause server errors. | Try an unsupported status, a timestamp in seconds, or an excessively large timestamp. | Added request validation and return `422` for invalid input. |

| 8 | Punch-in / attendance serialization | Internal MongoDB IDs or incorrectly formatted timestamps can leak into API responses. | Inspect the JSON returned by punch-in and attendance-list endpoints. | Excluded internal IDs and serialized instants as epoch milliseconds. |

| 9 | Punch-in concurrency | Concurrent requests can create duplicate attendance records without atomic uniqueness enforcement. | Send multiple simultaneous punch-ins for the same employee and date. | Used a unique `(emp_code, date)` index and mapped duplicate-key conflicts to `409`. |

| 10 | Attendance listing | Invalid date ranges may be accepted, and fetching all matching records before sorting wastes memory. | Supply `date_from` later than `date_to`; test with a large dataset. | Validated date ranges and moved filtering, sorting and pagination into the database query. |

| 11 | Late-minute helper | Incorrect timezone handling or a non-strict grace-period comparison can calculate late minutes incorrectly. | Test a punch at `09:40:00` and `09:40:01` for a `09:30` shift. | Used timezone-aware timestamps and implemented the strict grace-period rule. |

| 12 | Rounding helper | Floating-point or ties-to-even rounding can produce incorrect business values. | Test a value such as `2.675`, which must round to `2.68` under the contract. | Used deterministic half-up rounding. |

| 13 | Overtime helper | Overtime can be counted before the required minimum duration, or calculated incorrectly for overnight shifts. | Test 12 minutes after shift end, then test an overnight shift. | Applied the minimum-overtime rule and corrected shift-end date handling. |

| 14 | Punch-out endpoint | A read-then-update sequence can allow simultaneous requests to close the same attendance record. | Send simultaneous punch-outs for the same open attendance record. | Used an atomic conditional update so only one request can close the record. |

| 15 | Regularization / PATCH | Replacing history from a previously read list can lose another request's changes under concurrency. | Submit simultaneous regularizations for the same attendance record. | Used conditional atomic updates and appended history entries without overwriting previous entries. |

## Checked and found acceptable

- The unique employee-code index prevents duplicate employee identifiers.
- The unique `(emp_code, date)` attendance index enforces one attendance record per employee per date.
- Derived attendance fields should be computed by the API and not trusted from client input.
- MongoDB indexes should be created idempotently at startup.
- Analytics must preserve the contract's treatment of employees with no attendance logs, weekend dates, ties and missing calendar dates.

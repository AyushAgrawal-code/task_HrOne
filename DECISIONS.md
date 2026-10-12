# DECISIONS.md

### 1. Indexes
I added a unique `(emp_code, date)` index to prevent duplicate daily attendance. `attendance_date_emp` supports date sorting, and `attendance_status_date_emp` supports status filtering. Explain tests confirmed index usage. I skipped a separate date-only index because `attendance_date_emp` already starts with `date`.

### 2. Punch-in race
Two simultaneous requests can both pass the existence check. The unique index allows only one insert; the other raises `DuplicateKeyError`, which I handle as HTTP 409.

### 3. Ties
I used `$rank` so employees tied at the cutoff share the same rank. Filtering with `rank <= limit` includes all tied employees, even if results exceed the limit.

### 4. Headcount
The summary starts from `employees`, then uses `$lookup` and `preserveNullAndEmptyArrays: true`. This keeps employees with no attendance logs in the department count.

### 5. At 100x data
I would benchmark realistic workloads, inspect explain statistics, and optimize the slowest aggregation stages before adding indexes.
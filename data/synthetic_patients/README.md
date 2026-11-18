# Synthetic patients

All patient and operational data in this project is **synthetic**. No real people are involved.

The data is loaded into PostgreSQL by the database init scripts, so it lives next to the schema:
[`backend/database/init/02_seed.sql`](../../backend/database/init/02_seed.sql).

| Patient | Story | Exercises |
|---|---|---|
| P1001 Rahul Sharma | Diabetic; knee replacement on 26 Nov 2026; 3 pre-op items pending; pre-authorisation pending | multi-step surgery readiness, eligibility, labs, rescheduling |
| P1002 Priya Nair | MRI brain on 24 Nov 2026; basic insurance plan | MRI procedure, partial coverage, exclusions |
| P1003 Arjun Mehta | Cardiac patient on warfarin + aspirin; insurance expired | drug interactions, expired policy, overdue bill |
| P1004 Sneha Kapoor | New policy (Aug 2026) | insurance waiting period |

Staff: `nurse.anita` (assigned P1001, P1004) and `nurse.deepak` (assigned P1003). Admins: `admin_01` (documents) and `sysadmin_01`.
"Today" in the story is 18 Nov 2026; tests freeze the clock to that date.

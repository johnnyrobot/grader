---
name: Privacy Violation
about: Report a network call that may leak student data — HIGHEST PRIORITY
title: "[PRIVACY] "
labels: privacy, urgent
---

**This is a privacy violation report.** If a network call goes to a host not in the
allowlist, or if student data appears in any outbound request, this is a FERPA
issue and must be fixed immediately.

**What host was contacted?**
The destination host/IP:

**What process made the call?**
The process name (from lsof output):

**When did it happen?**
During what operation (grading, import, citation check, etc.):

**Does the request contain student data?**
If you can, check whether the request body contains student names, submission text,
or grade data:

**Egress gate output**
```
Paste engine/gates/egress.py output here
```
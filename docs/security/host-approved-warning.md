# HostApproved warning

**当前命令运行于宿主环境，不具备 OS 级隔离。**

Choose host_approved only explicitly in an Agent draft, inspect its tools and publish a new version. The warning appears on the sandbox page and Agent editor. Approval grants one operation; it does not turn host execution into a secure sandbox. Disabled is the general-assistant default; the code-change template defaults to isolated and never falls back when Docker is unavailable. A private working directory and sanitized environment do not prevent an approved program from reading other host files. Use trusted code only in this mode.

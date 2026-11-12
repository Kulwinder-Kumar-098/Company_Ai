# Role-Based Structured Tool Permission Matrix

**Execution Timestamp:** 2026-11-12T09:51:35.660591Z  
**Endpoint:** `POST /tools/execute`  
**Tool Variants Tested:** 16 (covering 14 unique tools)  

## Tool × Role Authorization Matrix

| Tool Name | Test Args | Required Permission(s) | Admin | HR | Manager | Employee | Verdict |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `get_employee_details` | `{'employee_id': 'EMP001'}` | employees:read:all (any), employees:read:self (self only) | ALLOWED (200) | ALLOWED (200) | DENIED (403) | DENIED (403) | **PASS** |
| `get_employee_details` | `{'employee_id': 'EMP004'}` | employees:read:self | ALLOWED (200) | ALLOWED (200) | ALLOWED (200) | DENIED (403) | **PASS** |
| `search_employees` | `{'query': 'Aarav'}` | employees:read:all (all results) / employees:read:self (own only) | ALLOWED (200) | ALLOWED (200) | ALLOWED (200) | ALLOWED (200) | **PASS** |
| `get_employee_tasks` | `{'employee_id': 'EMP006'}` | tasks:read:assigned or projects:read:all | ALLOWED (200) | DENIED (403) | DENIED (403) | ALLOWED (200) | **PASS** |
| `get_employee_performance_metrics` | `{'employee_id': 'EMP006'}` | performance:read:self (self), :department (dept), :all (any) | ALLOWED (200) | ALLOWED (200) | DENIED (403) | ALLOWED (200) | **PASS** |
| `get_performance_metrics` | `{'department': 'Human Resources'}` | performance:read:all (any), :department (dept scoped) | ALLOWED (200) | ALLOWED (200) | DENIED (403) | DENIED (403) | **PASS** |
| `get_employee_performance_summary` | `{'employee_id': 'EMP006'}` | performance:read:self (self), :department (dept), :all (any) | ALLOWED (200) | ALLOWED (200) | DENIED (403) | ALLOWED (200) | **PASS** |
| `get_department_performance_summary` | `{'department': 'Finance'}` | performance:read:department (own dept only), :all (any) | ALLOWED (200) | DENIED (403) | DENIED (403) | DENIED (403) | **PASS** |
| `get_project_details` | `{'project_id': 'PRJ-DELTA-04'}` | projects:read:all (any), projects:read:assigned + visible | ALLOWED (200) | DENIED (403) | DENIED (403) | ALLOWED (200) | **PASS** |
| `get_project_details` | `{'project_id': 'PRJ-AI-VISION'}` | projects:read:all (any), projects:read:assigned + visible | ALLOWED (200) | DENIED (403) | ALLOWED (200) | ALLOWED (200) | **PASS** |
| `get_project_status` | `{'project_id': 'PRJ-DELTA-04'}` | projects:read:all or :assigned + visible | ALLOWED (200) | DENIED (403) | DENIED (403) | ALLOWED (200) | **PASS** |
| `get_project_tasks` | `{'project_id': 'PRJ-DELTA-04'}` | tasks:read:assigned, projects:read:assigned or :all | ALLOWED (200) | DENIED (403) | DENIED (403) | ALLOWED (200) | **PASS** |
| `get_project_team` | `{'project_id': 'PRJ-DELTA-04'}` | projects:read:all or :assigned + visible | ALLOWED (200) | DENIED (403) | DENIED (403) | ALLOWED (200) | **PASS** |
| `get_task_details` | `{'task_id': 'TSK-5031'}` | tasks:read:assigned, projects:read:assigned or :all | ALLOWED (200) | DENIED (403) | DENIED (403) | ALLOWED (200) | **PASS** |
| `search_tasks` | `{'query': 'Audit'}` | tasks:read:assigned or projects:read:all | ALLOWED (200) | DENIED (403) | ALLOWED (200) | ALLOWED (200) | **PASS** |
| `get_overdue_tasks` | `{}` | tasks:read:assigned or projects:read:all | ALLOWED (200) | DENIED (403) | ALLOWED (200) | ALLOWED (200) | **PASS** |

## Argument Validation & Security Boundary Checks

| Scenario | Expected HTTP | Actual HTTP | Status |
| :--- | :---: | :---: | :---: |
| Missing required argument (`get_project_details`, empty `{}`) | 422 | 422 | **PASS** |
| Unknown / unregistered tool name | 404 | 404 | **PASS** |
| Employee spoofing Admin ID via tool argument | 403 | 403 | **PASS** |

## Security Design Notes

- **Identity Anchor:** Actor is from JWT `current_user`, never from client-supplied arguments.
- **Email Redaction:** Employee emails stripped from LLM evidence unless query explicitly asks for contact info.
- **No Credential Exposure:** Password hashes, JWT secrets, and API keys are unreachable via any registered tool.
- **Audit Trail:** Every tool call (permitted or denied) is logged with actor ID, tool name, and outcome.

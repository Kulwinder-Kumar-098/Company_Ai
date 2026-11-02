# Agent planner evaluation

- Dataset version: 1
- Evaluation mode: live_groq
- Model quality evaluated: True
- Planner provider: groq
- Planner model: openai/gpt-oss-20b

| Category | Cases | Routing | Tool selection | Invalid plan | Invalid args | Unsupported | Unnecessary tools | Citation route | Planner failures | Execution failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Overall | 21 | 0.71 | 0.71 | 0.00 | 0.00 | 0.83 | 0.00 | 0.75 | 0.00 | N/A |
| ambiguous | 2 | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 0.00 | N/A | 0.00 | N/A |
| authorization_sensitive | 2 | 0.00 | 0.00 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| document_only | 2 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | 1.00 | 0.00 | N/A |
| employee_structured | 3 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| mixed | 2 | 0.50 | 0.50 | 0.00 | 0.00 | N/A | 0.00 | 0.50 | 0.00 | N/A |
| performance_structured | 2 | 0.50 | 0.00 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| project_structured | 2 | 0.50 | 0.50 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| task_structured | 2 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| unavailable_tool | 2 | 0.50 | 1.00 | 0.00 | 0.00 | 0.50 | 0.00 | N/A | 0.00 | N/A |
| unsupported | 2 | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 0.00 | N/A | 0.00 | N/A |

## Per-case planner decisions

| Case ID | Category | Status | Expected mode | Actual mode | Expected tools | Selected tools | Error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| doc-policy-001 | document_only | match | rag | rag |  |  | - |
| doc-benefits-002 | document_only | match | rag | rag |  |  | - |
| employee-self-001 | employee_structured | match | structured | structured | get_employee_details | get_employee_details | - |
| employee-search-002 | employee_structured | match | structured | structured | search_employees | search_employees | - |
| project-details-001 | project_structured | match | structured | structured | get_project_status | get_project_status | - |
| project-details-002 | project_structured | review | structured | unsupported | get_project_details |  | - |
| task-search-001 | task_structured | match | structured | structured | get_overdue_tasks | get_overdue_tasks | - |
| task-search-002 | task_structured | match | structured | structured | search_tasks | search_tasks | - |
| performance-self-001 | performance_structured | review | structured | structured | get_employee_performance_metrics | get_performance_metrics | - |
| performance-department-002 | performance_structured | review | structured | unsupported | get_department_performance_summary |  | - |
| mixed-policy-employee-001 | mixed | match | mixed | mixed | get_employee_details | get_employee_details | - |
| mixed-project-policy-002 | mixed | review | mixed | unsupported | get_project_status |  | - |
| unsupported-fiction-001 | unsupported | match | unsupported | unsupported |  |  | - |
| unsupported-private-002 | unsupported | match | unsupported | unsupported |  |  | - |
| ambiguous-task-001 | ambiguous | match | unsupported | unsupported |  |  | - |
| ambiguous-project-002 | ambiguous | match | unsupported | unsupported |  |  | - |
| unavailable-tool-001 | unavailable_tool | review | unsupported | rag |  |  | - |
| unavailable-tool-002 | unavailable_tool | match | unsupported | unsupported |  |  | - |
| auth-employee-other-001 | authorization_sensitive | review | structured | unsupported | get_employee_performance_metrics |  | - |
| auth-project-other-002 | authorization_sensitive | review | structured | unsupported | get_project_details |  | - |
| invalid-arguments-001 | employee_structured | match | structured | structured | get_employee_details | get_employee_details | - |

Not measured by planner-only evaluation; runtime tool/RAG failures are emitted as redacted telemetry.

Rates are proportions from 0 to 1. Citation route coverage measures whether cases expecting citations were routed through RAG; it does not judge citation correctness.

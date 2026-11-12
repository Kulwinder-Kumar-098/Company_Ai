# Agent planner evaluation

- Dataset version: 1
- Evaluation mode: live_groq
- Model quality evaluated: True
- Planner provider: groq
- Planner model: openai/gpt-oss-20b

| Category | Cases | Routing | Tool selection | Invalid plan | Invalid args | Unsupported | Unnecessary tools | Citation route | Planner failures | Execution failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Overall | 21 | 0.90 | 0.90 | 0.00 | 0.00 | 1.00 | 0.00 | 1.00 | 0.00 | N/A |
| ambiguous | 2 | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 0.00 | N/A | 0.00 | N/A |
| authorization_sensitive | 2 | 0.50 | 0.50 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| document_only | 2 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | 1.00 | 0.00 | N/A |
| employee_structured | 3 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| mixed | 2 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | 1.00 | 0.00 | N/A |
| performance_structured | 2 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| project_structured | 2 | 0.50 | 0.50 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| task_structured | 2 | 1.00 | 1.00 | 0.00 | 0.00 | N/A | 0.00 | N/A | 0.00 | N/A |
| unavailable_tool | 2 | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 0.00 | N/A | 0.00 | N/A |
| unsupported | 2 | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 0.00 | N/A | 0.00 | N/A |

## Per-case planner decisions

| Case ID | Category | Status | Expected mode | Actual mode | Expected tools | Selected tools | Error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| holdout-doc-remote-001 | document_only | match | rag | rag |  |  | - |
| holdout-doc-security-002 | document_only | match | rag | rag |  |  | - |
| holdout-employee-lookup-001 | employee_structured | match | structured | structured | get_employee_details | get_employee_details | - |
| holdout-employee-search-002 | employee_structured | match | structured | structured | search_employees | search_employees | - |
| holdout-employee-tasks-003 | employee_structured | match | structured | structured | get_employee_tasks | get_employee_tasks | - |
| holdout-project-status-001 | project_structured | match | structured | structured | get_project_status | get_project_status | - |
| holdout-project-team-002 | project_structured | review | structured | None | get_project_team |  | provider_bad_request |
| holdout-task-details-001 | task_structured | match | structured | structured | get_task_details | get_task_details | - |
| holdout-task-overdue-002 | task_structured | match | structured | structured | get_overdue_tasks | get_overdue_tasks | - |
| holdout-performance-self-001 | performance_structured | match | structured | structured | get_employee_performance_metrics | get_employee_performance_metrics | - |
| holdout-performance-summary-002 | performance_structured | match | structured | structured | get_employee_performance_summary | get_employee_performance_summary | - |
| holdout-mixed-leave-tasks-001 | mixed | match | mixed | mixed | get_overdue_tasks | get_overdue_tasks | - |
| holdout-mixed-project-policy-002 | mixed | match | mixed | mixed | get_project_status | get_project_status | - |
| holdout-unsupported-general-001 | unsupported | match | unsupported | unsupported |  |  | - |
| holdout-unsupported-recipe-002 | unsupported | match | unsupported | unsupported |  |  | - |
| holdout-ambiguous-vague-001 | ambiguous | match | unsupported | unsupported |  |  | - |
| holdout-ambiguous-pronoun-002 | ambiguous | match | unsupported | unsupported |  |  | - |
| holdout-unavailable-parking-001 | unavailable_tool | match | unsupported | unsupported |  |  | - |
| holdout-unavailable-hardware-002 | unavailable_tool | match | unsupported | unsupported |  |  | - |
| holdout-auth-employee-metrics-001 | authorization_sensitive | match | structured | structured | get_employee_performance_metrics | get_employee_performance_metrics | - |
| holdout-auth-unassigned-project-002 | authorization_sensitive | review | structured | None | get_project_team |  | provider_bad_request |

Not measured by planner-only evaluation; runtime tool/RAG failures are emitted as redacted telemetry.

Rates are proportions from 0 to 1. Citation route coverage measures whether cases expecting citations were routed through RAG; it does not judge citation correctness.

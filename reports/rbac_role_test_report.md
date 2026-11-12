# Role-Based Access Control (RBAC) — Role Test Report

**Execution Timestamp:** 2026-11-12T09:51:35.660591Z  
**Environment:** Isolated In-Memory SQLite + Deterministic Mock Planner (offline)  
**Bug Fix Applied:** `manager` role granted `employees:read:self` (was missing in seed)  

## Executive Summary

| Role | Test Account | Cases | Passed | Failed | Blocked | Pass Rate |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| Admin | `EMP***01` | 11 | 11 | 0 | 0 | 100.0% |
| HR | `EMP***02` | 11 | 11 | 0 | 0 | 100.0% |
| Manager | `EMP***04` | 11 | 11 | 0 | 0 | 100.0% |
| Employee | `EMP***06` | 11 | 11 | 0 | 0 | 100.0% |

---

## Admin (`EMP***01`) — 11/11 PASSED

| Test ID | Query / Operation | Expected | Actual Answer Snippet | Route | HTTP | Status | Notes |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| TC-ADM-01 | What is my role in the company? | AUTH | {"employee_id": "EMP001", "full_name": "Aarav Sharma", "departmen | `structured` | 200 | **PASS** |  |
| TC-ADM-02 | Show me my employee profile. | AUTH | {"employee_id": "EMP001", "full_name": "Aarav Sharma", "departmen | `structured` | 200 | **PASS** |  |
| TC-ADM-03 | Show me all employees in the company. | AUTH | [{"employee_id": "EMP001", "full_name": "Aarav Sharma", "departme | `structured` | 200 | **PASS** |  |
| TC-ADM-04 | Which projects are currently active? | AUTH | {"project_id": "PRJ-AI-VISION", "project_name": "Computer Vision  | `structured` | 200 | **PASS** |  |
| TC-ADM-05 | Show me project details for PRJ-AI-VISION. | AUTH | {"project_id": "PRJ-AI-VISION", "project_name": "Computer Vision  | `structured` | 200 | **PASS** |  |
| TC-ADM-06 | Show me department-wise performance summaries. | AUTH | {"metric_count": 52, "employee_count": 14, "goals_completed": 248 | `structured` | 200 | **PASS** |  |
| TC-ADM-07 | What is the company leave policy? | AUTH | Company policy states: Leave Policy: NovaTech provides 24 days pa | `rag` | 200 | **PASS** |  |
| TC-ADM-08 | Show me overdue tasks across the company. | AUTH | [{"task_id": "TSK-5015", "project_id": "PRJ-BETA-02", "task_name" | `structured` | 200 | **PASS** |  |
| TC-ADM-09 | Show me tasks assigned to me. | AUTH | [{"task_id": "TSK-5047", "project_id": "PRJ-OMEGA-05", "task_name | `structured` | 200 | **PASS** |  |
| TC-ADM-10 | What is the company work from home policy? | AUTH | Company policy states: Work from Home Policy: Up to 2 days remote | `rag` | 200 | **PASS** |  |
| TC-API-01 | Admin accesses agent metrics endpoint | 200 |  | `/internal/agent-metrics` | 200 | **PASS** |  |

## HR (`EMP***02`) — 11/11 PASSED

| Test ID | Query / Operation | Expected | Actual Answer Snippet | Route | HTTP | Status | Notes |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| TC-HR-01 | What is my role in the company? | AUTH | {"employee_id": "EMP002", "full_name": "Ananya Verma", "departmen | `structured` | 200 | **PASS** |  |
| TC-HR-02 | Show me my employee profile. | AUTH | {"employee_id": "EMP002", "full_name": "Ananya Verma", "departmen | `structured` | 200 | **PASS** |  |
| TC-HR-03 | Show me employee information in the Human Resources depart | AUTH | [] | `structured` | 200 | **PASS** |  |
| TC-HR-04 | Show me department-wise employee performance for Human Res | AUTH | {"metric_count": 4, "employee_count": 1, "goals_completed": 18, " | `structured` | 200 | **PASS** |  |
| TC-HR-05 | Show me the company leave policy. | AUTH | Company policy states: Leave Policy: NovaTech provides 24 days pa | `rag` | 200 | **PASS** |  |
| TC-HR-06 | What is the employee attendance policy? | AUTH | Company policy states: Attendance Policy: Standard office hours 9 | `rag` | 200 | **PASS** |  |
| TC-HR-07 | Show me employee performance metrics for my department. | AUTH | {"metric_count": 4, "employee_count": 1, "goals_completed": 18, " | `structured` | 200 | **PASS** |  |
| TC-HR-08 | Show me tasks assigned to me. | DENY | I can't provide an answer because you don't have permission to ac | `structured` | 200 | **PASS** |  |
| TC-HR-09 | Show me project details for PRJ-DELTA-04. | DENY | I can't provide an answer because you don't have permission to ac | `structured` | 200 | **PASS** |  |
| TC-HR-10 | Show me all employees in the company. | AUTH | [{"employee_id": "EMP001", "full_name": "Aarav Sharma", "departme | `structured` | 200 | **PASS** |  |
| TC-API-02 | HR blocked from agent metrics endpoint | 403 |  | `/internal/agent-metrics` | 403 | **PASS** |  |

## Manager (`EMP***04`) — 11/11 PASSED

| Test ID | Query / Operation | Expected | Actual Answer Snippet | Route | HTTP | Status | Notes |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| TC-MGR-01 | What is my role in the company? | AUTH | {"employee_id": "EMP004", "full_name": "Priya Rao", "department": | `structured` | 200 | **PASS** |  |
| TC-MGR-02 | Show me my employee profile. | AUTH | {"employee_id": "EMP004", "full_name": "Priya Rao", "department": | `structured` | 200 | **PASS** |  |
| TC-MGR-03 | What projects are assigned to me? | AUTH | [{"task_id": "TSK-5045", "project_id": "PRJ-OMEGA-05", "task_name | `structured` | 200 | **PASS** |  |
| TC-MGR-04 | Show me the status of my assigned projects. | AUTH | {"project_id": "PRJ-AI-VISION", "status": "in_progress", "complet | `structured` | 200 | **PASS** |  |
| TC-MGR-05 | Show me tasks assigned to me. | AUTH | [{"task_id": "TSK-5045", "project_id": "PRJ-OMEGA-05", "task_name | `structured` | 200 | **PASS** |  |
| TC-MGR-06 | Which tasks are overdue in the projects I can access? | AUTH | [{"task_id": "TSK-5055", "project_id": "PRJ-AI-VISION", "task_nam | `structured` | 200 | **PASS** |  |
| TC-MGR-07 | Show me my own performance metrics. | AUTH | [{"record_id": "PERF-2025-Q4-EMP004", "employee_id": "EMP004", "q | `structured` | 200 | **PASS** |  |
| TC-MGR-08 | What is the company work from home policy? | AUTH | Company policy states: Work from Home Policy: Up to 2 days remote | `rag` | 200 | **PASS** |  |
| TC-MGR-09 | Show me project details for PRJ-BETA-02. | DENY | I can't provide an answer because you don't have permission to ac | `structured` | 200 | **PASS** |  |
| TC-MGR-10 | Show me all employees in the company. | AUTH | [{"employee_id": "EMP004", "full_name": "Priya Rao", "department" | `structured` | 200 | **PASS** |  |
| TC-API-03 | Manager blocked from agent metrics endpoint | 403 |  | `/internal/agent-metrics` | 403 | **PASS** |  |

## Employee (`EMP***06`) — 11/11 PASSED

| Test ID | Query / Operation | Expected | Actual Answer Snippet | Route | HTTP | Status | Notes |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| TC-EMP-01 | What is my role in the company? | AUTH | {"employee_id": "EMP006", "full_name": "Neha Iyer", "department": | `structured` | 200 | **PASS** |  |
| TC-EMP-02 | Show me my employee profile. | AUTH | {"employee_id": "EMP006", "full_name": "Neha Iyer", "department": | `structured` | 200 | **PASS** |  |
| TC-EMP-03 | What projects are assigned to me? | AUTH | [{"task_id": "TSK-5031", "project_id": "PRJ-DELTA-04", "task_name | `structured` | 200 | **PASS** |  |
| TC-EMP-04 | Show me the status of my assigned tasks. | AUTH | [{"task_id": "TSK-5031", "project_id": "PRJ-DELTA-04", "task_name | `structured` | 200 | **PASS** |  |
| TC-EMP-05 | Show me my own performance metrics. | AUTH | [{"record_id": "PERF-2025-Q4-EMP006", "employee_id": "EMP006", "q | `structured` | 200 | **PASS** |  |
| TC-EMP-06 | Show me the company leave policy. | AUTH | Company policy states: Leave Policy: NovaTech provides 24 days pa | `rag` | 200 | **PASS** |  |
| TC-EMP-07 | What is the employee attendance policy? | AUTH | Company policy states: Attendance Policy: Standard office hours 9 | `rag` | 200 | **PASS** |  |
| TC-EMP-08 | Show me project details for PRJ-BETA-02. | DENY | I can't provide an answer because you don't have permission to ac | `structured` | 200 | **PASS** |  |
| TC-EMP-09 | Show me tasks assigned to me. | AUTH | [{"task_id": "TSK-5031", "project_id": "PRJ-DELTA-04", "task_name | `structured` | 200 | **PASS** |  |
| TC-EMP-10 | Show me project details for PRJ-DELTA-04. | AUTH | {"project_id": "PRJ-DELTA-04", "project_name": "ISO 27001 Securit | `structured` | 200 | **PASS** |  |
| TC-API-04 | Employee blocked from agent metrics endpoint | 403 |  | `/internal/agent-metrics` | 403 | **PASS** |  |


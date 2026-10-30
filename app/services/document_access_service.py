from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models.document import Document
from app.models.employee import Employee


def can_access_document(
    document: Document,
    employee: Employee,
) -> bool:
    if document.access_scope == "company":
        return True

    if document.access_scope == "department":
        return (
            employee.department is not None
            and document.department == employee.department
        )

    if document.access_scope == "private":
        return (
            document.uploaded_by_employee_id
            == employee.employee_id
        )

    return False


def accessible_document_conditions(employee: Employee) -> ColumnElement[bool]:
    """Build the SQL predicate equivalent to ``can_access_document``."""
    conditions: list[ColumnElement[bool]] = [
        Document.access_scope == "company",
        and_(
            Document.access_scope == "private",
            Document.uploaded_by_employee_id == employee.employee_id,
        ),
    ]
    if employee.department is not None:
        conditions.append(
            and_(
                Document.access_scope == "department",
                Document.department == employee.department,
            )
        )
    return or_(*conditions)
from types import SimpleNamespace

from app.services.document_access_service import can_access_document


def test_company_document_is_accessible():
    document = SimpleNamespace(
        access_scope="company",
        department=None,
        uploaded_by_employee_id="ADMIN001",
    )

    employee = SimpleNamespace(
        employee_id="EMP001",
        department="Engineering",
    )

    assert can_access_document(document, employee) is True


def test_department_document_same_department():
    document = SimpleNamespace(
        access_scope="department",
        department="Engineering",
        uploaded_by_employee_id="MANAGER001",
    )

    employee = SimpleNamespace(
        employee_id="EMP001",
        department="Engineering",
    )

    assert can_access_document(document, employee) is True


def test_department_document_different_department():
    document = SimpleNamespace(
        access_scope="department",
        department="Engineering",
        uploaded_by_employee_id="MANAGER001",
    )

    employee = SimpleNamespace(
        employee_id="EMP002",
        department="HR",
    )

    assert can_access_document(document, employee) is False


def test_private_document_only_uploader_can_access():
    document = SimpleNamespace(
        access_scope="private",
        department=None,
        uploaded_by_employee_id="EMP001",
    )

    uploader = SimpleNamespace(
        employee_id="EMP001",
        department="Engineering",
    )

    another_employee = SimpleNamespace(
        employee_id="EMP002",
        department="Engineering",
    )

    assert can_access_document(document, uploader) is True
    assert can_access_document(document, another_employee) is False
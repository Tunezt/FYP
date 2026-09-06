from app.ai import tools


EXPECTED_TOOLS = {
    "get_stock",
    "get_sales_summary",
    "compare_periods",
    "get_profit",
    "correct_stock",
    "get_low_stock",
    "record_expense",
    # M9-T4
    "get_purchase_history",
    "get_supplier_prices",
    "get_recipe_cost",
    "get_shift_summary",
    "get_customer_summary",
    "get_promo_performance",
    "draft_purchase_order",
}


def test_registry_contains_expected_fixed_tool_set():
    names = {d.name for d in tools.TOOL_DECLARATIONS}
    assert EXPECTED_TOOLS <= names


def test_no_attendance_tool():
    # Attendance is a deliberate, documented omission (PROJECT_BRIEF scope).
    assert not any("attendance" in d.name for d in tools.TOOL_DECLARATIONS)


def test_every_declaration_has_an_executor_and_unique_name():
    names = [d.name for d in tools.TOOL_DECLARATIONS]
    assert len(names) == len(set(names))
    for name in names:
        assert name in tools.TOOL_EXECUTORS


def test_declarations_have_descriptions():
    for d in tools.TOOL_DECLARATIONS:
        assert d.description and len(d.description) > 20

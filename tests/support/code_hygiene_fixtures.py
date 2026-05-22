"""Synthetic source snippets used by code hygiene tests."""

SERVICE_NESTED_LOOP = '''"""Service-layer nested loop fixture."""

def count_pairs(items):
    """Count all ordered pairs.

    Args:
        items: Numeric items to combine.

    Returns:
        Sum of every ordered pair.
    """
    total = 0
    for left in items:
        for right in items:
            total += left + right
    return total
'''

DOCUMENTED_NESTED_LOOP = '''"""Algorithmic pair-counting helpers."""

def count_pairs(items):
    """Count all pairs.

    Args:
        items: Numeric items to combine.

    Returns:
        Sum of every ordered pair.

    Time complexity: O(n^2).
    """
    total = 0
    for left in items:
        for right in items:
            total += left + right
    return total
'''

UNDOCUMENTED_NESTED_LOOP = '''"""Algorithmic pair-counting helpers."""

def count_pairs(items):
    """Count all pairs.

    Args:
        items: Numeric items to combine.

    Returns:
        Sum of every ordered pair.
    """
    total = 0
    for left in items:
        for right in items:
            total += left + right
    return total
'''

MISSING_MODULE_DOCSTRING = '''
def find_positive(items):
    """Find the first positive item.

    Args:
        items: Numeric items to inspect.

    Returns:
        The first positive item, or None when no item is positive.
    """
    for item in items:
        if item > 0:
            return item
    return None
'''

MISSING_FUNCTION_DOCSTRING = '''"""Missing public function docstring fixture."""

def find_positive(items):
    for item in items:
        if item > 0:
            return item
    return None
'''

INCOMPLETE_FUNCTION_DOCSTRING = '''"""Incomplete function docstring fixture."""

def allocate_order(order):
    """Allocate an order."""
    if order is None:
        raise ValueError("order is required")
    return order
'''

MISSING_CLASS_DOCSTRING = '''"""Missing public class docstring fixture."""

class Allocator:
    def allocate(self, order):
        """Return an already validated order.

        Args:
            order: Order to return.

        Returns:
            The input order.
        """
        return order
'''

MISSING_METHOD_DOCSTRING = '''"""Missing public method docstring fixture."""

class Allocator:
    """Allocate already validated orders."""

    def allocate(self, order):
        return order
'''

CLEAN_CODE = '''"""Clean single-pass search helper."""

def find_positive(items):
    """Find the first positive item.

    Args:
        items: Numeric items to inspect.

    Returns:
        The first positive item, or None when no item is positive.
    """
    for item in items:
        if item > 0:
            return item
    return None
'''

FLAT_ELIF_CHAIN = '''"""Flat branch fixture."""

def label(value):
    """Return a small label for a numeric value.

    Args:
        value: Numeric value to label.

    Returns:
        Human-readable value label.
    """
    if value == 1:
        return "one"
    elif value == 2:
        return "two"
    elif value == 3:
        return "three"
    return "other"
'''

DEEP_IF_NESTING = '''"""Deep nesting fixture."""

def is_ready(value):
    """Check whether a value is ready.

    Args:
        value: Object with readiness flags.

    Returns:
        True when all readiness flags are set.
    """
    if value:
        if value.enabled:
            if value.ready:
                return True
    return False
'''

BARE_EXCEPT = '''"""Bare except fixture."""

def parse_int(value):
    """Parse an integer value.

    Args:
        value: Text value to parse.

    Returns:
        Parsed integer, or None when parsing fails.
    """
    try:
        return int(value)
    except:
        return None
'''

EMPTY_EXCEPT_PASS = '''"""Empty except pass fixture."""

def parse_int(value):
    """Parse an integer value.

    Args:
        value: Text value to parse.

    Returns:
        Parsed integer, or None when parsing fails.
    """
    parsed = None
    try:
        parsed = int(value)
    except ValueError:
        pass
    return parsed
'''

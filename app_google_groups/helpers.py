from typing import Dict


def dict_drop_blanks(indict: Dict[str, any]) -> Dict[str, any]:
    """
    Simple dictionary comprehension that drops falsey values
    """
    return {k: v for k, v in indict.items() if v}

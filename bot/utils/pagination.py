import math


def paginate(items: list, page: int, page_size: int) -> tuple[list, int]:
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    total = max(1, math.ceil(len(items) / page_size))
    page = min(max(1, page), total)
    return items[(page - 1) * page_size: page * page_size], total

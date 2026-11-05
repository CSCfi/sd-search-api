import pytest

from search_api.api.opensearch.search import take_page


async def _items(read: list[int], count: int):
    """Yield 0 to count - 1, recording each item read."""
    for item in range(count):
        read.append(item)
        yield item


@pytest.mark.asyncio
async def test_take_page_with_offset_and_limit():
    read: list[int] = []
    assert await take_page(_items(read, 10), offset=2, limit=3) == [2, 3, 4]
    assert read == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_take_page_with_offset_and_limit_zero():
    """A limit of 0 returns every item after offset."""
    assert await take_page(_items([], 4), offset=1, limit=0) == [1, 2, 3]

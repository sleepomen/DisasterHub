from services.rate_limit import RateLimiter


def test_allows_up_to_the_limit_then_blocks():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    assert [limiter.hit("a", now=1000 + i) for i in range(3)] == [0, 0, 0]
    # 第 4 次要等到第 1 次離開視窗（1000 + 60）才有名額
    assert limiter.hit("a", now=1003) == 57


def test_window_slides():
    limiter = RateLimiter(max_requests=2, window_seconds=10)
    assert limiter.hit("a", now=100) == 0
    assert limiter.hit("a", now=101) == 0
    assert limiter.hit("a", now=105) > 0
    # now=111 時 now=100 那一次已經出了視窗
    assert limiter.hit("a", now=111) == 0


def test_blocked_requests_do_not_extend_the_window():
    # 猛打的來源要等得到解除，不然就變成永久封鎖
    limiter = RateLimiter(max_requests=1, window_seconds=10)
    assert limiter.hit("a", now=100) == 0
    for t in range(101, 110):
        assert limiter.hit("a", now=t) > 0
    assert limiter.hit("a", now=110) == 0


def test_sources_are_counted_separately():
    limiter = RateLimiter(max_requests=1, window_seconds=10)
    assert limiter.hit("a", now=100) == 0
    assert limiter.hit("b", now=100) == 0
    assert limiter.hit("a", now=100) > 0


def test_zero_limit_disables_the_limiter():
    limiter = RateLimiter(max_requests=0, window_seconds=60)
    assert limiter.enabled is False
    assert [limiter.hit("a", now=100) for _ in range(50)] == [0] * 50
    assert limiter.tracked() == 0


def test_idle_sources_are_pruned():
    limiter = RateLimiter(max_requests=5, window_seconds=10)
    limiter.hit("old", now=100)
    assert limiter.tracked() == 1
    # 整個視窗沒動靜的來源會在下一次記錄時被清掉，表的大小只跟最近一個視窗有關
    limiter.hit("new", now=200)
    assert limiter.tracked() == 1


def test_tracking_table_has_a_hard_cap():
    limiter = RateLimiter(max_requests=5, window_seconds=600, max_tracked=3)
    for i in range(10):
        limiter.hit(f"src-{i}", now=1000 + i)
    assert limiter.tracked() == 3


def test_reset_clears_one_source():
    limiter = RateLimiter(max_requests=1, window_seconds=10)
    assert limiter.hit("a", now=100) == 0
    assert limiter.hit("a", now=101) > 0
    limiter.reset("a")
    assert limiter.hit("a", now=102) == 0

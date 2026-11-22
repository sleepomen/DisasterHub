from repositories.db import Database


class AssignmentQueries(Database):
    """疏散分配：在資料庫裡解最小成本最大流"""

    # 疏散分配的最小成本最大流（pgRouting 3.4 的 pgr_maxFlowMinCost，Boost 的 successive shortest path）。
    # 節點：1..m 是起點、m+1..m+n 是避難所、m+n+1 源點、m+n+2 匯點。
    # 成本用整數（0.1 秒為單位）：用浮點數時 Boost 的位能累積誤差會算出 -1e-12 的邊，直接報
    # 「The graph may not contain an edge with negative weight」。pair_costs 本來就四捨五入到 0.1 秒，換算沒有誤差
    MIN_COST_FLOW_SQL = """
        SELECT edge, flow FROM pgr_maxFlowMinCost(
            'SELECT id, source, target, capacity, cost FROM assignment_edges', %s::bigint, %s::bigint
        ) WHERE flow > 0
    """

    def assignment_flows(
        self, supply: list[int], capacity: list[int], cost: dict[tuple[int, int], float],
    ) -> dict[tuple[int, int], int]:
        """
        跟 services/simulation/min_cost_flow.min_cost_flow 同樣的輸入與輸出，改在資料庫裡解：
        先安置最多的人，同樣人數下總成本最小。回傳 {(起點序號, 避難所序號): 人數}
        """
        m, n = len(supply), len(capacity)
        source, sink = m + n + 1, m + n + 2
        rows, pairs = [], {}
        for i, people in enumerate(supply):
            if people > 0:
                rows.append((source, i + 1, people, 0))
        for j, room in enumerate(capacity):
            if room > 0:
                rows.append((m + 1 + j, sink, room, 0))
        for (i, j), c in cost.items():
            if supply[i] > 0 and capacity[j] > 0:
                pairs[len(rows) + 1] = (i, j)
                rows.append((i + 1, m + 1 + j, min(supply[i], capacity[j]), round(c * 10)))
        if not pairs:
            return {}
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "CREATE TEMP TABLE assignment_edges "
                    "(id bigint, source bigint, target bigint, capacity bigint, cost bigint) ON COMMIT DROP"
                )
                cursor.execute(
                    "INSERT INTO assignment_edges SELECT * FROM unnest("
                    "%s::bigint[], %s::bigint[], %s::bigint[], %s::bigint[], %s::bigint[])",
                    (list(range(1, len(rows) + 1)), *(list(col) for col in zip(*rows, strict=True))),
                )
                cursor.execute(self.MIN_COST_FLOW_SQL, (source, sink))
                result = cursor.fetchall()
        except Exception as e:
            raise RuntimeError(f"assignment_flows 失敗：{e}") from e
        return {pairs[edge]: int(flow) for edge, flow in result if edge in pairs}

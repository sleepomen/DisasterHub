"""
疏散的運輸問題：每個鄉鎮有一群人、每間避難所有剩餘空位、每一組（鄉鎮, 避難所）有通行時間，
在守住空位的前提下安置最多的人，並讓所有人的通行時間加總最小（min-cost max-flow）。

不另外裝套件：規模很小（鄉鎮最多幾十個、避難所幾百間），用 successive shortest path
＋ Dijkstra（Johnson 位能讓殘餘圖上的成本保持非負）就夠快。每次擴充至少讓一條邊飽和，
擴充次數大約是鄉鎮數＋避難所數
"""
import heapq

INF = float("inf")


class _Graph:
    def __init__(self, n: int):
        self.adj: list[list[int]] = [[] for _ in range(n)]
        self.to: list[int] = []
        self.cap: list[int] = []
        self.cost: list[float] = []

    def add(self, u: int, v: int, cap: int, cost: float) -> int:
        """加一條 u → v 的邊和它的反向殘餘邊；回傳正向邊的編號（反向邊是編號 ^ 1）"""
        for a, b, c, w in ((u, v, cap, cost), (v, u, 0, -cost)):
            self.adj[a].append(len(self.to))
            self.to.append(b)
            self.cap.append(c)
            self.cost.append(w)
        return len(self.to) - 2


def min_cost_flow(supply: list[int], capacity: list[int], cost: dict[tuple[int, int], float]) -> dict[tuple[int, int], int]:
    """
    supply[i]：第 i 個起點的人數；capacity[j]：第 j 間避難所的空位；cost[(i, j)]：每人從 i 到 j 的成本
    （沒有的配對就是不能走）。回傳 {(i, j): 人數}，只列有人的配對。
    先盡量多安置，同樣人數下總成本最小
    """
    m, n = len(supply), len(capacity)
    source, sink = m + n, m + n + 1
    g = _Graph(m + n + 2)
    for i, people in enumerate(supply):
        if people > 0:
            g.add(source, i, people, 0.0)
    for j, room in enumerate(capacity):
        if room > 0:
            g.add(m + j, sink, room, 0.0)
    pair_edge = {(i, j): g.add(i, m + j, min(supply[i], capacity[j]), c)
                 for (i, j), c in cost.items() if supply[i] > 0 and capacity[j] > 0}

    potential = [0.0] * (m + n + 2)
    while True:
        dist = [INF] * (m + n + 2)
        via = [-1] * (m + n + 2)
        dist[source] = 0.0
        heap = [(0.0, source)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            for e in g.adj[u]:
                if g.cap[e] <= 0:
                    continue
                v = g.to[e]
                # 浮點誤差可能讓 reduced cost 變成 -1e-12，夾回 0，Dijkstra 才成立
                nd = d + max(0.0, g.cost[e] + potential[u] - potential[v])
                if nd < dist[v]:
                    dist[v] = nd
                    via[v] = e
                    heapq.heappush(heap, (nd, v))
        if dist[sink] == INF:
            break
        for v in range(m + n + 2):
            if dist[v] < INF:
                potential[v] += dist[v]
        push, v = INF, sink
        while v != source:
            e = via[v]
            push = min(push, g.cap[e])
            v = g.to[e ^ 1]
        v = sink
        while v != source:
            e = via[v]
            g.cap[e] -= push
            g.cap[e ^ 1] += push
            v = g.to[e ^ 1]

    return {pair: g.cap[e ^ 1] for pair, e in pair_edge.items() if g.cap[e ^ 1] > 0}

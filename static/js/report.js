// 容量缺口報告頁：讀 /api/capacity_report，畫成可以排序、依縣篩選的表格。
// 重點與核對清單的文字由後端產生（services/simulation/capacity_report.py），跟 reports/capacity_gap.md 是同一份
(function () {
    'use strict';

    const FACTORS = ['0.8', '1.0', '1.2'];
    const LOW_SHARE = 0.05;
    const KIND_LABEL = { earthquake: '強震', flood: '淹水' };
    const state = { county: '', report: null, tables: [] };

    const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('zh-TW'));
    const pct = (x) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`);

    function el(tag, attrs, ...children) {
        const node = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (k === 'class') node.className = v;
            else if (k === 'text') node.textContent = v;
            else node.setAttribute(k, v);
        }
        for (const c of children) if (c != null) node.append(c);
        return node;
    }

    // 後端的文字用 **粗體**；不用 innerHTML，資料裡的字都當成純文字
    function richText(text) {
        const frag = document.createDocumentFragment();
        text.split('**').forEach((part, i) => frag.append(i % 2 ? el('strong', { text: part }) : part));
        return frag;
    }

    function fillList(id, items) {
        const list = document.getElementById(id);
        list.replaceChildren(...items.map((t) => el('li', {}, richText(t))));
    }

    // ── 可以排序的表格 ─────────────────────────────────
    // columns: [{label, num, sort(row) → 排序值, cell(row) → 文字或節點}]
    function sortableTable(table, columns, getRows, initial) {
        const sort = { index: initial ? initial[0] : -1, dir: initial ? initial[1] : 1 };
        const heads = columns.map((col, i) => {
            const th = el('th', { scope: 'col', class: col.num ? 'num' : '' });
            if (col.sort) {
                const btn = el('button', { type: 'button', text: col.label });
                btn.addEventListener('click', () => {
                    sort.dir = sort.index === i ? -sort.dir : (col.num ? -1 : 1);
                    sort.index = i;
                    render();
                });
                th.append(btn);
            } else {
                th.textContent = col.label;
            }
            return th;
        });
        const tbody = el('tbody');
        table.replaceChildren(el('thead', {}, el('tr', {}, ...heads)), tbody);

        function render() {
            heads.forEach((th, i) => {
                if (i === sort.index) th.setAttribute('aria-sort', sort.dir > 0 ? 'ascending' : 'descending');
                else th.removeAttribute('aria-sort');
            });
            const rows = getRows().slice();
            if (sort.index >= 0) {
                const key = columns[sort.index].sort;
                rows.sort((a, b) => {
                    const x = key(a), y = key(b);
                    if (x === y) return 0;
                    if (x == null) return 1;
                    if (y == null) return -1;
                    return (x < y ? -1 : 1) * sort.dir;
                });
            }
            tbody.replaceChildren(...rows.map((row) => el('tr', {}, ...columns.map((col) => {
                const value = col.cell(row);
                return el('td', { class: col.num ? 'num' : '' }, value == null ? '—' : value);
            }))));
            if (!rows.length) tbody.append(el('tr', {}, el('td', { colspan: String(columns.length), text: '沒有資料' })));
        }
        render();
        return render;
    }

    function byCounty(rows) {
        return state.county ? rows.filter((r) => r.county.replace('台', '臺') === state.county) : rows;
    }

    // 占人口幾 %：長條以疏散比例為滿格，看得出離「收得下」還差多少
    function shareCell(hazard, ratio) {
        const share = hazard.share;
        const cls = share === 0 ? 'is-zero is-critical' : share < LOW_SHARE ? 'is-critical' : share < ratio ? 'is-low' : '';
        const width = share == null ? 0 : Math.min(1, share / ratio) * 100;
        return el('span', { class: `share ${cls}`, title: `疏散比例 ${pct(ratio)} 為滿格` },
            el('span', { class: 'share-text', text: `${fmt(hazard.capacity)}（${pct(share)}）` }),
            el('span', { class: 'share-bar', 'aria-hidden': 'true' }, el('span', { style: `width:${width}%` })));
    }

    function unplacedCell(sc) {
        if (!sc) return null;
        const [low, mid, high] = FACTORS.map((f) => sc.unplaced[f]);
        if (high === 0) return '0';
        const out = el('span', {});
        if (!sc.destinations) out.append(el('span', { class: 'is-danger', text: '全部 ' }));
        out.append(el('strong', { text: fmt(mid) }), ' ', el('span', { class: 'range', text: `${fmt(low)}～${fmt(high)}` }));
        return out;
    }

    function maxCell(sc) {
        if (!sc) return null;
        if (!sc.destinations) return el('span', { class: 'is-danger', text: '0%（沒有能去的避難所）' });
        return pct(sc.max_ratio);
    }

    function render(report) {
        const meta = report.meta;
        const ratios = meta.evacuation_ratios;
        document.getElementById('report-generated').textContent =
            `產生時間 ${meta.generated_at}。同樣的資料一定得到同樣的報告。`;
        fillList('highlights', report.highlights);
        fillList('checks', report.checks);
        fillList('limitations', report.limitations);

        document.getElementById('towns-lead').textContent =
            '只用資料，沒有假設：鄉鎮內的登記容量占鄉鎮人口幾 %。強震只算列為震災適用的避難所，淹水只算列為水災適用、' +
            `而且不在土石流潛勢範圍內的。長條以疏散比例（強震 ${pct(ratios.earthquake)}、淹水 ${pct(ratios.flood)}）為滿格，` +
            `琥珀色是不到疏散比例，紅色是不到 ${pct(LOW_SHARE)}。`;
        document.getElementById('scenarios-lead').textContent =
            `每個鄉鎮以人口重心為中心、半徑 ${meta.radius_km} 公里，跟地圖上的模擬同一套算法（沿路網步行、最小成本流），避難所當成空的。` +
            '圈內受影響人口包含鄰近鄉鎮。未安置的粗體是基準疏散比例的人數，旁邊是比例 ×0.8～×1.2 的範圍。' +
            '「最多安置」是所有受影響的人都疏散時能安置的比例，不受疏散比例的假設影響。' +
            '紅色警戒是這個鄉鎮所有土石流潛勢溪流同時發布紅色警戒，範圍內的道路封閉。';

        // 各鄉鎮自有容量
        const towns = report.townships;
        const renderTowns = sortableTable(document.getElementById('towns-table'), [
            { label: '縣', sort: (t) => t.county, cell: (t) => t.county },
            { label: '鄉鎮', sort: (t) => t.township, cell: (t) => t.township },
            { label: '人口', num: true, sort: (t) => t.population, cell: (t) => fmt(t.population) },
            { label: '避難所', num: true, sort: (t) => t.shelters, cell: (t) => fmt(t.shelters) },
            { label: '容量', num: true, sort: (t) => t.capacity, cell: (t) => fmt(t.capacity) },
            { label: '強震可用', num: true, sort: (t) => t.hazards.earthquake.share,
              cell: (t) => shareCell(t.hazards.earthquake, ratios.earthquake) },
            { label: '淹水可用', num: true, sort: (t) => t.hazards.flood.share,
              cell: (t) => shareCell(t.hazards.flood, ratios.flood) },
            { label: '適合避難弱者', num: true, sort: (t) => t.vulnerable_capacity, cell: (t) => fmt(t.vulnerable_capacity) },
        ], () => byCounty(towns));

        // 情境模擬：一個鄉鎮一列
        const runs = {};
        for (const sc of report.scenarios) (runs[sc.township] = runs[sc.township] || {})[sc.kind] = sc;
        const rows = towns.map((t) => ({ county: t.county, township: t.township, ...(runs[t.township] || {}) }));
        const covered = (r) => (r.earthquake || r.flood || {}).covered_population;
        const cutPeople = (r) => (r.flood_red ? r.flood_red.cut_off.reduce((s, c) => s + c.population, 0) : null);
        const kindColumns = (kind) => [
            { label: `${KIND_LABEL[kind]}未安置`, num: true, sort: (r) => (r[kind] ? r[kind].unplaced['1.0'] : null),
              cell: (r) => unplacedCell(r[kind]) },
            { label: `${KIND_LABEL[kind]}最多安置`, num: true, sort: (r) => (r[kind] ? r[kind].max_ratio : null),
              cell: (r) => maxCell(r[kind]) },
        ];
        const renderScenarios = sortableTable(document.getElementById('scenarios-table'), [
            { label: '中心鄉鎮', sort: (r) => r.township, cell: (r) => r.township },
            { label: '圈內受影響人口', num: true, sort: covered, cell: (r) => fmt(covered(r)) },
            ...kindColumns('earthquake'),
            ...kindColumns('flood'),
            { label: '紅色警戒被切斷', num: true, sort: cutPeople, cell: (r) => {
                if (!r.flood_red) return el('span', { class: 'range', text: '無潛勢溪流' });
                const cut = r.flood_red.cut_off;
                return cut.length ? `${cut.length} 村里，${fmt(cutPeople(r))} 人` : '0';
            } },
        ], () => byCounty(rows), [4, -1]);

        sortableTable(document.getElementById('cut-table'), [
            { label: '警戒鄉鎮', sort: (r) => r.township, cell: (r) => r.township },
            { label: '村里', sort: (r) => r.village, cell: (r) => r.village },
            { label: '受影響人口', num: true, sort: (r) => r.population, cell: (r) => fmt(r.population) },
        ], () => report.cut_off, [2, -1]);

        const lead = document.getElementById('shelters-lead');
        lead.replaceChildren(`看強震與淹水共 ${report.main_scenarios} 個情境的基準分配。`);
        if (report.busiest.length) {
            lead.append(el('br'), el('strong', { text: '最常滿載：' }),
                report.busiest.map((b) => `${b.name}（${b.full} 個情境）`).join('、') + '。');
        }
        if (report.never_used.length) {
            lead.append(el('br'), el('strong', { text: `是目的地卻從來沒分到人（${report.never_used.length} 間）：` }),
                report.never_used.join('、') + '。通常是附近的需求已經被更近的避難所接走，不一定代表它沒有用。');
        }

        const data = [
            ['避難所', `消防署「避難收容處所點位檔」${meta.shelter_dataset_updated} 版，${fmt(meta.shelters)} 處`],
            ['人口', `內政部戶籍人口（${meta.population_month}），${fmt(meta.villages)} 個村里`],
            ['道路', `OpenStreetMap，匯入 ${meta.roads_loaded}`],
            ['土石流潛勢', `農業部 1753 條土石流潛勢溪流影響範圍，${fmt(meta.debris_zones)} 個在宜花東`],
        ];
        document.getElementById('data-table').replaceChildren(el('tbody', {},
            ...data.map(([k, v]) => el('tr', {}, el('th', { scope: 'row', text: k }), el('td', { text: v })))));

        // 依縣篩選，兩張表一起
        const counties = ['', ...new Set(towns.map((t) => t.county.replace('台', '臺')))];
        const filter = document.getElementById('county-filter');
        const buttons = counties.map((c) => {
            const btn = el('button', { type: 'button', text: c || '全部', 'aria-pressed': String(c === state.county) });
            btn.addEventListener('click', () => {
                state.county = c;
                buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(counties[i] === c)));
                renderTowns();
                renderScenarios();
            });
            return btn;
        });
        filter.replaceChildren(...buttons);
    }

    async function load() {
        const status = document.getElementById('report-status');
        try {
            const res = await fetch('/api/capacity_report');
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            state.report = body;
            render(body);
            status.hidden = true;
            document.getElementById('report-body').hidden = false;
        } catch (err) {
            status.textContent = `報告載入失敗：${err.message}`;
            status.classList.add('is-error');
        }
    }

    load();
}());

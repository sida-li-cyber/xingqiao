/**
 * 协议 3.1 解码纯函数单测（node --test tests/frontend/）。
 *
 * 被测对象：hypatia-master/satviz/js/protocol31.js（无 DOM 依赖）。
 * 覆盖 rebuildPositions / expandLink / mergeLinks 的关键路径与边界。
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const Protocol31 = require('../../hypatia-master/satviz/js/protocol31.js');

const LINK_TYPES = {
    isl: { capacity_bps: 1.25e9 },
    gsl: { capacity_bps: 5e8 },
};
const QUEUE_PKTS = 64;

test('rebuildPositions: sat_pos 与 sat_order 对齐还原，高度取 init 值', () => {
    const payload = {
        sat_pos: [[10.5, 20.25], [11.0, 21.0]],
        positions: {},
    };
    const out = Protocol31.rebuildPositions(payload, ['Sat-0', 'Sat-1'], { 'Sat-0': 600000 });
    assert.deepEqual(out['Sat-0'], { lat: 10.5, lon: 20.25, alt: 600000 });
    // 缺省高度回退 550km
    assert.deepEqual(out['Sat-1'], { lat: 11.0, lon: 21.0, alt: 550000 });
});

test('rebuildPositions: sat_pos 超长时按 sat_order 截断，动态节点并入', () => {
    const payload = {
        sat_pos: [[1, 2], [3, 4], [5, 6]],
        positions: { 'UAV-0': { lat: 30, lon: 120, alt: 20000 } },
    };
    const out = Protocol31.rebuildPositions(payload, ['Sat-0', 'Sat-1'], {});
    assert.equal(Object.keys(out).length, 3); // 2 星 + 1 无人机，多余 sat_pos 被忽略
    assert.deepEqual(out['UAV-0'], { lat: 30, lon: 120, alt: 20000 });
});

test('rebuildPositions: 无 sat_pos / 空 order 时仅透传动态节点', () => {
    const out = Protocol31.rebuildPositions(
        { positions: { 'SHIP-0': { lat: 1, lon: 2, alt: 0 } } }, [], {});
    assert.equal(Object.keys(out).length, 1);
    assert.ok(out['SHIP-0']);
});

test('expandLink: 短键展开为长格式，键按 -- 拆分为端点', () => {
    const l = Protocol31.expandLink('Sat-0-1--Sat-0-2', { t: 'isl', u: 0.42, l: 12.5, d: 0.01, tx: 1e8, q: 3, p: 9.8 }, LINK_TYPES, QUEUE_PKTS);
    assert.equal(l.type, 'isl');
    assert.equal(l.source, 'Sat-0-1');
    assert.equal(l.target, 'Sat-0-2');
    assert.equal(l.is_active, true);
    assert.equal(l.bandwidth_utilization, 0.42);
    assert.equal(l.latency_ms, 12.5);
    assert.equal(l.loss_rate, 0.01);
    assert.equal(l.tx_bps, 1e8);
    assert.equal(l.capacity_bps, 1.25e9);
    assert.equal(l.queue_depth, 3);
    assert.equal(l.queue_capacity, QUEUE_PKTS * 2); // 无向链路聚合双端口
    assert.equal(l.propagation_ms, 9.8);
});

test('expandLink: 缺省字段回退 0，未知类型容量为 0', () => {
    const l = Protocol31.expandLink('A--B', { t: 'mystery' }, LINK_TYPES, QUEUE_PKTS);
    assert.equal(l.bandwidth_utilization, 0);
    assert.equal(l.capacity_bps, 0);
    assert.equal(l.source, 'A');
    assert.equal(l.target, 'B');
});

test('expandLink: 无 -- 的键（单端点）source=key target 为空', () => {
    const l = Protocol31.expandLink('GS-0', { t: 'gsl' }, LINK_TYPES, QUEUE_PKTS);
    assert.equal(l.source, 'GS-0');
    assert.equal(l.target, '');
});

test('mergeLinks: 增量合并 + links_removed 剪枝', () => {
    let cache = {};
    cache = Protocol31.mergeLinks(cache, {
        links: { 'A--B': { t: 'isl', u: 0.1 }, 'C--D': { t: 'gsl', u: 0.2 } },
    }, LINK_TYPES, QUEUE_PKTS);
    assert.equal(Object.keys(cache).length, 2);

    cache = Protocol31.mergeLinks(cache, {
        links: { 'E--F': { t: 'isl', u: 0.3 } },
        links_removed: ['A--B'],
    }, LINK_TYPES, QUEUE_PKTS);
    assert.equal(Object.keys(cache).length, 2);
    assert.ok(cache['C--D']);
    assert.ok(cache['E--F']);
    assert.equal(cache['A--B'], undefined);
});

test('mergeLinks: links_full 先整体重置再写入，不残留旧链路', () => {
    let cache = Protocol31.mergeLinks({}, {
        links: { 'OLD--1': { t: 'isl' }, 'OLD--2': { t: 'isl' } },
    }, LINK_TYPES, QUEUE_PKTS);
    assert.equal(Object.keys(cache).length, 2);

    const before = cache;
    cache = Protocol31.mergeLinks(cache, {
        links_full: true,
        links: { 'NEW--1': { t: 'isl' } },
    }, LINK_TYPES, QUEUE_PKTS);
    assert.equal(Object.keys(cache).length, 1);
    assert.ok(cache['NEW--1']);
    assert.notEqual(cache, before); // 全量帧返回新对象
});

test('mergeLinks: 空帧不改动缓存内容', () => {
    const cache = { 'A--B': { type: 'isl' } };
    const out = Protocol31.mergeLinks(cache, {}, LINK_TYPES, QUEUE_PKTS);
    assert.equal(out, cache);
    assert.equal(Object.keys(out).length, 1);
});

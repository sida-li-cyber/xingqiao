/**
 * 协议 3.1 紧凑帧解码纯函数。
 *
 * 从 app.js 提取，无 DOM / Cesium 依赖，可直接用 node:test 单测
 * （tests/frontend/protocol31.test.js）。app.js 中的同名方法只做委托。
 *
 * 帧格式回顾：
 *   sat_pos        —— [[lat, lon], ...]，与 sat_order 对齐，高度恒定
 *   positions      —— 动态节点（无人机 / 船舶）位置字典
 *   links          —— 短键增量 {t,u,l,d,tx,q,p}
 *   links_removed  —— 本帧消失的链路键列表
 *   links_full     —— 全量重同步标志（先清空客户端缓存）
 */
const Protocol31 = {
    /**
     * 由 sat_pos 数组 + 动态节点字典还原全量位置表。
     * @param {object} payload - state_update.payload
     * @param {string[]} satOrder - simulation_init 下发的卫星编号顺序
     * @param {object} satAltM - 卫星编号 -> 高度（米），缺省 550km
     */
    rebuildPositions(payload, satOrder, satAltM) {
        const positions = {};
        const sp = payload.sat_pos;
        if (sp && satOrder.length) {
            const n = Math.min(sp.length, satOrder.length);
            for (let i = 0; i < n; i++) {
                const id = satOrder[i];
                positions[id] = {
                    lat: sp[i][0],
                    lon: sp[i][1],
                    alt: (satAltM && satAltM[id]) || 550000,
                };
            }
        }
        if (payload.positions) {
            Object.assign(positions, payload.positions);
        }
        return positions;
    },

    /**
     * 将短键链路记录 {t,u,l,d,tx,q,p} 展开为 CesiumManager / UIController
     * 消费的长格式。
     * @param {string} key - 链路键，如 "Sat-0-1--Sat-0-2"
     * @param {object} v - 短键值对象
     * @param {object} linkTypes - 链路类型元数据（含 capacity_bps）
     * @param {number} queueCapacityPkts - 单端口队列容量（包数）
     */
    expandLink(key, v, linkTypes, queueCapacityPkts) {
        const dash = key.indexOf('--');
        const lt = (linkTypes && linkTypes[v.t]) || {};
        return {
            type: v.t,
            source: dash >= 0 ? key.slice(0, dash) : key,
            target: dash >= 0 ? key.slice(dash + 2) : '',
            is_active: true,
            bandwidth_utilization: v.u || 0,
            latency_ms: v.l || 0,
            loss_rate: v.d || 0,
            tx_bps: v.tx || 0,
            capacity_bps: lt.capacity_bps || 0,
            queue_depth: v.q || 0,
            // 每条无向链路聚合两个有向端口
            queue_capacity: queueCapacityPkts * 2,
            propagation_ms: v.p || 0,
        };
    },

    /**
     * 将一帧链路增量合并进客户端缓存，返回合并后的缓存对象。
     * links_full 为真时先整体重置（返回新对象）。
     */
    mergeLinks(cache, payload, linkTypes, queueCapacityPkts) {
        let out = cache;
        if (payload.links_full) {
            out = {};
        }
        const links = payload.links;
        if (links) {
            for (const key in links) {
                out[key] = Protocol31.expandLink(key, links[key], linkTypes, queueCapacityPkts);
            }
        }
        const removed = payload.links_removed;
        if (removed && removed.length) {
            for (const key of removed) {
                delete out[key];
            }
        }
        return out;
    },
};

// 双环境导出：浏览器挂 window，Node 单测走 module.exports
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Protocol31;
}
if (typeof window !== 'undefined') {
    window.Protocol31 = Protocol31;
}
